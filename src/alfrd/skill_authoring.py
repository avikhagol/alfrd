"""Validated, revision-guarded skill writes. Never executes skill instructions."""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import yaml
from yaml.tokens import (AliasToken, AnchorToken, TagToken, BlockMappingStartToken,
                         FlowMappingStartToken, BlockSequenceStartToken, FlowSequenceStartToken)

class SkillError(ValueError):
    def __init__(self, reason, message, status=400, **details):
        super().__init__(message)
        self.reason, self.status, self.details = reason, status, details


def validate_skill_document(content):
    from alfrd.skills import _FRONT, MAX_SKILL_BYTES
    if not isinstance(content, str):
        raise SkillError('invalid_manifest', 'Content must be UTF-8 text')
    try:
        data = content.encode('utf-8')
    except UnicodeError as exc:
        raise SkillError('invalid_manifest', 'Content must be UTF-8 text') from exc
    if len(data) > MAX_SKILL_BYTES:
        raise SkillError('manifest_too_large', 'SKILL.md exceeds 65,536 bytes', 413)
    match = _FRONT.match(content)
    if '\0' in content or not match or not content[match.end():].strip():
        raise SkillError('invalid_manifest', 'SKILL.md requires name and description in a closed leading YAML block and a nonempty Markdown body', line=1)
    front = match.group(1)
    if len(front.encode('utf-8')) > 8192:
        raise SkillError('invalid_manifest', 'Frontmatter exceeds 8 KiB', line=2)
    try:
        mappings = 0
        for token in yaml.scan(front):
            if isinstance(token, (BlockMappingStartToken, FlowMappingStartToken)):
                mappings += 1
            if mappings > 1 or isinstance(token, (BlockSequenceStartToken, FlowSequenceStartToken)):
                raise SkillError('invalid_manifest', 'Nested metadata collections are unsupported', line=token.start_mark.line + 2)
            if isinstance(token, (AliasToken, AnchorToken, TagToken)):
                raise SkillError('invalid_manifest', 'YAML anchors, aliases and tags are unsupported', line=token.start_mark.line + 2)
        node = yaml.compose(front, Loader=yaml.SafeLoader)
        if not isinstance(node, yaml.MappingNode):
            raise SkillError('invalid_manifest', 'Frontmatter must be a mapping', line=2)
        fields = {}
        for key, value in node.value:
            line = key.start_mark.line + 2
            if not isinstance(key, yaml.ScalarNode) or not isinstance(value, yaml.ScalarNode) or value.style in ('|', '>') or value.start_mark.line != value.end_mark.line:
                raise SkillError('invalid_manifest', 'Use one-line scalar metadata; block scalars and collections are unsupported', line=line)
            if key.value in fields or key.value == '<<':
                raise SkillError('invalid_manifest', 'Duplicate or merge metadata key', line=line)
            fields[key.value] = value.value if value.tag == 'tag:yaml.org,2002:str' or key.value not in ('name', 'description') else None
        for name, limit in [('name', 128), ('description', 2048)]:
            value = fields.get(name)
            if not isinstance(value, str) or not value.strip() or len(value.encode('utf-8')) > limit:
                raise SkillError('invalid_manifest', f'{name} must be a nonempty string of at most {limit} bytes', field=name, line=2)
        return fields
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        raise SkillError('invalid_manifest', 'Invalid YAML frontmatter', line=mark.line + 2 if mark else 2, column=mark.column + 1 if mark else 1) from exc


def validate_id(id):
    if not isinstance(id, str) or len(id) > 64 or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', id) or id.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}:
        raise SkillError('invalid_id', 'Use 1–64 lowercase letters, numbers and single hyphens; reserved device names are forbidden')


@contextlib.contextmanager
def directory(path, create=False):
    """Walk from / using no-follow descriptors, including every ancestor."""
    path = Path(path).absolute()
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        yield fd
    except OSError as exc:
        raise SkillError('filesystem_denied', 'Skill path is unavailable or contains a symlink', 403) from exc
    finally:
        os.close(fd)


@contextlib.contextmanager
def directory_at(parent, parts, create=False):
    fd = os.dup(parent)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = new
        yield fd
    finally:
        os.close(fd)


@contextlib.contextmanager
def write_lock(rootfd):
    # Same lock inode and flock protocol as runtime.plan_csv.locked / local add.
    import fcntl
    fd = os.open('.install.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=rootfd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise SkillError('filesystem_denied', 'Invalid library write lock', 403)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def publish(rootfd, stage, id):
    # Linux renameat2 gives a true no-replace publish, including empty directories.
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise SkillError('filesystem_denied', 'Atomic no-replace publication is unavailable on this platform', 403)
    if rename(rootfd, os.fsencode(stage), rootfd, os.fsencode(id), 1):
        import errno
        error = ctypes.get_errno()
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            raise SkillError('already_exists', 'Skill already exists; choose another ID', 409)
        raise OSError(error, 'Cannot publish skill')


def remove_tree(rootfd, name):
    with directory_at(rootfd, [name]) as fd:
        for child in os.listdir(fd):
            info = os.stat(child, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                remove_tree(fd, child)
            else:
                os.unlink(child, dir_fd=fd)
    os.rmdir(name, dir_fd=rootfd)


def write_policy(skill):
    shipped = Path(__file__).resolve().parents[2] / 'skills'
    path = Path(skill['path']).absolute()
    packaged = path.is_relative_to(shipped)
    kind = skill.get('source_kind', 'packaged' if packaged else 'library')
    editable = kind == 'library' and os.access(path, os.W_OK) and path.stat().st_uid == os.getuid() and path.parent.stat().st_uid == os.getuid() and not any(p.is_symlink() for p in (path,*path.parents))
    return dict(source_kind=kind, editable=editable, can_copy=True,
                read_only_reason='' if editable else 'Packaged/external or unwritable skill; copy it to customize')


def resolve(project, origin, id, global_root=None):
    from alfrd.skills import library
    if origin not in ('global', 'project') or not isinstance(id, str):
        raise SkillError('invalid_id', 'A skill origin and identity are required')
    for skill in library(project, global_root=global_root, content=True):
        if skill['origin'] == origin and skill['id'] == id:
            return {**skill, **write_policy(skill)}
    raise SkillError('unavailable', 'Skill is unavailable', 404)


def _read(fd):
    f = os.open('SKILL.md', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(f, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise SkillError('read_only', 'Manifest must be a regular file', 403)
        data = stream.read(65537)
    if len(data) > 65536:
        raise SkillError('manifest_too_large', 'SKILL.md exceeds 65,536 bytes', 413)
    return data, info


def _revision(skill, data, info):
    return hashlib.sha256(json.dumps([skill['origin'], skill['id'], skill['path'], hashlib.sha256(data).hexdigest(), info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size]).encode()).hexdigest()


def read_skill_document(project, id, origin='project', *, global_root=None):
    skill = resolve(project, origin, id, global_root)
    with directory(Path(skill['path']).parent) as fd:
        data, info = _read(fd)
    result = {**skill, 'content': data.decode('utf-8', errors='replace'), 'revision': _revision(skill, data, info)}
    try:
        files, provenance = read_bundle(skill)
        result['copy_revision'] = bundle_revision(skill, files)
    except SkillError as exc:
        result.update(can_copy=False, copy_reason=str(exc))
    return result


def save_skill_document(project, id, content, *, origin='project', base_revision, global_root=None):
    validate_skill_document(content)
    opened = resolve(project, origin, id, global_root)
    root = Path(opened['path']).parent.parent
    with directory(root) as rootfd:
        with write_lock(rootfd):
            try:
                skill = resolve(project, origin, id, global_root)
            except SkillError as exc:
                raise SkillError('changed', 'Skill changed since you opened it', 409) from exc
            if not skill['editable']:
                raise SkillError('read_only', skill['read_only_reason'], 403)
            with directory(Path(skill['path']).parent) as fd:
                data, info = _read(fd)
                if base_revision != _revision(skill, data, info):
                    raise SkillError('changed', 'Skill changed since you opened it. Reload the current version or copy your draft.', 409, current_content=data.decode('utf-8', errors='replace'))
                temp = '.manifest-' + secrets.token_hex(12)
                try:
                    f = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, info.st_mode & 0o666, dir_fd=fd)
                    with os.fdopen(f, 'wb') as stream:
                        stream.write(content.encode('utf-8')); stream.flush(); os.fsync(stream.fileno())
                    os.replace(temp, 'SKILL.md', src_dir_fd=fd, dst_dir_fd=fd)
                    os.fsync(fd)
                finally:
                    try:
                        os.unlink(temp, dir_fd=fd)
                    except FileNotFoundError:
                        pass
    return read_skill_document(project, id, origin, global_root=global_root)


def target_root(project, origin, global_root=None):
    from alfrd.skills import global_skills_root
    if origin not in ('global', 'project'):
        raise SkillError('invalid_id', 'Choose project or global scope')
    root = global_skills_root(global_root) if origin == 'global' else Path(project).resolve() / '.alfrd/skills'
    if root.is_relative_to(Path(__file__).resolve().parents[2] / 'skills'):
        raise SkillError('read_only', 'Packaged skills are read-only', 403)
    return root


def create_skill(project, id, content, *, origin='project', global_root=None, files=None, provenance=None):
    validate_id(id)
    return _publish_skill(project, id, content, origin=origin, global_root=global_root, files=files, provenance=provenance)


def _publish_skill(project, id, content, *, origin, global_root=None, files=None, provenance=None, modes=None):
    from alfrd.skills import SKILL_ROOTS
    validate_skill_document(content)
    files = dict(files or {}); files['SKILL.md'] = content.encode('utf-8')
    if len(files) > 1000 or sum(len(v) for v in files.values()) > 10*1024*1024:
        raise SkillError('bundle_too_large', 'Bundle exceeds 10 MiB or 1000 files', 413)
    for name, data in files.items():
        p = Path(name)
        if p.is_absolute() or any(x in ('..', '.') for x in p.parts) or '\\' in name or not p.parts or name == '.alfrd-source.json' or any(ord(c)<32 or ord(c)==127 for c in name):
            raise SkillError('unsafe_archive', 'Unsafe bundle path', 422)
        if not isinstance(data, bytes):
            raise SkillError('invalid_manifest', 'Bundle files must be bytes')
    root = target_root(project, origin, global_root)
    with directory(root, create=True) as rootfd:
        with write_lock(rootfd):
            candidates = [root / id] if origin == 'global' else [Path(project).resolve() / r / id for r in SKILL_ROOTS]
            if any(os.path.lexists(p) for p in candidates):
                raise SkillError('already_exists', f'{origin.title()} skill {id!r} already exists; open it or choose another ID', 409)
            stage = '.install-' + secrets.token_hex(12)
            os.mkdir(stage, 0o700, dir_fd=rootfd)
            try:
                stagepath = root / stage
                output = dict(files)
                if provenance is not None:
                    encoded = (json.dumps(provenance, sort_keys=True) + '\n').encode()
                    if len(encoded) > 16384:
                        raise SkillError('bundle_too_large', 'Provenance exceeds 16 KiB', 413)
                    output['.alfrd-source.json'] = encoded
                for name, data in output.items():
                    with directory_at(rootfd, [stage, *Path(name).parts[:-1]], create=True) as fd:
                        mode = (modes or {}).get(name, 0o600) & (0o666 if name == 'SKILL.md' else 0o777)
                        f = os.open(Path(name).name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
                        with os.fdopen(f, 'wb') as stream:
                            stream.write(data); stream.flush(); os.fsync(stream.fileno())
                        os.fsync(fd)
                # All ALFRD publishers share the lock. Never replace any existing entry.
                if os.path.lexists(root / id):
                    raise SkillError('already_exists', 'Skill already exists', 409)
                publish(rootfd, stage, id); os.fsync(rootfd)
            finally:
                try:
                    remove_tree(rootfd, stage)
                except FileNotFoundError:
                    pass
    return read_skill_document(project, id, origin, global_root=global_root)


def read_bundle(skill, *, modes=None):
    files, provenance, total = {}, None, 0
    root = Path(skill['path']).parent
    with directory(root) as rootfd:
        def walk(fd, prefix=''):
            nonlocal total, provenance
            for name in sorted(os.listdir(fd)):
                path = prefix+name
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if len(Path(path).parts) > 16:
                        raise SkillError('bundle_too_large', 'Bundle nesting exceeds 16 components', 413)
                    with directory_at(fd, [name]) as child:
                        walk(child, path+'/')
                elif stat.S_ISREG(info.st_mode):
                    if len(files) >= 1000 or total+info.st_size > 10*1024*1024:
                        raise SkillError('bundle_too_large', 'Bundle exceeds 10 MiB or 1000 files', 413)
                    f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    with os.fdopen(f, 'rb') as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            raise SkillError('read_only', 'Bundle file changed to a special file', 403)
                        if modes is not None:
                            modes[path] = os.fstat(stream.fileno()).st_mode & 0o777
                        data = stream.read(10*1024*1024-total+1)
                    total += len(data)
                    if total > 10*1024*1024:
                        raise SkillError('bundle_too_large', 'Bundle exceeds 10 MiB', 413)
                    if path == '.alfrd-source.json':
                        try:
                            if len(data) <= 16384:
                                value = json.loads(data)
                                if value.get('version') == 1 and isinstance(value.get('files'), list):
                                    provenance = value
                        except (ValueError, AttributeError):
                            pass
                    else:
                        files[path] = data
                else:
                    raise SkillError('read_only', 'Cannot copy bundles with symlinks, hardlinks or special files', 403)
        walk(rootfd)
    return files, provenance


def bundle_revision(skill, files):
    records = [(p,len(v),hashlib.sha256(v).hexdigest()) for p,v in sorted(files.items())]
    return hashlib.sha256(json.dumps([skill['origin'],skill['id'],skill['path'],records]).encode()).hexdigest()


def copy_skill(project, source_id, *, source_origin='project', id, target_origin='project', base_revision, global_root=None):
    source = read_skill_document(project, source_id, source_origin, global_root=global_root)
    modes = {}
    files, provenance = read_bundle(source, modes=modes)
    if source.get('copy_revision') != base_revision or bundle_revision(source, files) != base_revision:
        raise SkillError('changed', 'Source bundle changed; refresh before copying', 409)
    content = files.get('SKILL.md', b'').decode('utf-8')
    if provenance:
        provenance = {**provenance, 'copy_origin': {'origin':source_origin,'id':source_id}}
    validate_id(id)
    return _publish_skill(project, id, content, origin=target_origin, global_root=global_root, files=files, provenance=provenance, modes=modes)

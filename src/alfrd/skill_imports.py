"""Bounded public HTTPS imports: fetch, inspect, explicitly publish. No execution."""
from __future__ import annotations
import concurrent.futures
import hashlib
import gzip
import http.client
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import socket
import ssl
import stat
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urljoin, unquote, quote, parse_qsl
import zipfile
from alfrd.skill_authoring import SkillError, validate_skill_document, create_skill, validate_id

LIMIT = 10*1024*1024
_ALLOWED = {'.md', '.txt', '.rst', '.json', '.yaml', '.yml', '.toml', '.csv', '.tsv'}
_DNS = concurrent.futures.ThreadPoolExecutor(max_workers=2)
_SLOTS = threading.BoundedSemaphore(2)
_STORE_LOCK = threading.RLock()
# Private, per-process store. CLI persists a preview via its own process lifetime (interactive/--yes).
_STORE = {}


def validate_url(url):
    if not isinstance(url, str) or len(url.encode()) > 4096 or any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise SkillError('unsafe_url', 'Invalid HTTPS URL', 422)
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or not p.hostname or p.username is not None or p.password is not None or p.fragment or p.netloc.endswith(':') or p.port not in (None, 443) or '%' in p.netloc or '\\' in url:
            raise ValueError()
        if any(k.lower() in {'token','access_token','auth','authorization','password','api_key','apikey'} for k,_ in parse_qsl(p.query)):
            raise ValueError()
        host = p.hostname.encode('idna').decode('ascii')
        if host.endswith('.') or ('.' not in host and ':' not in host) or host.lower().endswith(('.local', '.localhost', '.internal')) or not re.fullmatch(r'[a-zA-Z0-9.:-]+', host):
            raise ValueError()
        # Numeric-looking alternatives such as 127.1 / 0x7f000001 are never hostnames.
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            if re.fullmatch(r'[0-9.]+', host) or any(x.lower().startswith('0x') for x in host.split('.')):
                raise ValueError()
        else:
            if not public_ip(addr):
                raise ValueError()
    except (ValueError, UnicodeError):
        raise SkillError('unsafe_url', 'Use a public HTTPS URL on port 443 without credentials or fragments', 422) from None
    return p


def public_ip(value):
    ip = ipaddress.ip_address(value)
    if getattr(ip, 'ipv4_mapped', None):
        return public_ip(ip.ipv4_mapped)
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)


class Budget:
    def __init__(self):
        self.deadline, self.bytes, self.requests, self.redirects = time.monotonic()+30, 0, 0, 0
        self.payloads = hashlib.sha256()

    def remaining(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise SkillError('fetch_timeout', 'Import exceeded its 30 second deadline', 504)
        return left

    def count(self, data):
        self.remaining(); self.bytes += len(data); self.payloads.update(data)
        if self.bytes > LIMIT:
            raise SkillError('download_too_large', 'Import download exceeds 10 MiB', 413)


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, budget):
        super().__init__(host, 443, timeout=min(5, budget.remaining()), context=ssl.create_default_context())
        self.address, self.budget = address, budget

    def connect(self):
        # Connect to the validated IP, while retaining hostname verification and SNI.
        raw = socket.socket(socket.AF_INET6 if ipaddress.ip_address(self.address).version == 6 else socket.AF_INET, socket.SOCK_STREAM)
        try:
            raw.settimeout(min(5, self.budget.remaining()))
            raw.connect((self.address, 443))
            if ipaddress.ip_address(raw.getpeername()[0]) != ipaddress.ip_address(self.address):
                raise SkillError('unsafe_url', 'Connection peer did not match validated address', 422)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
            self.sock.settimeout(min(10, self.budget.remaining()))
        except BaseException:
            raw.close(); raise


def fetch(url, budget):
    """Manual redirects, no proxies/cookies/netrc; all DNS answers must be public."""
    while True:
        p = validate_url(url); budget.remaining(); budget.requests += 1
        if budget.requests > 8:
            raise SkillError('fetch_failed', 'Import exceeds eight outbound requests', 502)
        future = _DNS.submit(socket.getaddrinfo, p.hostname, 443, type=socket.SOCK_STREAM)
        try:
            addresses = future.result(timeout=min(5, budget.remaining()))
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise SkillError('fetch_timeout', 'DNS resolution timed out', 504) from None
        except OSError:
            raise SkillError('fetch_failed', 'Cannot resolve import host', 502) from None
        ips = list(dict.fromkeys(a[4][0] for a in addresses))
        if not ips or not all(public_ip(ip) for ip in ips):
            raise SkillError('unsafe_url', 'URL resolves to a blocked network address', 422)
        conn = PinnedHTTPS(p.hostname, ips[0], budget)
        active_socket = None
        def abort():
            sock = active_socket or conn.sock
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            conn.close()
        timer = threading.Timer(budget.remaining(), abort)
        timer.daemon = True; timer.start()
        try:
            conn.request('GET', (p.path or '/') + ('?' + p.query if p.query else ''), headers={'Accept-Encoding': 'identity', 'User-Agent': 'ALFRD-skill-import/1', 'Accept': 'application/vnd.github+json, text/plain, application/octet-stream'})
            active_socket = conn.sock
            response = conn.getresponse()
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise SkillError('fetch_failed', 'Compressed HTTP encodings are unsupported', 502)
            length = response.getheader('Content-Length')
            if length and (not length.isdecimal() or int(length) > LIMIT-budget.bytes):
                raise SkillError('download_too_large', 'Import download exceeds 10 MiB', 413)
            chunks = []
            while True:
                if conn.sock:
                    conn.sock.settimeout(min(10, budget.remaining()))
                chunk = response.read1(min(65536, LIMIT-budget.bytes+1))
                if not chunk:
                    break
                budget.count(chunk); chunks.append(chunk)
            if response.status in (301,302,303,307,308):
                budget.redirects += 1
                if budget.redirects > 3 or not response.getheader('Location'):
                    raise SkillError('fetch_failed', 'Import exceeds three redirects', 502)
                url = urljoin(url, response.getheader('Location')); continue
            if not 200 <= response.status < 300:
                raise SkillError('fetch_failed', f'Upstream returned HTTP {response.status}', 502)
            return b''.join(chunks), url, response.getheader('Content-Type', '')
        except (TimeoutError, socket.timeout):
            raise SkillError('fetch_timeout', 'Import request timed out', 504) from None
        except (OSError, http.client.HTTPException):
            budget.remaining()
            raise SkillError('fetch_failed', 'Could not fetch skill over verified HTTPS', 502) from None
        finally:
            timer.cancel(); conn.close()


def github_source(url, budget, transport=fetch):
    """Resolve every possible ref/path split; ambiguity fails closed."""
    p = validate_url(url)
    if p.hostname != 'github.com':
        return url, None, None
    parts = unquote(p.path).strip('/').split('/')
    if len(parts) < 5 or parts[2] not in ('blob', 'tree') or any(not x or x in ('.', '..') or '\\' in x for x in parts):
        raise SkillError('unsafe_url', 'Use a GitHub blob or tree URL with explicit ref and path', 422)
    owner, repo, kind = parts[:3]
    if not all(re.fullmatch(r'[A-Za-z0-9_.-]+', s) and s not in ('.','..') for s in (owner,repo)):
        raise SkillError('unsafe_url', 'Invalid GitHub owner or repository', 422)
    tail = parts[3:]; candidates = []
    for split in range(1, len(tail)):
        budget.remaining()
        if split > 5:
            raise SkillError('unsafe_url', 'Ambiguous GitHub URL; use an explicit commit SHA and path', 422)
        ref = '/'.join(tail[:split]); path = '/'.join(tail[split:])
        try:
            data, _, _ = transport(f'https://api.github.com/repos/{owner}/{repo}/commits/{quote(ref, safe="")}', budget)
        except SkillError as exc:
            if exc.reason == 'fetch_failed' and '404' in str(exc):
                continue
            raise
        try:
            sha = json.loads(data)['sha']
        except (ValueError, KeyError, TypeError):
            raise SkillError('fetch_failed', 'Invalid GitHub commit response', 502) from None
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
            raise SkillError('fetch_failed', 'Invalid GitHub commit SHA', 502)
        # A commit SHA is an unambiguous boundary; branch names with slashes are probed.
        candidates.append((sha, path))
        if re.fullmatch('[0-9a-f]{40}', ref):
            break
    if len(candidates) != 1:
        raise SkillError('unsafe_url', 'Ambiguous GitHub ref/path; use an explicit commit SHA and path', 422)
    sha, path = candidates[0]
    if kind == 'blob':
        if not path.endswith('SKILL.md'):
            raise SkillError('unsafe_url', 'GitHub blob must be a SKILL.md', 422)
        return f'https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{quote(path, safe="/")}', sha, None
    return f'https://codeload.github.com/{owner}/{repo}/tar.gz/{sha}', sha, path


def member_path(name):
    if not isinstance(name, str) or len(name.encode('utf-8')) > 240 or '\\' in name or any(ord(c)<32 or ord(c)==127 for c in name):
        raise SkillError('unsafe_archive', 'Archive contains an unsafe path', 422)
    parts = name.rstrip('/').split('/')
    if len(parts) > 16 or any(x in ('', '.', '..') or ':' in x for x in parts) or PurePosixPath(name).is_absolute():
        raise SkillError('unsafe_archive', f'Archive entry {name!r} is not allowed', 422)
    return '/'.join(parts)


def _check_zip_directory(data):
    # Bound central-directory work before ZipFile allocates a ZipInfo per entry.
    end = data.rfind(b'PK\x05\x06', max(0, len(data)-65557))
    if end < 0 or end+22 > len(data):
        raise SkillError('unsafe_archive', 'Malformed ZIP directory', 422)
    if int.from_bytes(data[end+4:end+8], 'little'):
        raise SkillError('unsupported_archive', 'Multipart ZIP archives are unsupported', 415)
    count = int.from_bytes(data[end+10:end+12], 'little')
    size = int.from_bytes(data[end+12:end+16], 'little')
    offset = int.from_bytes(data[end+16:end+20], 'little')
    if count > 2000:
        raise SkillError('too_many_files', 'ZIP exceeds 2000 entries (ZIP64 is unsupported)', 413)
    if offset+size != end or offset > len(data):
        raise SkillError('unsafe_archive', 'Invalid ZIP directory bounds', 422)
    entries = 0
    while offset < end:
        if offset+46 > end or data[offset:offset+4] != b'PK\x01\x02':
            raise SkillError('unsafe_archive', 'Malformed ZIP directory entry', 422)
        entries += 1
        if entries > 2000:
            raise SkillError('too_many_files', 'ZIP exceeds 2000 entries', 413)
        lengths = [int.from_bytes(data[offset+i:offset+i+2], 'little') for i in (28,30,32)]
        if lengths[0] > 240:
            raise SkillError('unsafe_archive', 'Archive path exceeds 240 bytes', 422)
        offset += 46+sum(lengths)
    if offset != end or entries != count:
        raise SkillError('unsafe_archive', 'ZIP directory entry count is inconsistent', 422)


def _bounded_tar(data, budget):
    chunks, total = [], 0
    # File data plus at most 2,000 ordinary header/padding pairs. This also bounds
    # hidden PAX/GNU metadata that tarfile consumes before yielding a member.
    limit = LIMIT+2000*1024
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        while True:
            if budget:
                budget.remaining()
            chunk = stream.read(min(65536, limit-total+1))
            if not chunk:
                return b''.join(chunks)
            total += len(chunk)
            if total > limit:
                raise SkillError('bundle_too_large', 'Expanded tar stream exceeds archive limits', 413)
            chunks.append(chunk)


def archive_files(data, *, subtree=None, budget=None):
    """Validate every entry before selecting a subtree; never extract to the filesystem."""
    files, seen, total, entries, directories = {}, set(), 0, 0, []
    def accept(name, size, stream, isdir=False):
        nonlocal total, entries
        if budget:
            budget.remaining()
        entries += 1
        if entries > 2000:
            raise SkillError('too_many_files', 'Archive exceeds 2000 entries', 413)
        name = member_path(name)
        if name.casefold() in seen:
            raise SkillError('unsafe_archive', 'Archive has duplicate or case-colliding paths', 422)
        seen.add(name.casefold())
        if isdir:
            directories.append(name)
            return
        if len(files) >= 1000 or size > (65536 if name.endswith('/SKILL.md') or name == 'SKILL.md' else 1024*1024) or total+size > LIMIT:
            raise SkillError('bundle_too_large', 'Archive exceeds file or expanded-size limits', 413)
        chunks, actual = [], 0
        while True:
            if budget:
                budget.remaining()
            chunk = stream.read(min(65536, 1024*1024-actual+1))
            if not chunk:
                break
            actual += len(chunk); total += len(chunk)
            if actual > size or actual > 1024*1024 or total > LIMIT:
                raise SkillError('bundle_too_large', 'Archive exceeds expanded-size limits', 413)
            chunks.append(chunk)
        if actual != size:
            raise SkillError('unsafe_archive', 'Truncated archive entry', 422)
        files[name] = b''.join(chunks)
    try:
        if zipfile.is_zipfile(io.BytesIO(data)):
            _check_zip_directory(data)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for item in archive.infolist():
                    mode = item.external_attr >> 16
                    if item.flag_bits & 1 or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                        raise SkillError('unsafe_archive', 'Encrypted, linked or unsupported ZIP entry', 422)
                    with archive.open(item) as stream:
                        accept(item.filename, item.file_size, stream, item.is_dir())
        else:
            expanded = _bounded_tar(data, budget)
            with tarfile.open(fileobj=io.BytesIO(expanded), mode='r:') as archive:
                for item in archive:
                    if not (item.isdir() or item.isfile()) or item.issparse() or item.linkname:
                        raise SkillError('unsafe_archive', 'Linked, sparse or special archive entry', 422)
                    stream = archive.extractfile(item) if item.isfile() else io.BytesIO()
                    with stream:
                        accept(item.name, item.size, stream, item.isdir())
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, OSError, RuntimeError, UnicodeError):
        raise SkillError('unsafe_archive', 'Malformed or unsupported skill archive', 422) from None
    candidates = [p for p in files if p == 'SKILL.md' or p.endswith('/SKILL.md')]
    if subtree is not None:
        roots = {p.split('/')[0] for p in files}
        if len(roots) != 1:
            raise SkillError('unsafe_archive', 'Invalid GitHub archive wrapper', 422)
        prefix = next(iter(roots)) + '/' + subtree.strip('/') + '/'
        if prefix+'SKILL.md' not in files:
            raise SkillError('invalid_manifest', 'GitHub folder has no SKILL.md')
    else:
        if len(candidates) != 1 or candidates[0].count('/') > 1:
            raise SkillError('unsafe_archive', 'Archive must contain exactly one skill at root or in one wrapper directory', 422)
        prefix = candidates[0][:-len('SKILL.md')]
        if prefix and (any(not name.startswith(prefix) for name in files) or any(name != prefix.rstrip('/') and not name.startswith(prefix) for name in directories)):
            raise SkillError('unsafe_archive', 'Archive must have one common skill wrapper directory', 422)
    for name in directories:
        if name.startswith(prefix) and any(x.startswith('.') for x in name[len(prefix):].split('/') if x):
            raise SkillError('unsupported_file', 'Hidden imported paths are unsupported', 415)
    selected = {p[len(prefix):]: v for p,v in files.items() if p.startswith(prefix)}
    for name, data in selected.items():
        if any(x.startswith('.') for x in name.split('/')) or (name != 'SKILL.md' and PurePosixPath(name).suffix.lower() not in _ALLOWED):
            raise SkillError('unsupported_file', f'Unsupported imported file {name!r}; URL imports accept text references only', 415)
        text(data)
    return selected


def text(data):
    try:
        value = data.decode('utf-8')
        if '\0' in value:
            raise UnicodeError()
        return value
    except UnicodeError:
        raise SkillError('unsupported_file', 'Imported files must be UTF-8 text without NUL bytes', 415) from None


def records(files):
    return [{'path': p, 'bytes': len(v), 'sha256': hashlib.sha256(v).hexdigest()} for p,v in sorted(files.items())]


def bundle_digest(files):
    return hashlib.sha256(json.dumps(records(files), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _cleanup():
    now = time.time()
    for token in list(_STORE):
        if _STORE[token]['expires'] <= now:
            del _STORE[token]


def prepare_skill_import(project, url, *, owner='python', global_root=None, transport=None):
    if not _SLOTS.acquire(blocking=False):
        raise SkillError('import_busy', 'Two imports are already fetching; retry shortly', 429)
    try:
        with _STORE_LOCK:
            _cleanup()
            if sum(s['owner'] == owner and 'receipt' not in s for s in _STORE.values()) >= 3:
                raise SkillError('import_busy', 'Discard a pending preview before fetching another', 429)
        budget = Budget(); transport = transport or fetch
        resolved, commit, subtree = github_source(url, budget, transport)
        data, final, content_type = transport(resolved, budget)
        budget.remaining()
        if len(data) > LIMIT:
            raise SkillError('download_too_large', 'Import download exceeds 10 MiB', 413)
        isarchive = (subtree is not None or any(urlsplit(u).path.lower().endswith(('.zip','.tar.gz','.tgz')) for u in (url,resolved,final))
                     or data.startswith((b'PK\x03\x04', b'\x1f\x8b')))
        if isarchive:
            files = archive_files(data, subtree=subtree, budget=budget)
        else:
            manifest = text(data)
            if 'text/html' in content_type.lower() or manifest.lstrip().lower().startswith(('<!doctype html', '<html')):
                raise SkillError('invalid_manifest', 'Expected raw SKILL.md text; use a raw link or a GitHub skill folder URL')
            files = {'SKILL.md': data}
        meta = validate_skill_document(text(files['SKILL.md']))
        digest = bundle_digest(files)
        source = dict(version=1, source_url=url, resolved_url=final, fetched_at=datetime.now(timezone.utc).isoformat(), commit=commit, subpath=subtree,
                      payload_sha256=hashlib.sha256(data).hexdigest(), manifest_sha256=hashlib.sha256(files['SKILL.md']).hexdigest(), files=records(files), bundle_sha256=digest)
        if len(json.dumps(source).encode()) > 16384:
            raise SkillError('bundle_too_large', 'Provenance exceeds 16 KiB; use fewer reference files', 413)
        budget.remaining()
        token = secrets.token_urlsafe(32); expires = time.time()+600
        suggested = re.sub('[^a-z0-9]+', '-', meta['name'].lower()).strip('-')[:64].rstrip('-') or 'imported-skill'
        with _STORE_LOCK:
            _cleanup()
            if sum(s['owner']==owner and 'receipt' not in s for s in _STORE.values()) >= 3:
                raise SkillError('import_busy', 'Too many pending previews', 429)
            _STORE[token] = dict(owner=owner, project=str(Path(project).resolve()), expires=expires, files=files, source=source, digest=digest)
        return dict(token=token, expires=expires, content=text(files['SKILL.md']), files=records(files), source=source, digest=digest, proposed_id=suggested, total_bytes=sum(map(len,files.values())))
    finally:
        _SLOTS.release()


def commit_skill_import(project, token, origin, id, *, owner='python', global_root=None):
    validate_id(id)
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise SkillError('preview_expired', 'Preview expired or unavailable; fetch again', 410)
    with _STORE_LOCK:
        _cleanup(); stored = _STORE.get(token)
        if not stored or stored['owner'] != owner or stored['project'] != str(Path(project).resolve()):
            raise SkillError('preview_expired', 'Preview expired or unavailable; fetch again', 410)
        if 'receipt' in stored:
            if stored['arguments'] != (origin,id):
                raise SkillError('already_exists', 'Preview already saved with a different target', 409)
            return stored['receipt']
        if bundle_digest(stored['files']) != stored['digest']:
            raise SkillError('changed', 'Preview data changed; fetch again', 409)
        result = create_skill(project, id, text(stored['files']['SKILL.md']), origin=origin, global_root=global_root, files=stored['files'], provenance=stored['source'])
        stored.update(receipt=result, arguments=(origin,id)); stored.pop('files')
        return result


def discard_skill_import(project, token, *, owner='python'):
    with _STORE_LOCK:
        stored = _STORE.get(token)
        if stored and stored['owner']==owner and stored['project']==str(Path(project).resolve()):
            del _STORE[token]
    return {'discarded': True}


def _cli_store():
    # Separate from Studio's restart-cleared in-memory previews, owned by this OS user.
    from alfrd.skill_authoring import directory
    root = Path(tempfile.gettempdir()) / f'alfrd-skill-previews-{os.getuid()}'
    with directory(root, create=True) as fd:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise SkillError('filesystem_denied', 'Unsafe CLI preview directory', 403)
    return root


def _check_cli_quota(fd, token, owner, enforce=True):
    pending = 0
    for name in os.listdir(fd):
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', name):
            continue
        f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            with os.fdopen(f, 'r', encoding='utf-8') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 15*1024*1024:
                    raise SkillError('filesystem_denied', 'Unsafe CLI preview file', 403)
                value = json.load(stream)
            if value.get('expires', 0) <= time.time():
                os.unlink(name, dir_fd=fd)
            elif name != token and value.get('owner') == owner and 'receipt' not in value:
                pending += 1
        except (ValueError, UnicodeError):
            os.unlink(name, dir_fd=fd)
    if enforce and pending >= 3:
        raise SkillError('import_busy', 'Discard or confirm a pending preview before fetching another', 429)


def persist_cli_preview(token, *, owner):
    import base64
    from alfrd.skill_authoring import directory
    with _STORE_LOCK:
        stored = _STORE.get(token)
        if not stored or stored['owner'] != owner or not owner.startswith('cli:'):
            raise SkillError('preview_expired', 'Preview unavailable', 410)
        value = dict(stored)
        if 'files' in value:
            value['files'] = {p: base64.b64encode(v).decode('ascii') for p,v in value['files'].items()}
        with directory(_cli_store()) as fd:
            from alfrd.skill_authoring import write_lock
            with write_lock(fd):
                _check_cli_quota(fd, token, owner, enforce='receipt' not in stored)
                temp = '.tmp-' + secrets.token_hex(12)
                f = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                try:
                    with os.fdopen(f, 'w', encoding='utf-8') as stream:
                        json.dump(value, stream); stream.flush(); os.fsync(stream.fileno())
                    os.replace(temp, token, src_dir_fd=fd, dst_dir_fd=fd); os.fsync(fd)
                finally:
                    try:
                        os.unlink(temp, dir_fd=fd)
                    except FileNotFoundError:
                        pass


def restore_cli_preview(project, token, *, owner):
    import base64
    from alfrd.skill_authoring import directory
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise SkillError('preview_expired', 'Invalid preview token', 410)
    try:
        with directory(_cli_store()) as fd:
            f = os.open(token, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(f, 'r', encoding='utf-8') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 15*1024*1024:
                    raise ValueError()
                value = json.load(stream)
        if value['expires'] <= time.time() or value['owner'] != owner or value['project'] != str(Path(project).resolve()):
            raise ValueError()
        if 'files' in value:
            value['files'] = {p: base64.b64decode(v, validate=True) for p,v in value['files'].items()}
        if 'arguments' in value:
            value['arguments'] = tuple(value['arguments'])
        with _STORE_LOCK:
            _STORE[token] = value
    except (OSError, ValueError, KeyError, TypeError):
        raise SkillError('preview_expired', 'Preview expired or unavailable; fetch again', 410) from None

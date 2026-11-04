import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from alfrd import skills
from alfrd.skill_authoring import SkillError, validate_id

DOC = '---\n# keep comment\nname: "Review \\"diffs\\""\ndescription: "Review changes"\ncustom: original\n---\n# Instructions\n\nRead the diff.\n'

@pytest.mark.parametrize('origin', ['project','global'])
def test_create_edit_preserves_metadata_references_and_activation(tmp_path, origin):
    item = skills.create_skill(tmp_path, 'review', DOC, origin=origin)
    path = Path(item['path']); (path.parent/'guide.txt').write_text('reference')
    updated = skills.save_skill_document(tmp_path, 'review', DOC.replace('Read the diff.', 'Read all diffs.'), origin=origin, base_revision=item['revision'])
    assert updated['name'] == 'Review "diffs"'
    assert '# keep comment' in updated['content'] and 'custom: original' in updated['content']
    assert (path.parent/'guide.txt').read_text() == 'reference'
    assert not (tmp_path/'.alfrd/skills.json').exists()
    assert not path.stat().st_mode & 0o111
    assert not list(path.parent.glob('.manifest-*'))

@pytest.mark.parametrize('id', ['../a','a/b','a.b','A','has space','nul','com1','a--b','-a','x'*65,''])
def test_invalid_new_ids(id):
    with pytest.raises(SkillError, match='lowercase'):
        validate_id(id)

@pytest.mark.parametrize('content', [
    '', 'name: a', '---\nname: a\ndescription: b\n---\n',
    '---\nname: a\nname: b\ndescription: c\n---\nbody',
    '---\nname: &a text\ndescription: *a\n---\nbody',
    '---\nname: [text]\ndescription: b\n---\nbody',
    '---\nname: true\ndescription: b\n---\nbody',
    '---\nname: text\ndescription: |\n  block\n---\nbody',
    '---\nname: !custom text\ndescription: b\n---\nbody',
    '---\nname: text\ndescription: b\n---\nbody\0',
    '---\nname: "'+ 'é'*65 +'"\ndescription: b\n---\nbody',
])
def test_manifest_validation(content):
    with pytest.raises(SkillError):
        skills.validate_skill_document(content)


def test_byte_bounds_and_bom():
    prefix='\ufeff---\nname: a\ndescription: b\n---\n'
    assert skills.validate_skill_document(prefix+'x'*(65536-len(prefix.encode())))['name']=='a'
    with pytest.raises(SkillError) as error:
        skills.validate_skill_document(prefix+'x'*65536)
    assert error.value.status==413


def test_same_size_same_mtime_content_conflict_and_replacement(tmp_path):
    item=skills.create_skill(tmp_path,'review',DOC)
    path=Path(item['path']); info=path.stat()
    path.write_text(DOC.replace('original','modified'))
    os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns))
    with pytest.raises(SkillError) as error:
        skills.save_skill_document(tmp_path,'review',DOC,base_revision=item['revision'])
    assert error.value.reason=='changed' and error.value.status==409
    assert 'modified' in path.read_text()
    current=skills.read_skill_document(tmp_path,'review'); data=path.read_bytes()
    path.unlink();path.write_bytes(data)
    # A replacement can reuse an inode: explicit mtime change still binds the revision.
    os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns+1))
    with pytest.raises(SkillError):
        skills.save_skill_document(tmp_path,'review',DOC,base_revision=current['revision'])

@pytest.mark.parametrize('root', skills.SKILL_ROOTS)
def test_all_project_roots_editable_legacy_identity_and_collision(tmp_path, root):
    path=tmp_path/root/'Legacy_ID';path.mkdir(parents=True);(path/'SKILL.md').write_text(DOC)
    item=skills.read_skill_document(tmp_path,'Legacy_ID')
    skills.save_skill_document(tmp_path,'Legacy_ID',DOC+'\nnew',base_revision=item['revision'])
    path2=tmp_path/root/'review';path2.mkdir();(path2/'SKILL.md').write_text(DOC)
    with pytest.raises(SkillError) as error:
        skills.create_skill(tmp_path,'review',DOC)
    assert error.value.reason=='already_exists'


def test_symlinks_and_dangling_collision(tmp_path):
    outside=tmp_path/'outside';outside.mkdir()
    (tmp_path/'.alfrd').symlink_to(outside,target_is_directory=True)
    with pytest.raises(SkillError):
        skills.create_skill(tmp_path,'review',DOC)
    assert not list(outside.iterdir())
    (tmp_path/'.alfrd').unlink(); item=skills.create_skill(tmp_path,'review',DOC)
    path=Path(item['path']);path.unlink();path.symlink_to(outside/'manifest')
    with pytest.raises(SkillError):
        skills.save_skill_document(tmp_path,'review',DOC,base_revision=item['revision'])
    (tmp_path/'.alfrd/skills/collision').symlink_to(outside/'missing')
    with pytest.raises(SkillError) as error:
        skills.create_skill(tmp_path,'collision',DOC)
    assert error.value.reason=='already_exists'


def test_failed_replace_preserves_original(tmp_path, monkeypatch):
    item=skills.create_skill(tmp_path,'review',DOC)
    def fail(*args,**kwargs):
        raise OSError('fixture write failure')
    monkeypatch.setattr(os,'replace',fail)
    with pytest.raises(SkillError):
        skills.save_skill_document(tmp_path,'review',DOC+'new',base_revision=item['revision'])
    assert Path(item['path']).read_text()==DOC
    assert not list(Path(item['path']).parent.glob('.manifest-*'))


def test_parallel_publish_never_replaces(tmp_path):
    def attempt(_):
        try:
            return skills.create_skill(tmp_path,'review',DOC)['id']
        except SkillError as exc:
            return exc.reason
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt,range(2)))==['already_exists','review']
    root=tmp_path/'.alfrd/skills/.install-hidden';root.mkdir();(root/'SKILL.md').write_text(DOC)
    assert [s['id'] for s in skills.library(tmp_path)]==['review']


def test_copy_and_readonly_policy(tmp_path, monkeypatch):
    from alfrd import skill_authoring as author
    source=skills.create_skill(tmp_path,'review',DOC)
    original=author.write_policy
    monkeypatch.setattr(author,'write_policy',lambda s:{**original(s),'editable':False,'source_kind':'external','read_only_reason':'External'})
    with pytest.raises(SkillError) as error:
        skills.save_skill_document(tmp_path,'review',DOC,base_revision=source['revision'])
    assert error.value.reason=='read_only'
    result=skills.copy_skill(tmp_path,'review',id='review-copy',base_revision=source['copy_revision'])
    assert Path(result['path']).read_text()==DOC


def test_copy_revision_binds_references_and_preserves_provenance(tmp_path):
    item=skills.create_skill(tmp_path,'review',DOC)
    path=Path(item['path']);(path.parent/'ref.md').write_text('first')
    opened=skills.read_skill_document(tmp_path,'review')
    (path.parent/'ref.md').write_text('changed')
    with pytest.raises(SkillError) as error:
        skills.copy_skill(tmp_path,'review',id='copy',base_revision=opened['copy_revision'])
    assert error.value.reason=='changed'


def test_packaged_policy():
    from alfrd.skill_authoring import write_policy
    path=Path(skills.__file__).resolve().parents[2]/'skills/alfrd/SKILL.md'
    if path.exists():
        policy=write_policy({'path':str(path)})
        assert not policy['editable'] and policy['source_kind']=='packaged'


def test_lock_symlink_refused(tmp_path):
    item=skills.create_skill(tmp_path,'review',DOC)
    lock=Path(item['path']).parent.parent/'.install.lock';lock.unlink();target=tmp_path/'outside';target.write_text('untouched');lock.symlink_to(target)
    with pytest.raises(SkillError):
        skills.save_skill_document(tmp_path,'review',DOC+'x',base_revision=item['revision'])
    assert target.read_text()=='untouched'


def test_bounded_yaml_rejects_deep_collections_before_composition():
    content='---\nname: '+('['*2000)+'x'+(']'*2000)+'\ndescription: details\n---\nbody'
    with pytest.raises(SkillError) as error:
        skills.validate_skill_document(content)
    assert error.value.reason=='invalid_manifest'
    numeric='---\nname: safe\ndescription: details\nother: '+('9'*5000)+'\n---\nbody'
    assert skills.validate_skill_document(numeric)['name']=='safe'

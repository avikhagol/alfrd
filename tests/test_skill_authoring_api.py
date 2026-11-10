from pathlib import Path
import pytest
from tests.test_skills import skills_web, project  # noqa: F401
from alfrd import skill_imports
from tests.test_skill_imports import DOC, raw


def test_create_edit_validate_conflict_and_scopes(skills_web):
    client,base,root=skills_web;base+='/skills'
    client.environ_base['HTTP_X_CSRF_TOKEN']=client.get('/api/studio/session').json['csrf_token']
    payload={'origin':'project','id':'review','content':DOC.decode()}
    response=client.post(base+'/create',json=payload)
    assert response.status_code==201
    item=response.json
    assert Path(item['path']).is_relative_to(root)
    assert not (root/'.alfrd/skills.json').exists()
    assert client.post(base+'/create',json=payload).status_code==409
    opened=client.get(base+'/preview?id=review&origin=project').json
    saved=client.put(base+'/content',json={**payload,'content':DOC.decode()+'new','base_revision':opened['revision']})
    assert saved.status_code==200
    stale=client.put(base+'/content',json={**payload,'base_revision':opened['revision']})
    assert stale.status_code==409 and stale.json['error']['reason']=='changed'
    assert client.post(base+'/validate',json={'content':'bad'}).status_code==400
    assert client.post(base+'/create',json={**payload,'origin':'global'}).status_code==201
    assert client.post(base+'/create',json={**payload,'path':'/tmp/escape'}).status_code==400
    assert client.post(base+'/create',data='x'*524289,content_type='application/json').status_code==413


def test_import_preview_auth_scope_confirm_and_discard(skills_web,monkeypatch):
    client,base,root=skills_web;base+='/skills'
    client.environ_base['HTTP_X_CSRF_TOKEN']=client.get('/api/studio/session').json['csrf_token']
    monkeypatch.setattr(skill_imports,'fetch',raw)
    response=client.post(base+'/imports/preview',json={'url':'https://example.org/SKILL.md'})
    assert response.status_code==200
    assert not (root/'.alfrd/skills').exists()
    token=response.json['token']
    result=client.post(base+'/imports/confirm',json={'token':token,'id':'review','origin':'project'})
    assert result.status_code==201
    assert not (root/'.alfrd/skills.json').exists()
    assert client.post(base+'/imports/confirm',json={'token':token,'id':'review','origin':'project'}).json==result.json
    pending=client.post(base+'/imports/preview',json={'url':'https://example.org/SKILL.md'}).json
    assert client.delete(base+'/imports/'+pending['token']).status_code==200
    assert client.post(base+'/imports/confirm',json={'token':pending['token'],'id':'other','origin':'global'}).status_code==410
    assert client.post(base+'/imports/preview',json={'url':'http://localhost/SKILL.md'}).status_code==422


def test_mutation_gate(skills_web):
    client,base,root=skills_web
    response=client.post(base+'/skills/create',json={'id':'review','origin':'project','content':DOC.decode()},headers={'Origin':'https://evil.example'})
    assert response.status_code==403
    assert not (root/'.alfrd/skills').exists()


def test_validation_scalar_spans_preserve_comments_and_unicode(skills_web):
    client,base,root=skills_web
    client.environ_base['HTTP_X_CSRF_TOKEN']=client.get('/api/studio/session').json['csrf_token']
    content='---\n# 🦉 comment\n"name": "Review \\"diffs\\"" # retain comment\ndescription: details\nunknown: 12\n---\nBody\n'
    result=client.post(base+'/skills/validate',json={'content':content})
    assert result.status_code==200
    start,end=result.json['scalar_spans']['name']
    # Match the browser's UTF-16 indexing, including the astral character before the scalar.
    encoded=content.encode('utf-16-le')
    updated=(encoded[:2*start]+'"New title"'.encode('utf-16-le')+encoded[2*end:]).decode('utf-16-le')
    assert '"name": "New title" # retain comment' in updated
    assert '# 🦉 comment' in updated and 'unknown: 12' in updated

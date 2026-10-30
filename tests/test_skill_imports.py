import io
import json
import os
import socket
import stat
import tarfile
import zipfile
from pathlib import Path
import pytest
from alfrd import skill_imports as imports
from alfrd.skill_authoring import SkillError
DOC=b'---\nname: review\ndescription: Review diffs\n---\nRead diffs.\n'

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    imports._STORE.clear()
    def forbidden(*a,**kw):
        raise AssertionError('Real network forbidden')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    monkeypatch.setattr(socket,'socket',forbidden)


def raw(url,budget):
    budget.count(DOC)
    return DOC,url,'text/plain'

@pytest.mark.parametrize('url', ['http://example.org/SKILL.md','file:///etc/passwd','https://localhost/a','https://127.0.0.1/a','https://169.254.169.254/a','https://[::1]/a','https://[::ffff:127.0.0.1]/a','https://127.1/a','https://0x7f000001/a','https://example.org:444/a','https://a:b@example.org/a','https://example.org/a#x','https://a.local/a','https://example.org/\nx','https://[fe80::1%25eth0]/a'])
def test_unsafe_urls(url):
    with pytest.raises(SkillError) as error:
        imports.validate_url(url)
    assert error.value.reason=='unsafe_url'

@pytest.mark.parametrize('address', ['10.1.1.1','127.0.0.1','169.254.169.254','224.0.0.1','0.0.0.0','::1','fc00::1','fe80::1','::ffff:192.168.1.1'])
def test_private_addresses(address):
    assert not imports.public_ip(address)


def test_dns_all_addresses_checked(tmp_path,monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443)),(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    with pytest.raises(SkillError,match='blocked network'):
        imports.fetch('https://example.org/SKILL.md',imports.Budget())


def make_zip(files):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for p,data in files:
            archive.writestr(p,data)
    return buffer.getvalue()

@pytest.mark.parametrize('name', ['../outside','/outside','C:/outside','a\\b','wrapper/../outside','a//b','a/./b','a\x01b'])
def test_archive_traversal(name):
    with pytest.raises(SkillError):
        imports.archive_files(make_zip([('SKILL.md',DOC),(name,b'bad')]))


def test_zip_link_and_case_collision():
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        link=zipfile.ZipInfo('link');link.create_system=3;link.external_attr=(stat.S_IFLNK|0o777)<<16
        archive.writestr(link,'/tmp/escape');archive.writestr('SKILL.md',DOC)
    with pytest.raises(SkillError):
        imports.archive_files(buffer.getvalue())
    with pytest.raises(SkillError):
        imports.archive_files(make_zip([('SKILL.md',DOC),('guide.md',b'a'),('GUIDE.md',b'b')]))

@pytest.mark.parametrize('kind', [tarfile.SYMTYPE,tarfile.LNKTYPE,tarfile.FIFOTYPE,tarfile.CHRTYPE])
def test_tar_special_files(kind):
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
        member=tarfile.TarInfo('link');member.type=kind;member.linkname='outside';archive.addfile(member)
    with pytest.raises(SkillError):
        imports.archive_files(buffer.getvalue())

@pytest.mark.parametrize('name,data', [('run.sh',b'echo hello'),('.hidden.md',b'secret'),('.alfrd-source.json',b'{}'),('nested.zip',b'archive'),('bad.txt',b'\xff'),('bad.json',b'\0')])
def test_text_only_policy(name,data):
    with pytest.raises(SkillError):
        imports.archive_files(make_zip([('SKILL.md',DOC),(name,data)]))


def test_oversize_bomb_and_manifest():
    for name,data in [('big.txt',b'a'*(1024*1024+1)),('SKILL.md',DOC+b'a'*65536)]:
        with pytest.raises(SkillError) as error:
            imports.archive_files(make_zip([('SKILL.md',DOC), (name,data)] if name!='SKILL.md' else [(name,data)]))
        assert error.value.status in (413,422)


def test_wrapper_ambiguity_and_entire_archive_validation():
    assert imports.archive_files(make_zip([('wrapper/SKILL.md',DOC),('wrapper/references/a.md',b'text')]))['SKILL.md']==DOC
    with pytest.raises(SkillError):
        imports.archive_files(make_zip([('a/SKILL.md',DOC),('b/SKILL.md',DOC)]))
    with pytest.raises(SkillError):
        imports.archive_files(make_zip([('repo/skill/SKILL.md',DOC),('repo/../bad',b'x')]),subtree='skill')


def test_preview_confirm_ownership_collision_expiry_and_idempotence(tmp_path):
    preview=imports.prepare_skill_import(tmp_path,'https://example.org/SKILL.md',owner='session1',transport=raw)
    assert not (tmp_path/'.alfrd').exists()
    assert preview['files'][0]['bytes']==len(DOC)
    with pytest.raises(SkillError):
        imports.commit_skill_import(tmp_path,preview['token'],'project','review',owner='session2')
    result=imports.commit_skill_import(tmp_path,preview['token'],'project','review',owner='session1')
    path=Path(result['path']);source=json.loads((path.parent/'.alfrd-source.json').read_text())
    assert source['bundle_sha256']==preview['digest']
    assert imports.commit_skill_import(tmp_path,preview['token'],'project','review',owner='session1')==result
    with pytest.raises(SkillError):
        imports.commit_skill_import(tmp_path,preview['token'],'project','other',owner='session1')
    again=imports.prepare_skill_import(tmp_path,'https://example.org/SKILL.md',owner='session1',transport=raw)
    with pytest.raises(SkillError) as error:
        imports.commit_skill_import(tmp_path,again['token'],'project','review',owner='session1')
    assert error.value.reason=='already_exists'
    imports._STORE[again['token']]['expires']=0
    with pytest.raises(SkillError) as error:
        imports.commit_skill_import(tmp_path,again['token'],'project','review',owner='session1')
    assert error.value.status==410


def test_quota_discard_tampering(tmp_path):
    previews=[imports.prepare_skill_import(tmp_path,'https://example.org/SKILL.md',transport=raw) for _ in range(3)]
    with pytest.raises(SkillError) as error:
        imports.prepare_skill_import(tmp_path,'https://example.org/SKILL.md',transport=raw)
    assert error.value.status==429
    imports.discard_skill_import(tmp_path,previews[0]['token'])
    p=imports.prepare_skill_import(tmp_path,'https://example.org/SKILL.md',transport=raw)
    imports._STORE[p['token']]['files']['SKILL.md']=DOC+b'changed'
    with pytest.raises(SkillError,match='changed'):
        imports.commit_skill_import(tmp_path,p['token'],'project','review')


def test_github_translation_and_slash_ref():
    sha='a'*40;seen=[]
    def api(url,budget):
        seen.append(url)
        if url.endswith('feature%2Fbranch'):
            return json.dumps({'sha':sha}).encode(),url,'application/json'
        raise SkillError('fetch_failed','Upstream returned HTTP 404',502)
    url,commit,subtree=imports.github_source('https://github.com/o/r/blob/feature/branch/SKILL.md',imports.Budget(),api)
    assert url==f'https://raw.githubusercontent.com/o/r/{sha}/SKILL.md' and commit==sha and subtree is None
    def pinned(url,budget):
        return json.dumps({'sha':sha}).encode(),url,'application/json'
    url,commit,subtree=imports.github_source(f'https://github.com/o/r/tree/{sha}/skills/review',imports.Budget(),pinned)
    assert url==f'https://codeload.github.com/o/r/tar.gz/{sha}' and subtree=='skills/review'
    with pytest.raises(SkillError,match='Ambiguous'):
        imports.github_source('https://github.com/o/r/tree/main/skills/review',imports.Budget(),pinned)


def test_cli_token_roundtrip(tmp_path,monkeypatch):
    monkeypatch.setattr(imports,'_cli_store',lambda:tmp_path/'previews')
    (tmp_path/'previews').mkdir(mode=0o700)
    p=imports.prepare_skill_import(tmp_path,'https://example.org/SKILL.md',owner='cli:fixture',transport=raw)
    imports.persist_cli_preview(p['token'],owner='cli:fixture');imports._STORE.clear()
    imports.restore_cli_preview(tmp_path,p['token'],owner='cli:fixture')
    result=imports.commit_skill_import(tmp_path,p['token'],'global','review',owner='cli:fixture')
    assert Path(result['path']).read_bytes()==DOC


def test_provenance_local_edit(tmp_path):
    from alfrd.skills import library, save_skill_document
    p=imports.prepare_skill_import(tmp_path,'https://example.org/SKILL.md',transport=raw)
    result=imports.commit_skill_import(tmp_path,p['token'],'project','review')
    assert not library(tmp_path)[0]['locally_modified']
    save_skill_document(tmp_path,'review',DOC.decode()+'Changed.',base_revision=result['revision'])
    assert library(tmp_path)[0]['locally_modified']


class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=None):
        self.status=status;self.headers=headers or {};self.chunks=list(chunks or [DOC])
    def getheader(self,key,default=None):
        return self.headers.get(key,default)
    def read1(self,size):
        return self.chunks.pop(0) if self.chunks else b''


def mock_http(monkeypatch,responses):
    seen=[]
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))])
    class Connection:
        sock=None
        def __init__(self,host,address,budget):
            seen.append((host,address));self.response=responses.pop(0)
        def request(self,method,path,headers):
            assert method=='GET' and headers['Accept-Encoding']=='identity'
            assert not any(k.lower() in ('cookie','authorization','proxy-authorization') for k in headers)
        def getresponse(self):
            return self.response
        def close(self):
            pass
    monkeypatch.setattr(imports,'PinnedHTTPS',Connection)
    return seen


def test_redirect_revalidation_and_ambient_proxy_bypass(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:9999')
    seen=mock_http(monkeypatch,[FakeResponse(302,{'Location':'https://other.example/SKILL.md'},[b'redirect']),FakeResponse()])
    budget=imports.Budget();value,final,_=imports.fetch('https://example.org/SKILL.md',budget)
    assert value==DOC and final=='https://other.example/SKILL.md'
    assert seen==[('example.org','8.8.8.8'),('other.example','8.8.8.8')]
    assert budget.bytes==len(DOC)+8
    mock_http(monkeypatch,[FakeResponse(302,{'Location':'http://127.0.0.1/a'})])
    with pytest.raises(SkillError) as error:
        imports.fetch('https://example.org/SKILL.md',imports.Budget())
    assert error.value.reason=='unsafe_url'

@pytest.mark.parametrize('response,reason', [
    (FakeResponse(headers={'Content-Encoding':'gzip'}),'fetch_failed'),
    (FakeResponse(headers={'Content-Length':str(imports.LIMIT+1)}),'download_too_large'),
    (FakeResponse(headers={'Content-Length':'1'},chunks=[b'x'*(imports.LIMIT+1)]),'download_too_large'),
    (FakeResponse(status=401),'fetch_failed'),
])
def test_http_resource_guards(monkeypatch,response,reason):
    mock_http(monkeypatch,[response])
    with pytest.raises(SkillError) as error:
        imports.fetch('https://example.org/SKILL.md',imports.Budget())
    assert error.value.reason==reason


def test_redirect_request_and_deadline_limits(monkeypatch):
    mock_http(monkeypatch,[FakeResponse(302,{'Location':'https://example.org/next'}) for _ in range(4)])
    with pytest.raises(SkillError,match='three redirects'):
        imports.fetch('https://example.org/SKILL.md',imports.Budget())
    budget=imports.Budget();budget.requests=8
    with pytest.raises(SkillError,match='eight outbound'):
        imports.fetch('https://example.org/SKILL.md',budget)
    budget=imports.Budget();budget.deadline=0
    with pytest.raises(SkillError) as error:
        budget.remaining()
    assert error.value.status==504


def test_ip_pinning_sni_and_peer_mismatch(monkeypatch):
    calls=[]
    class Raw:
        def connect(self,address):
            calls.append(address)
        def getpeername(self):
            return ('8.8.8.8',443)
        def settimeout(self,value):
            pass
        def close(self):
            pass
    class Context:
        def wrap_socket(self,sock,server_hostname):
            calls.append(server_hostname);return sock
    monkeypatch.setattr(socket,'socket',lambda *args:Raw())
    conn=imports.PinnedHTTPS('example.org','8.8.8.8',imports.Budget())
    assert conn._context.check_hostname and conn._context.verify_mode==imports.ssl.CERT_REQUIRED
    conn._context=Context();conn.connect()
    assert calls==[('8.8.8.8',443),'example.org']
    monkeypatch.setattr(Raw,'getpeername',lambda self:('127.0.0.1',443))
    with pytest.raises(SkillError,match='peer'):
        imports.PinnedHTTPS('example.org','8.8.8.8',imports.Budget()).connect()


def test_archive_count_expansion_and_unselected_limits():
    with pytest.raises(SkillError):
        imports.archive_files(make_zip([('SKILL.md',DOC),*[(f'{i}.txt',b'a') for i in range(1000)]]))
    # Whole archive accounting runs before the requested GitHub subtree is selected.
    with pytest.raises(SkillError):
        imports.archive_files(make_zip([('repo/skill/SKILL.md',DOC),*[(f'repo/other/{i}.txt',b'a'*(1024*1024)) for i in range(11)]]),subtree='skill')


def test_failed_preview_never_publishes(tmp_path):
    def html(url,budget):
        return b'<html>Sign in</html>',url,'text/html'
    with pytest.raises(SkillError,match='raw SKILL.md'):
        imports.prepare_skill_import(tmp_path,'https://example.org/SKILL.md',transport=html)
    assert not (tmp_path/'.alfrd').exists() and not imports._STORE


def test_tar_metadata_expansion_budget_and_zip_directory_preflight():
    import gzip
    bomb=gzip.compress(b'\0'*(imports.LIMIT+2000*1024+1))
    with pytest.raises(SkillError) as error:
        imports.archive_files(bomb)
    assert error.value.status==413
    data=bytearray(make_zip([('SKILL.md',DOC)]))
    end=data.rfind(b'PK\x05\x06');data[end+10:end+12]=(2001).to_bytes(2,'little')
    with pytest.raises(SkillError) as error:
        imports.archive_files(bytes(data))
    assert error.value.status==413


def test_public_ipv6_url_and_invalid_token(tmp_path):
    assert imports.validate_url('https://[2606:4700:4700::1111]/SKILL.md').hostname=='2606:4700:4700::1111'
    with pytest.raises(SkillError):
        imports.commit_skill_import(tmp_path,{},'project','review')


def test_archive_redirect_without_extension(tmp_path):
    data=make_zip([('SKILL.md',DOC),('references/guide.md',b'reference')])
    def downloaded(url,budget):
        budget.count(data)
        return data,'https://cdn.example/download?id=skill','application/octet-stream'
    preview=imports.prepare_skill_import(tmp_path,'https://example.org/skill.zip',transport=downloaded)
    assert [f['path'] for f in preview['files']]==['SKILL.md','references/guide.md']


def test_direct_archive_requires_common_wrapper():
    with pytest.raises(SkillError,match='common skill wrapper'):
        imports.archive_files(make_zip([('wrapper/SKILL.md',DOC),('other/notes.md',b'outside')]))

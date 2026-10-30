from pathlib import Path
from typer.testing import CliRunner
from alfrd.cli import alfrd_cli
from alfrd import skill_imports
from tests.test_skill_imports import DOC, raw


def test_new_edit_scope_and_file(tmp_path):
    file=tmp_path/'draft.md';file.write_bytes(DOC)
    runner=CliRunner()
    args=['skills','new','review','--project',str(tmp_path),'--file',str(file)]
    result=runner.invoke(alfrd_cli,args)
    assert result.exit_code==0,result.output
    file.write_bytes(DOC+b'new')
    result=runner.invoke(alfrd_cli,['skills','edit','review','--project',str(tmp_path),'--file',str(file)])
    assert result.exit_code==0,result.output
    assert (tmp_path/'.alfrd/skills/review/SKILL.md').read_bytes()==DOC+b'new'
    assert runner.invoke(alfrd_cli,args).exit_code!=0
    assert runner.invoke(alfrd_cli,['skills','new','other','--file',str(file)]).exit_code!=0
    assert runner.invoke(alfrd_cli,['skills','new','other','--global','--project',str(tmp_path),'--file',str(file)]).exit_code!=0


def test_import_preview_confirm_and_yes(tmp_path,monkeypatch):
    import json
    monkeypatch.setattr(skill_imports,'fetch',raw)
    (tmp_path/'previews').mkdir(mode=0o700)
    monkeypatch.setattr(skill_imports,'_cli_store',lambda:tmp_path/'previews')
    runner=CliRunner()
    args=['skills','import','https://example.org/SKILL.md','--project',str(tmp_path)]
    preview=runner.invoke(alfrd_cli,[*args,'--preview-only','--json'])
    assert preview.exit_code==0,preview.output
    token=json.loads(preview.output)['token']
    assert not (tmp_path/'.alfrd/skills').exists()
    skill_imports._STORE.clear()
    confirmed=runner.invoke(alfrd_cli,['skills','import','--confirm',token,'--project',str(tmp_path),'--id','review'])
    assert confirmed.exit_code==0,confirmed.output
    assert (tmp_path/'.alfrd/skills/review/SKILL.md').read_bytes()==DOC
    result=runner.invoke(alfrd_cli,[*args,'--yes','--id','copy'])
    assert result.exit_code==0,result.output


def test_optional_activation_and_partial_failure(tmp_path):
    from alfrd.skills import activation_state
    file=tmp_path/'draft.md';file.write_bytes(DOC)
    runner=CliRunner()
    result=runner.invoke(alfrd_cli,['skills','new','review','--global','--project',str(tmp_path),'--file',str(file),'--activate','orchestrator','--activate','role:custom role'])
    assert result.exit_code==0,result.output
    assert len(activation_state(tmp_path)['activations'])==2
    # Publishing and activation are separate commits, so a failed scope leaves the skill saved.
    result=runner.invoke(alfrd_cli,['skills','new','partial','--project',str(tmp_path),'--file',str(file),'--activate','invalid-scope'])
    assert result.exit_code==1 and 'Skill saved; activation was not saved' in result.output
    assert (tmp_path/'.alfrd/skills/partial/SKILL.md').exists()

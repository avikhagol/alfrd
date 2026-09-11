from typer.testing import CliRunner

from alfrd import get_project_dir
from alfrd.cli import alfrd_cli


runner = CliRunner()


def test_cli_help_smoke():
    result = runner.invoke(alfrd_cli, ["--help"])

    assert result.exit_code == 0, result.output
    assert "init" in result.output
    assert "run" in result.output
    assert "inspect" in result.output


def test_cli_command_help_smoke():
    for command in ("init", "ls", "lsp", "run", "add", "rm", "inspect", "nrun"):
        result = runner.invoke(alfrd_cli, [command, "--help"])
        assert result.exit_code == 0, f"{command}: {result.output}"


def test_cli_init_uses_isolated_project_directory(tmp_path):
    result = runner.invoke(alfrd_cli, ["init", "smoke"])

    assert result.exit_code == 0, result.output
    project_dir = get_project_dir() / "smoke"
    assert project_dir.is_dir()
    assert project_dir.is_relative_to(tmp_path)


def test_cli_rm_rejects_paths_outside_project_directory(tmp_path):
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()

    result = runner.invoke(alfrd_cli, ["rm", str(sentinel)])

    assert result.exit_code != 0
    assert isinstance(result.exception, ValueError)
    assert sentinel.is_dir()

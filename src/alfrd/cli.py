from pathlib import Path
import os
import shutil
import sys
from typing import Optional, Annotated

import typer

from alfrd import (
    B,
    Pipeline,
    REGISTERED_STEPS,
    VALIDATE_AFTER,
    VALIDATE_BEFORE,
    X,
    __version__,
    c,
    get_project_dir,
)
from alfrd.plugins import List, load_projects
from alfrd.util import padded_output, read_inputfile
from alfrd.core.inspect import Inspector, Executor
from alfrd.core.project import project_directory


alfrd_cli = typer.Typer()


try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    else:
        sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
except:
    pass


def load_steps(prefix=""):
    for i, (name, info) in enumerate(REGISTERED_STEPS.items()):
        print(f"{prefix}- {i}\t {c['bc']}{name.ljust(20)}{c['x']}: {info['desc']}")


def list_steps(proj):
    """List all registered steps."""
    print(f"\n\t\t{c['c']}ALFRD ({__version__}){c['x']}\n")
    print(f"  Run following pipeline steps for {c['bc']}{proj.upper()}{c['x']}")
    if not REGISTERED_STEPS:
        print("No pipeline steps registered.")
    else:
        load_steps()


def proj_dir(proj, create=False):
    project_dir = project_directory(proj)

    if create:
        project_dir.mkdir(parents=True, exist_ok=True)
        print(f"Created project directory at {project_dir}")
    if not project_dir.exists():
        print(f"Project directory '{proj}' does not exist.")
        raise ValueError(f"Project {proj} not found.")
    return project_dir


@alfrd_cli.command()
def init(proj: str):
    """Initialize the project-specific plugin directory."""
    project_dir = project_directory(proj)
    if not project_dir.exists():
        _ = proj_dir(proj, create=True)
    else:
        print(f"Project directory already exists at {project_dir}")


@alfrd_cli.command()
def ls(proj: str):
    """List all available pipeline steps for a project."""
    project_dir = proj_dir(proj)

    load_projects(project_dir)
    list_steps(proj)


@alfrd_cli.command()
def lsp():
    """List all available projects."""
    project_dirs = list(get_project_dir().glob("*"))

    if len(project_dirs):
        print(f"\tAvailable projects:")
        for proj in project_dirs:
            print(f"\t\t{proj.name}")
            try:
                load_projects(proj)
                load_steps(prefix="\t\t\t")
            except:
                print("\t\t\tsteps not configured properly!")
    else:
        print("no projects found!")


@alfrd_cli.command()
def run(
    step_name: str = typer.Argument(help="name of the step name in project"),
    proj: str = typer.Argument(help="name of the ALFRD project"),
    step_to: str = None,
    params: List[str] = typer.Argument(None, help="Key-value pairs of parameters or parameter file path (e.g., id=123 name=Test)"),
    steps: Optional[List[str]] = typer.Option(None, help="list of steps e.g., --steps=step1 --steps=step2 | supersedes values and sequence of the steps", show_default=False),
):
    """Run a specific pipeline step for a project."""
    _params_found = {}

    art = f"""
    ╔══════════════════════════════════════════════════════════════════╗
    ║{proj.upper():^66}║
    ╚══════════════════════════════════════════════════════════════════╝
    """
    print(art)

    if params and len(params):
        for param in params:
            if not '=' in param:
                if Path(param).exists():
                    _params_found, _, _ = read_inputfile(Path(param).absolute().parent, Path(param).name)
                    Pipeline.update_params(_params_found)

        _params_found = {param.split("=")[0]: param.split("=")[1] for param in params if '=' in param}
    Pipeline.update_params(_params_found)

    project_dir = proj_dir(proj)
    load_projects(project_dir)

    if step_name not in REGISTERED_STEPS:
        print(f"Step '{step_name}' not found! Use `ls` to view available steps.")
        raise typer.Exit()
    allsteps = steps or list(REGISTERED_STEPS.keys())
    idx_from = allsteps.index(step_name)
    idx_to = idx_from + 1

    if step_to:
        if step_to not in REGISTERED_STEPS:
            print(f"Step '{step_to}' not found! Use `ls` to view available steps.")
            raise typer.Exit()
        else:
            idx_from = allsteps.index(step_name)
            idx_to = allsteps.index(step_to) + 1

    steps = allsteps[idx_from:idx_to]
    print("Following steps will be executed in the sequence:")
    print(f"{c['bc']}", "-", f"\n - ".join(steps), f"{c['x']}\n")
    for s, step_name in enumerate(steps):
        if s == 0:
            Pipeline.prev_step_success = True
            Pipeline.validation_success = True
        Pipeline.step_name = step_name

        if Pipeline.prev_step_success and (step_name in VALIDATE_BEFORE) and VALIDATE_BEFORE[step_name]['functions']:
            print(f"\n>  {B}Pre-processing{X} ({Pipeline.step_name})")
            print("""  ─────────────────────────────────────────────────────────────────""")
            with padded_output(4):
                Pipeline.validate_steps = VALIDATE_BEFORE
                Pipeline.run_validations()

        if Pipeline.prev_step_success and Pipeline.validation_success:
            print(f"\n>  {B}Processing{X}: {proj.upper()} {step_name}")
            print("""  ─────────────────────────────────────────────────────────────────""")
            with padded_output(4):
                Pipeline.run_step()

        if Pipeline.validation_success and Pipeline.prev_step_success and (step_name in VALIDATE_AFTER) and VALIDATE_AFTER[step_name]['functions']:
            print(f"\n>  {B}Post-processing{X} ({Pipeline.step_name})")
            print("""  ─────────────────────────────────────────────────────────────────""")

            with padded_output(4):
                Pipeline.validate_steps = VALIDATE_AFTER
                Pipeline.run_validations()

        if Pipeline.validation_success:
            print(f"{B} finished : {c['bc']}{step_name}{X}")
        else:
            print(f"{B} skipped  : {c['bc']}{step_name}{X}")


@alfrd_cli.command()
def add(
    script_path: str,
    proj: str,
    symlink: bool = typer.Option(True, help="(instead of copying files a shortcut is placed in the project folder"),
):
    """Add a new plugin to a specific project."""
    project_dir = proj_dir(proj)
    script_path = Path(script_path).absolute()

    if not script_path.is_file():
        print(f"File '{script_path}' not found.")
        raise typer.Exit()

    dest_path = project_dir / script_path.name
    if symlink:
        if Path(dest_path).exists():
            Path.unlink(dest_path)
        Path(dest_path).symlink_to(script_path)
    else:
        shutil.copy(script_path, dest_path)
    print(f"Added plugin to {proj}: {dest_path}")


@alfrd_cli.command()
def rm(proj: str):
    """Remove a specific project."""
    project_dir = proj_dir(proj)
    shutil.rmtree(project_dir)

    print(f"removed {proj}: {project_dir}")

@alfrd_cli.command()
def inspect(configfile: str, schema: bool = False):
    """Inspect a specific project."""
    inspect = Inspector(configfile)
    if schema:
        inspect.print_schema()
    else:
        inspect.print_config()

@alfrd_cli.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def nrun(
    ctx: typer.Context,
    yaml_configfile,
    name: Annotated[str, typer.Option("--name", "-n")] = "runner",
):
    """Run a specific entrypoint and forward remaining args."""
    args = list(ctx.args)

    executor = Executor(yaml_configfile)
    executor.run_entrypoint(name, args)


def _serve_web(host: str, port: int, debug: bool) -> None:
    try:
        from alfrd.gui import create_app
    except ImportError as error:
        raise typer.BadParameter(
            "The web dependencies are not installed; install 'alfrd[gui]'."
        ) from error

    create_app().run(host=host, port=port, debug=debug)


@alfrd_cli.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(5000, min=1, max=65535, help="TCP port to bind."),
    debug: bool = typer.Option(False, help="Enable Flask development debugging."),
):
    """Serve the read-only ALFRD catalog web application."""

    _serve_web(host, port, debug)


@alfrd_cli.command()
def gui(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(5000, min=1, max=65535, help="TCP port to bind."),
    debug: bool = typer.Option(False, help="Enable Flask development debugging."),
):
    """Alias for ``alfrd serve``."""

    _serve_web(host, port, debug)


if __name__ == "__main__":
    alfrd_cli()

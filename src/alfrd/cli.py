from pathlib import Path
import os
import shutil
import subprocess
import sys
import webbrowser
import ipaddress
import threading
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
    get_alfrd_dir,
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


def _open_dashboard_when_ready(url: str, stopped: threading.Event) -> None:
    """Open once the local HTTP server responds; stop if serving exits early."""
    from urllib.error import URLError
    from urllib.request import ProxyHandler, build_opener

    opener = build_opener(ProxyHandler({}))
    for _ in range(50):
        if stopped.wait(0.1):
            return
        try:
            with opener.open(url, timeout=0.25) as response:
                ready = response.status == 200
        except (OSError, URLError):
            continue
        if ready and not stopped.is_set():
            try:
                if not webbrowser.open(url):
                    print("Could not open a browser; open the dashboard URL above manually.")
            except Exception as error:
                print(f"Could not open a browser ({error}); open the dashboard URL above manually.")
            return
    if not stopped.is_set():
        print("Browser launch timed out; open the dashboard URL above manually.")


def _interrupt_self() -> None:
    """Stop this process as if Ctrl+C was pressed (used by the Studio's Quit button)."""
    import signal

    if os.name == "nt":  # no reliable self-SIGINT on Windows
        os._exit(0)
    os.kill(os.getpid(), signal.SIGINT)


def _connect_startup_project(service, project: str | None) -> str | None:
    """Register the folder `alfrd serve` was started for (``--project`` or the cwd)."""
    from alfrd.manifest import ManifestError, load_manifest
    from alfrd.runtime import RuntimeNotFound
    from alfrd.studio_defs import manifest_file

    folder = Path(project).expanduser().resolve() if project else Path.cwd().resolve()
    if folder.is_file():
        folder = folder.parent
    path = manifest_file(folder)
    if path is None:
        if project:
            raise typer.BadParameter(f"No alfrd.yaml in {folder}")
        return None
    try:
        manifest = load_manifest(path)
    except (ManifestError, OSError) as error:
        print(f"alfrd.yaml in {folder} was not loaded: {error}")
        return None
    try:
        existing = service.get_project_by_name(manifest.name)
    except RuntimeNotFound:
        existing = None
    if existing is not None and Path(existing.root_path).resolve() != folder:
        print(f"Project {manifest.name!r} is already connected to {existing.root_path}; showing that one.")
        return manifest.name
    if existing is None:
        service.register_manifest(manifest, root_path=folder, create_root=False)
    print(f"Project {manifest.name}: {folder}")
    return manifest.name


def _serve_web(host: str, port: int, debug: bool, runtime_db: str | None = None, no_browser: bool = False,
               demo: bool = False, project: str | None = None, all_projects: bool = False,
               live_interval: float = 2.0) -> None:
    try:
        from alfrd.gui import create_app
    except ImportError as error:
        raise typer.BadParameter(
            "The web dependencies are not installed; install 'alfrd[gui]'."
        ) from error

    from alfrd.gui.services import RuntimeCatalogReader

    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if debug and not loopback:
        raise typer.BadParameter("Debug mode is only available on a loopback interface.")
    database = Path(runtime_db).expanduser().resolve() if runtime_db else _default_runtime_db().resolve()
    service = _runtime_service(str(database))
    config = {
        "RUNTIME_DATABASE": str(database),
        "RUNTIME_SERVICE": service,
        "CATALOG_READER": RuntimeCatalogReader(service),
        "CATALOG_CREATE_SCHEMA": False,
        "RUNTIME_MUTATIONS_ENABLED": loopback,
        "STUDIO_DEMO": demo,
        # Live updates: seconds between checks of a busy project tree (0 turns them off).
        "STUDIO_LIVE_INTERVAL": max(0.0, float(live_interval)),
        "STUDIO_DEFAULT_PROJECT": _connect_startup_project(service, project),
    }
    # Started for one project: the Studio shows only that one (unless --all-projects).
    config["STUDIO_PROJECTS"] = None if all_projects or not config["STUDIO_DEFAULT_PROJECT"] else [config["STUDIO_DEFAULT_PROJECT"]]
    others = [p.name for p in service.list_projects() if p.name != config["STUDIO_DEFAULT_PROJECT"]]
    if config["STUDIO_PROJECTS"] and others:
        print(f"Also remembered in {database}: {', '.join(others)} (show them with --all-projects; remove with `alfrd projects forget NAME`).")
    elif not config["STUDIO_DEFAULT_PROJECT"] and others:
        print(f"No alfrd.yaml here. Showing projects remembered in {database}: {', '.join(others)}.")
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    authority = f"[{browser_host}]" if ":" in browser_host else browser_host
    url = f"http://{authority}:{port}/studio/"
    print(f"ALFRD Studio: {url}")
    print(f"ALFRD dashboard: http://{authority}:{port}/dashboard/")
    app = create_app(config)
    stopped = threading.Event()
    browser_thread = None
    if (not no_browser and (loopback or host in {"0.0.0.0", "::"})
            and (not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true")):
        browser_thread = threading.Thread(
            target=_open_dashboard_when_ready, args=(url, stopped), daemon=True,
        )
        browser_thread.start()
    try:
        config_map = getattr(app, "config", None)
        if not debug and isinstance(config_map, dict):
            # Settings → Quit in the Studio: same as Ctrl+C in this terminal.
            config_map["STUDIO_SHUTDOWN"] = _interrupt_self
            if threading.current_thread() is threading.main_thread():
                import signal

                # Background jobs (`alfrd serve &`, nohup) start with SIGINT ignored.
                signal.signal(signal.SIGINT, signal.default_int_handler)
            print("Press Ctrl+C (or Quit in the Studio) to stop.")
        try:
            app.run(host=host, port=port, debug=debug)
        except KeyboardInterrupt:
            pass
        print("alfrd serve stopped.")
    finally:
        stopped.set()
        if browser_thread is not None:
            browser_thread.join(timeout=1)


@alfrd_cli.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(5000, min=1, max=65535, help="TCP port to bind."),
    debug: bool = typer.Option(False, help="Enable Flask development debugging."),
    runtime_db: Optional[str] = typer.Option(
        None, help="Path to the runtime SQLite database that backs matrix routes."
    ),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open the dashboard in a browser."),
    demo: bool = typer.Option(False, "--demo", help="Show the built-in demo data in the Studio."),
    project: Optional[str] = typer.Option(
        None, "--project", help="Folder with alfrd.yaml to open (default: the current folder when it has one)."
    ),
    all_projects: bool = typer.Option(
        False, "--all-projects", help="Also show every other project remembered in the runtime database."
    ),
    live_interval: float = typer.Option(
        2.0, "--live-interval", min=0.0,
        help="Seconds between checks of the project folder while it changes (5 s when quiet; 0 = no live updates).",
    ),
):
    """Serve ALFRD Studio (default) and the dashboard, backed by the runtime database."""

    _serve_web(host, port, debug, runtime_db, no_browser, demo, project, all_projects, live_interval=live_interval)


@alfrd_cli.command()
def gui(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(5000, min=1, max=65535, help="TCP port to bind."),
    debug: bool = typer.Option(False, help="Enable Flask development debugging."),
    runtime_db: Optional[str] = typer.Option(
        None, help="Path to the runtime SQLite database that backs matrix routes."
    ),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open the dashboard in a browser."),
):
    """Alias for ``alfrd serve``."""

    _serve_web(host, port, debug, runtime_db, no_browser)


def _studio_handler(directory: Path):
    from http.server import SimpleHTTPRequestHandler

    from alfrd.web import MIME_TYPES

    class StudioHandler(SimpleHTTPRequestHandler):
        extensions_map = {**SimpleHTTPRequestHandler.extensions_map, **MIME_TYPES}

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def end_headers(self):
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            super().end_headers()

        def log_message(self, format, *args):  # keep the terminal quiet
            pass

    return StudioHandler


@alfrd_cli.command()
def studio(
    port: int = typer.Option(8080, min=0, max=65535, help="TCP port to bind (0 picks a free port)."),
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    open_browser: bool = typer.Option(True, "--open-browser/--no-browser", help="Open the Studio in a browser."),
    export: Optional[str] = typer.Option(
        None, "--export", help="Copy the static Studio into this directory (e.g. for GitHub Pages) and exit."
    ),
    demo: bool = typer.Option(False, "--demo", help="Open the Studio with the built-in demo data."),
):
    """Launch ALFRD Studio locally (browser-only, no backend)."""
    from http.server import ThreadingHTTPServer

    from alfrd.web import export_site, missing_assets, web_root

    missing = missing_assets()
    if missing:
        raise typer.BadParameter(f"Studio assets missing from this installation: {', '.join(missing)}")
    if export:
        target = export_site(export)
        print(f"Studio exported to {target}")
        return
    try:
        httpd = ThreadingHTTPServer((host, port), _studio_handler(web_root()))
    except OSError as error:
        raise typer.BadParameter(f"Could not bind {host}:{port} ({error})") from error
    bound_host, bound_port = httpd.server_address[:2]
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    authority = f"[{browser_host}]" if ":" in browser_host else browser_host
    url = f"http://{authority}:{bound_port}/{'?demo=1' if demo else ''}"
    print(f"ALFRD Studio (browser mode): {url}")
    print("Static files only - use `alfrd serve` for live runtime projects. Press Ctrl+C to stop.")
    stopped = threading.Event()
    if open_browser:
        threading.Thread(target=_open_dashboard_when_ready, args=(url, stopped), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set()
        httpd.server_close()


projects_cli = typer.Typer(help="Projects remembered in the runtime database (used by `alfrd serve`).")
alfrd_cli.add_typer(projects_cli, name="projects")


@projects_cli.command("list")
def projects_list(db: Optional[str] = typer.Option(None, "--db", help="Path to the runtime SQLite database.")):
    """List remembered projects and their folders."""
    service = _runtime_service(db)
    projects = service.list_projects()
    if not projects:
        print("No projects remembered.")
    for p in projects:
        exists = "" if Path(p.root_path).is_dir() else "  (folder missing)"
        print(f"{p.name}\t{p.root_path}{exists}")


@projects_cli.command("forget")
def projects_forget(
    name: str = typer.Argument(..., help="Project name (see `alfrd projects list`)."),
    db: Optional[str] = typer.Option(None, "--db", help="Path to the runtime SQLite database."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
):
    """Remove a project from the runtime database. Files on disk are not touched."""
    from alfrd.runtime import RuntimeNotFound

    service = _runtime_service(db)
    try:
        project = service.get_project_by_name(name)
    except RuntimeNotFound:
        raise typer.BadParameter(f"No project named {name!r}. See `alfrd projects list`.")
    if not yes and not typer.confirm(f"Forget {name} ({project.root_path})? Its runs in the database are removed too."):
        raise typer.Abort()
    counts = service.forget_project(name)
    print(f"Forgot {name}: {counts['runs']} run(s), {counts['datasets']} dataset(s), {counts['workflows']} workflow(s). Files untouched.")


avica_cli = typer.Typer(help="Read an AVICA reduction tree next to alfrd.yaml (never imports AVICA).")
alfrd_cli.add_typer(avica_cli, name="avica")


@avica_cli.command("summary")
def avica_summary_command(
    root: str = typer.Argument(".", help="Folder containing alfrd.yaml and avica.inp."),
    run: bool = typer.Option(True, "--run/--no-run", help="Run `avica pipe config --summary` (else read the cache)."),
    as_json: bool = typer.Option(False, "--json", help="Print the parsed rows as JSON."),
):
    """Cache `avica pipe config --summary` as avica.summary.json for the Studio."""
    import json as _json

    from alfrd.avica_layout import resolve_config

    try:
        config = resolve_config(root, run_summary=run)
    except FileNotFoundError as error:
        print(f"{error}; save the table manually with `avica pipe config --summary > avica.summary.txt`.")
        raise typer.Exit(code=1)
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"avica pipe config --summary failed: {error}")
        raise typer.Exit(code=1)
    if not config.summary:
        print("No summary cached; run without --no-run.")
        raise typer.Exit(code=1)
    rows = config.summary["rows"]
    if as_json:
        print(_json.dumps(rows, indent=1, default=str))
    else:
        print(f"{len(rows)} parameters; target_dir={config.get('target_dir')}; cached in {Path(root) / 'avica.summary.json'}")


@avica_cli.command("scan")
def avica_scan_command(
    root: str = typer.Argument(".", help="Folder containing alfrd.yaml and avica.inp."),
    bundle: Optional[str] = typer.Option(None, "--bundle", help="Write the files the Studio reads to this JSON (import it in the Studio)."),
    log_tail: int = typer.Option(64, "--log-tail", help="With --bundle: KiB kept from the end of each avica.logs/*.log (0 = list only)."),
):
    """Print the detected layout as JSON, or write a Studio import bundle with --bundle."""
    import json as _json

    from alfrd.avica_layout import collect_studio_files, scan_layout

    if not bundle:
        print(_json.dumps(scan_layout(root), indent=1, default=str))
        return
    try:
        data = collect_studio_files(root, log_tail=max(0, log_tail) * 1024)
    except FileNotFoundError as error:
        print(error)
        raise typer.Exit(code=1)
    out = Path(bundle)
    out.write_text(_json.dumps(data, default=str), encoding="utf-8")
    size = out.stat().st_size
    print(f"{len(data['files'])} file(s) from {data['root']} (target_dir {data['target_dir']}/) -> {out} ({size / 1024:.0f} KiB). Import it in the Studio.")


runtime_cli = typer.Typer(help="Start, resume, retry, and cancel durable runtime runs.")
alfrd_cli.add_typer(runtime_cli, name="runtime")


def _default_runtime_db() -> Path:
    return get_alfrd_dir() / "runtime.sqlite"


def _runtime_service(db: Optional[str]):
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(db or _default_runtime_db())
    store.initialize()
    return RuntimeService(store)


manifest_cli = typer.Typer(help="Validate import-free ALFRD project manifests.")
import_cli = typer.Typer(help="Import completed external workflow output into the runtime database.")
alfrd_cli.add_typer(manifest_cli, name="manifest")
alfrd_cli.add_typer(import_cli, name="import")


@manifest_cli.command("validate")
def manifest_validate(path: str = typer.Argument(..., help="Path to alfrd.yaml.")):
    """Validate a project manifest against project-manifest-v1."""
    from alfrd.manifest import ManifestError, load_manifest

    try:
        manifest = load_manifest(path)
    except ManifestError as error:
        print(f"invalid project-manifest-v1: {error}")
        raise typer.Exit(code=1)
    print(f"valid project-manifest-v1: {manifest.path}")


@import_cli.command("avica-run")
def import_avica_run_command(
    reductions_dir: str = typer.Argument(..., help="AVICA target_dir containing *_result.csv files."),
    project: str = typer.Option(..., "--project", help="Runtime project name to create."),
    manifest: str = typer.Option(..., "--manifest", help="Validated ALFRD manifest attached to this import."),
    db: Optional[str] = typer.Option(None, help="Path to the runtime SQLite database."),
):
    """Import an existing AVICA output directory without importing AVICA."""
    from alfrd.manifest import ManifestError, load_manifest
    from alfrd.runtime.avica import DEFAULT_AVICA_STEPS, import_avica_run

    try:
        document = load_manifest(manifest)
    except ManifestError as error:
        print(f"invalid manifest: {error}")
        raise typer.Exit(code=1)
    workflows = document.extra.get("workflows", [])
    workflow_spec = next(
        (item for item in workflows if isinstance(item, dict) and item.get("name") == "avica"),
        None,
    )
    steps = workflow_spec.get("steps") if workflow_spec else list(DEFAULT_AVICA_STEPS)
    if not isinstance(steps, list) or not all(isinstance(item, str) for item in steps):
        print("invalid manifest: workflows.avica.steps must be a list of step names")
        raise typer.Exit(code=1)
    try:
        result = import_avica_run(
            _runtime_service(db),
            reductions_dir,
            project_name=project,
            steps=steps,
            project_root=document.path.parent if document.path else Path(manifest).parent,
        )
    except (FileNotFoundError, ValueError) as error:
        print(f"AVICA import failed: {error}")
        raise typer.Exit(code=1)
    print(
        f"Imported {result.dataset_count} dataset(s) into project={result.project.name} "
        f"workflow={result.workflow.name}; artifacts={result.artifact_count}; "
        f"missing_artifacts={result.skipped_artifact_count}"
    )


def _print_run(run) -> None:
    print(f"run {run.id}: status={run.status} attempt={run.attempt}")
    for step in run.step_executions:
        print(f"  - {step.step_definition.key}: {step.status}")


@runtime_cli.command("start")
def runtime_start(
    workflow_id: str,
    dataset_id: str,
    db: Optional[str] = typer.Option(None, help="Path to the runtime SQLite database."),
    allow_concurrent: bool = typer.Option(
        False, help="Allow starting even if the dataset already has an active run."
    ),
    spawn: bool = typer.Option(
        True, help="Spawn a detached worker process to execute the run immediately."
    ),
):
    """Start a workflow run for one dataset via the runtime service."""
    from alfrd.runtime import DuplicateRunError, ParameterValidationError

    service = _runtime_service(db)
    try:
        run = service.start_run(workflow_id, dataset_id, allow_concurrent=allow_concurrent)
    except ParameterValidationError as error:
        print(f"parameter validation failed: {error}")
        raise typer.Exit(code=1)
    except DuplicateRunError as error:
        print(f"duplicate run rejected: {error}")
        raise typer.Exit(code=1)
    _print_run(run)
    if spawn:
        _spawn_worker(run.id, db)


@runtime_cli.command("resume")
def runtime_resume(run_id: str, db: Optional[str] = typer.Option(None), spawn: bool = typer.Option(True)):
    """Resume a failed or interrupted run from its first unfinished step."""
    service = _runtime_service(db)
    run = service.resume_run(run_id)
    _print_run(run)
    if spawn:
        _spawn_worker(run.id, db)


@runtime_cli.command("retry")
def runtime_retry(run_id: str, db: Optional[str] = typer.Option(None), spawn: bool = typer.Option(True)):
    """Retry a failed, cancelled, or interrupted run as a new run."""
    service = _runtime_service(db)
    run = service.retry_run(run_id)
    _print_run(run)
    if spawn:
        _spawn_worker(run.id, db)


@runtime_cli.command("retry-step")
def runtime_retry_step(
    run_id: str,
    step_key: str,
    db: Optional[str] = typer.Option(None),
    spawn: bool = typer.Option(True),
):
    """Retry one step in place, respecting the workflow's step order."""
    run = _runtime_service(db).retry_step(run_id, step_key)
    _print_run(run)
    if spawn:
        _spawn_worker(run.id, db)


@runtime_cli.command("cancel")
def runtime_cancel(
    run_id: str,
    db: Optional[str] = typer.Option(None),
    yes: bool = typer.Option(
        False, "--yes", help="Confirm cancellation without an interactive prompt."
    ),
    reason: Optional[str] = typer.Option(None, help="Optional cancellation reason to record."),
):
    """Cancel a pending or running run, requiring explicit confirmation."""
    if not yes:
        confirmed = typer.confirm(f"Cancel run {run_id}?", default=False)
        if not confirmed:
            print("Cancellation aborted.")
            raise typer.Exit(code=1)
    run = _runtime_service(db).cancel_run(run_id, reason=reason)
    _print_run(run)


@runtime_cli.command("logs")
def runtime_logs(run_id: str, db: Optional[str] = typer.Option(None)):
    """Print live or completed logs for every step in a run."""
    for entry in _runtime_service(db).run_logs(run_id):
        print(f"--- {entry['step_key']} ({entry['status']}) ---")
        print(entry["content"] or "(no output captured)")


@runtime_cli.command("status")
def runtime_status(run_id: str, db: Optional[str] = typer.Option(None)):
    """Print the current status of a run and its steps."""
    _print_run(_runtime_service(db).get_run(run_id))


@runtime_cli.command("execute", hidden=True)
def runtime_execute(run_id: str, db: Optional[str] = typer.Option(None)):
    """Drive one pending/running run to completion using the local worker.

    This is the entrypoint spawned as a detached subprocess by ``start``,
    ``resume``, and ``retry``; it is not meant to be invoked synchronously
    from within a Flask request.
    """
    from alfrd.runtime import run_workflow

    service = _runtime_service(db)
    status = run_workflow(service, run_id)
    print(f"run {run_id} finished with status {status.value}")


def _spawn_worker(run_id: str, db: Optional[str]) -> None:
    """Launch a detached subprocess that drives one run to completion.

    Never executes pipeline code in-process; the browser/CLI caller returns
    immediately while the spawned process owns the actual run.
    """
    command = [sys.executable, "-m", "alfrd.cli", "runtime", "execute", run_id]
    if db:
        command.extend(["--db", db])
    kwargs: dict = {}
    if os.name != "nt":
        kwargs["start_new_session"] = True
    else:  # pragma: no cover - Windows-only branch
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


if __name__ == "__main__":
    alfrd_cli()

from alfrd.agent_loop import DEFAULT_ITERATIONS, MAX_ITERATIONS
from pathlib import Path
import os
import secrets
import subprocess
import sys
import webbrowser
import ipaddress
import threading
from urllib.parse import urlencode
from typing import Optional

import typer

from alfrd import get_alfrd_dir
from alfrd.manifest_paths import project_root as _manifest_root


alfrd_cli = typer.Typer()

from alfrd.mailbox_cli import cli as mailbox_cli
alfrd_cli.add_typer(mailbox_cli, name="mailbox")


skills_cli = typer.Typer(help="Manage the global skills library.")
alfrd_cli.add_typer(skills_cli, name="skills")


@skills_cli.command("add")
def skills_add(path: Path, global_library: bool = typer.Option(False, "--global"),
               skill_id: Optional[str] = typer.Option(None, "--id")):
    """Copy a skill directory (or SKILL.md) into the global library, without activating it."""
    from alfrd.skills import add_global_skill
    import json

    if not global_library:
        raise typer.BadParameter("specify --global; project skills can be created directly in skills/<id>")
    try:
        result = add_global_skill(path, id=skill_id)
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(json.dumps(result))


def _skill_scope(project, global_library, activations=()):
    if activations and not project:
        raise typer.BadParameter("--activate requires an explicit --project PATH, including for --global")
    if bool(project) == global_library and not (activations and project and global_library):
        raise typer.BadParameter('Specify exactly one of --project PATH or --global')
    return Path(project).resolve() if project else Path.cwd(), 'global' if global_library else 'project'


def _activate_skill(root, result, scopes):
    if not scopes:
        return
    from alfrd.skills import activation_state, save_activations
    try:
        state = activation_state(root)
        additions = [{'source': result['origin'], 'id': result['id'], 'scope': scope} for scope in scopes]
        save_activations(root, {'version':1, 'activations':state['activations']+additions}, base_hash=state['hash'])
    except (ValueError, OSError) as exc:
        typer.echo(f'Skill saved; activation was not saved: {exc}', err=True)
        raise typer.Exit(1) from exc


def _skill_draft(content, file):
    import tempfile
    if file:
        return (sys.stdin.read() if str(file) == '-' else file.read_text(encoding='utf-8')), None
    fd, name = tempfile.mkstemp(prefix='alfrd-skill-', suffix='.md')
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(content)
    editor = os.environ.get('VISUAL') or os.environ.get('EDITOR')
    if not editor:
        raise typer.BadParameter(f'Set EDITOR or use --file; draft retained at {name}')
    import shlex
    try:
        subprocess.run([*shlex.split(editor), name], check=True)
        return Path(name).read_text(encoding='utf-8'), Path(name)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise typer.BadParameter(f'Editor failed; draft retained at {name}') from exc


@skills_cli.command('new')
def skills_new(skill_id: str, project: Optional[Path] = typer.Option(None, '--project'),
               global_library: bool = typer.Option(False, '--global'),
               name: Optional[str] = typer.Option(None, '--name'), description: str = typer.Option('Describe when to use this skill.', '--description'),
               file: Optional[Path] = typer.Option(None, '--file'),
               activate: list[str] = typer.Option([], '--activate')):
    """Create validated guidance, without activation. --file - reads stdin."""
    import json
    from alfrd.skills import create_skill
    root, origin = _skill_scope(project, global_library, activate)
    content = f'---\nname: {json.dumps(name or skill_id)}\ndescription: {json.dumps(description)}\n---\n# Instructions\n\nDescribe the action and expected result.\n'
    content, draft = _skill_draft(content, file)
    try:
        result = create_skill(root, skill_id, content, origin=origin)
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(f'{exc}' + (f'; draft retained at {draft}' if draft else '')) from exc
    if draft:
        draft.unlink()
    typer.echo(json.dumps(result))
    _activate_skill(root, result, activate)


@skills_cli.command('edit')
def skills_edit(skill_id: str, project: Optional[Path] = typer.Option(None, '--project'),
                global_library: bool = typer.Option(False, '--global'), origin: str = typer.Option('project', '--origin'),
                file: Optional[Path] = typer.Option(None, '--file')):
    """Edit SKILL.md using EDITOR or --file, preserving supporting files."""
    import json
    from alfrd.skills import read_skill_document, save_skill_document
    root, scope = _skill_scope(project, global_library)
    if scope == 'global':
        origin = scope
    draft = None
    try:
        opened = read_skill_document(root, skill_id, origin)
        if not opened['editable']:
            raise ValueError(opened['read_only_reason'])
        content, draft = _skill_draft(opened['content'], file)
        result = save_skill_document(root, skill_id, content, origin=origin, base_revision=opened['revision'])
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(f'{exc}' + (f'; draft retained at {draft}' if draft else '')) from exc
    if draft:
        draft.unlink()
    typer.echo(json.dumps(result))


@skills_cli.command('import')
def skills_import(url: Optional[str] = typer.Argument(None), project: Optional[Path] = typer.Option(None, '--project'),
                  global_library: bool = typer.Option(False, '--global'), skill_id: Optional[str] = typer.Option(None, '--id'),
                  preview_only: bool = typer.Option(False, '--preview-only'), json_output: bool = typer.Option(False, '--json'),
                  yes: bool = typer.Option(False, '--yes'), confirm: Optional[str] = typer.Option(None, '--confirm'),
                  from_skill: Optional[str] = typer.Option(None, '--from'),
                  activate: list[str] = typer.Option([], '--activate')):
    """Fetch and review public HTTPS guidance; --yes explicitly confirms after preview."""
    import json
    from alfrd.skill_imports import prepare_skill_import, commit_skill_import, persist_cli_preview, restore_cli_preview
    from alfrd.skills import read_skill_document
    root, origin = _skill_scope(project, global_library, activate)
    owner = f'cli:{os.getuid()}'
    try:
        if from_skill:
            source_origin, source_id = from_skill.split(':', 1)
            url = read_skill_document(root, source_id, source_origin).get('provenance', {}).get('source_url')
            if not url:
                raise ValueError('No valid provenance; enter a URL manually')
        if confirm:
            if url or preview_only or not skill_id:
                raise ValueError('--confirm requires --id and no URL/--preview-only')
            restore_cli_preview(root, confirm, owner=owner)
            result = commit_skill_import(root, confirm, origin, skill_id, owner=owner)
            persist_cli_preview(confirm, owner=owner)
            typer.echo(json.dumps(result))
            _activate_skill(root, result, activate)
            return
        if not url:
            raise ValueError('Provide a public HTTPS URL')
        preview = prepare_skill_import(root, url, owner=owner)
        persist_cli_preview(preview['token'], owner=owner)
        typer.echo(json.dumps(preview, indent=None if json_output else 2))
        if preview_only:
            return
        if not yes:
            if not sys.stdin.isatty():
                raise ValueError('Noninteractive imports need --preview-only or explicit --yes')
            if not typer.confirm('Save this reviewed skill?'):
                return
        result = commit_skill_import(root, preview['token'], origin, skill_id or preview['proposed_id'], owner=owner)
        persist_cli_preview(preview['token'], owner=owner)
        typer.echo(json.dumps(result))
        _activate_skill(root, result, activate)
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc




try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    else:
        sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
except:
    pass


def _serve_production(app, host: str, port: int) -> None:
    """Serve with waitress (a production WSGI server) instead of Flask's development server.

    Live updates are long-lived Server-Sent Event streams, one per open Studio tab, each holding a
    thread: hence many threads, output sent as soon as it is yielded, and a channel timeout above
    the 15 s ping. Falls back to Flask's server only when waitress is not installed.
    """
    try:
        from waitress import serve
    except ImportError:
        print("waitress is not installed (pip install waitress); using Flask's development server.")
        app.run(host=host, port=port, debug=False)
        return
    serve(app, host=host, port=port, threads=48, send_bytes=1, channel_timeout=300,
          connection_limit=200, asyncore_use_poll=True, ident="alfrd", _quiet=True)


def _open_studio_when_ready(url: str, stopped: threading.Event, probe_url: str | None = None) -> None:
    """Open ``url`` once the local HTTP server responds; stop if serving exits early.

    ``probe_url`` is the readiness check (the public ``/api/health``), so the
    access token in ``url`` is not spent on a probe.
    """
    from urllib.error import URLError
    from urllib.request import ProxyHandler, build_opener

    opener = build_opener(ProxyHandler({}))
    for _ in range(50):
        if stopped.wait(0.1):
            return
        try:
            with opener.open(probe_url or url, timeout=0.25) as response:
                ready = response.status == 200
        except (OSError, URLError):
            continue
        if ready and not stopped.is_set():
            try:
                if not webbrowser.open(url):
                    print("Could not open a browser; open the Studio URL above manually.")
            except Exception as error:
                print(f"Could not open a browser ({error}); open the Studio URL above manually.")
            return
    if not stopped.is_set():
        print("Browser launch timed out; open the Studio URL above manually.")


def _interrupt_self() -> None:
    """Stop this process as if Ctrl+C was pressed (used by the Studio's Quit button)."""
    import signal

    if os.name == "nt":  # no reliable self-SIGINT on Windows
        os._exit(0)
    os.kill(os.getpid(), signal.SIGINT)


def _reexec_server(token: str, secret_key: str, no_token: bool) -> None:
    """Settings → Plugins → Restart now: run the same command again in this process.

    The token and session secret travel in the environment, so the new server
    writes the same token file and the Studio's session cookie stays valid.
    Sockets are not inherited (PEP 446), so the port is free again.
    """
    os.environ["ALFRD_SECRET_KEY"] = secret_key
    if not no_token:
        os.environ["ALFRD_TOKEN"] = token
    argv = list(sys.orig_argv)
    if "--no-browser" not in argv:  # the Studio tab is already open
        argv.append("--no-browser")
    print("Restarting alfrd serve…", flush=True)
    sys.stderr.flush()
    os.execv(sys.executable, argv)


def _connect_startup_project(service, project: str | None) -> str | None:
    """Register the folder `alfrd serve` was started for (``--project`` or the cwd)."""
    from alfrd.manifest import ManifestError
    from alfrd.manifest_default import default_manifest_path, looks_like_project, register_project_folder
    from alfrd.studio_defs import manifest_file

    folder = _startup_folder(project)
    if project and not folder.is_dir():
        raise typer.BadParameter(f"{folder} is not a folder")
    local = manifest_file(folder) is not None
    # No local alfrd.yaml: use the default one for --project, or for a cwd that
    # looks like an AVICA folder (avica.inp, avica.logs/, reductions/) or is
    # completely empty (a new AVICA project started from scratch).
    if not local and (default_manifest_path() is None or not (project or looks_like_project(folder))):
        if project:
            raise typer.BadParameter(f"No alfrd.yaml in {folder} and no default alfrd.yaml")
        return None
    try:
        existing, used_default = register_project_folder(service, folder)
    except (ManifestError, OSError) as error:
        print(f"alfrd.yaml for {folder} was not loaded: {error}")
        return None
    if used_default:
        print(f"No alfrd.yaml in {folder}: using the default ({default_manifest_path()}). "
              "A local alfrd.yaml replaces it (`alfrd manifest default -o alfrd.yaml` starts one).")
    print(f"Project {existing.name}: {folder}")
    return existing.identifier


def _discover_startup_projects(service, folder: Path, depth: int = 2, max_dirs: int = 2000) -> list[tuple[str, Path]]:
    """Register every sub-folder of ``folder`` that has its own alfrd.yaml (depth <= ``depth``).

    Used when `alfrd serve` starts in a folder that is not itself a project
    (e.g. ``data_reductions/`` holding ``alma/alfrd.yaml`` and
    ``pipe_comparison/alfrd.yaml``). Returns ``[(identifier, folder)]``.
    """
    from alfrd.manifest import ManifestError
    from alfrd.manifest_default import discover_projects, register_project_folder

    folders, capped = discover_projects(folder, depth=depth, max_dirs=max_dirs)
    found: list[tuple[str, Path]] = []
    for sub in folders:
        try:
            existing, _ = register_project_folder(service, sub, allow_default=False)
        except (ManifestError, OSError) as error:
            print(f"alfrd.yaml for {sub} was not loaded: {error}")
            continue
        print(f"Project {existing.name}: {sub}")
        found.append((existing.identifier, sub))
    if capped:
        print(f"Stopped looking for projects after {max_dirs} folders under {folder}; "
              "use --discover-depth or --project to narrow it.")
    return found


def _startup_folder(project: str | None) -> Path:
    """The folder `alfrd serve` was started for (``--project`` or the cwd)."""
    folder = Path(project).expanduser().resolve() if project else Path.cwd().resolve()
    return folder.parent if folder.is_file() else folder


def _serve_web(host: str, port: int, debug: bool, runtime_db: str | None = None, no_browser: bool = False,
               demo: bool = False, project: str | None = None, all_projects: bool = False,
               live_interval: float = 2.0, discover: bool = True, discover_depth: int = 2,
               token: str | None = None, no_token: bool = False, gui_install: bool = True,
               plugin_actions: bool = True, experimental: bool = False) -> None:
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
    if no_token and not loopback:
        raise typer.BadParameter("--no-token is only available on a loopback interface.")
    if no_token:
        typer.echo("Warning: --no-token disables access-token protection; any local user can access this server.", err=True)
    elif not loopback:
        typer.echo("Warning: without TLS, the access token travels in clear text. Use an HTTPS reverse proxy or ssh -L.", err=True)
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
        # --no-gui-install: Settings → Plugins can't install/update/remove (plugins.json may also say so).
        "PLUGINS_GUI_INSTALL": gui_install,
        # --no-plugin-actions: plugins' mutating project actions are refused (plugins.json may also say so).
        "PLUGINS_ACTIONS": plugin_actions,
        # --experimental-features: Chat mode (Settings → Preferences) and the agent-loop template.
        "STUDIO_EXPERIMENTAL": experimental,
    }
    from alfrd.manifest_default import local_manifest

    start = _startup_folder(project)
    discovered: list[tuple[str, Path]] = []
    # Not a project itself: register the sub-folders that have their own alfrd.yaml.
    if (discover and not project and not config["STUDIO_DEFAULT_PROJECT"]
            and local_manifest(start) is None and discover_depth > 0):
        discovered = _discover_startup_projects(service, start, depth=discover_depth)
        if discovered:
            config["STUDIO_DEFAULT_PROJECT"] = discovered[0][0]
    # Studio settings → Rediscover registers these folders again after a Forget.
    config["STUDIO_START_FOLDER"] = str(start) if config["STUDIO_DEFAULT_PROJECT"] and not discovered else None
    config["STUDIO_DISCOVERED_FOLDERS"] = [str(f) for _, f in discovered]
    # Started for one project (or a parent folder of several): the Studio shows
    # only those (unless --all-projects).
    scope = [i for i, _ in discovered] or ([config["STUDIO_DEFAULT_PROJECT"]] if config["STUDIO_DEFAULT_PROJECT"] else [])
    config["STUDIO_PROJECTS"] = None if all_projects or not scope else scope
    if not scope:
        # Identifiers are location keys; print the alfrd.yaml names.
        others = [p.name for p in service.list_projects()]
        if others:
            print(f"No alfrd.yaml here. Showing projects remembered in {database}: {', '.join(others)}.")
    from alfrd.gui import auth

    # One token and session secret per server. Under --debug the reloader
    # re-runs this function in a child process: the environment carries both
    # over, so the printed link and the cookies stay valid.
    token = token or os.environ.get("ALFRD_TOKEN") or auth.new_token()
    secret_key = os.environ.get("ALFRD_SECRET_KEY") or secrets.token_hex(32)
    if debug:
        os.environ["ALFRD_TOKEN"] = token
        os.environ["ALFRD_SECRET_KEY"] = secret_key
    config["ACCESS_TOKEN"] = token
    config["ACCESS_TOKEN_REQUIRED"] = not no_token
    config["SECRET_KEY"] = secret_key
    # Cookies ignore the port: two servers on one host need different names.
    config["SESSION_COOKIE_NAME"] = f"alfrd-session-{port}"
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    authority = f"[{browser_host}]" if ":" in browser_host else browser_host
    base = f"http://{authority}:{port}"
    query = "" if no_token else "?" + urlencode({"token": token})
    url = f"{base}/studio/{query}"
    print(f"ALFRD Studio: {url}")
    app = create_app(config)
    # Only the process that serves writes the token file (not the reloader parent).
    serving = not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    if serving:
        auth.write_server_file(port, f"{base}/studio/", None if no_token else token, secret_key)
        _start_plugin_services(database)
    _reconcile_plans_later(service, config.get("STUDIO_PROJECTS"), spawn=loopback)
    stopped = threading.Event()
    if serving:
        _chat_watch_later(service, config.get("STUDIO_PROJECTS"), stopped, spawn=loopback)
        _chat_schedule_later(service, config.get("STUDIO_PROJECTS"), stopped, spawn=loopback)
    restart = threading.Event()
    browser_thread = None
    if not no_browser and (loopback or host in {"0.0.0.0", "::"}) and serving:
        browser_thread = threading.Thread(
            target=_open_studio_when_ready, args=(url, stopped, f"{base}/api/health"), daemon=True,
        )
        browser_thread.start()
    try:
        config_map = getattr(app, "config", None)
        if not debug and isinstance(config_map, dict):
            # Settings → Quit in the Studio: same as Ctrl+C in this terminal.
            config_map["STUDIO_SHUTDOWN"] = _interrupt_self
            if os.name != "nt":  # Windows has no self-SIGINT to stop cleanly before the re-exec
                config_map["STUDIO_RESTART"] = lambda: (restart.set(), _interrupt_self())
            if threading.current_thread() is threading.main_thread():
                import signal

                # Background jobs (`alfrd serve &`, nohup) start with SIGINT ignored.
                signal.signal(signal.SIGINT, signal.default_int_handler)
            print("Press Ctrl+C (or Quit in the Studio) to stop.")
        try:
            if debug:
                app.run(host=host, port=port, debug=True)  # Flask's development server: reloader, debugger
            else:
                _serve_production(app, host, port)
        except KeyboardInterrupt:
            pass
        print("alfrd serve stopped.")
    finally:
        stopped.set()
        if browser_thread is not None:
            browser_thread.join(timeout=1)
        if serving:
            from alfrd.extensions import services

            services.supervisor.stop_all()
            auth.remove_server_file(port)
    if restart.is_set():
        _reexec_server(token, secret_key, no_token)


def _start_plugin_services(database: Path) -> None:
    """Plugin services run against this server's database; start the ones set to start with it."""
    from alfrd import extensions
    from alfrd.extensions import services

    services.supervisor.env["ALFRD_RUNTIME_DB"] = str(database)
    if extensions.safe_mode():
        return
    try:
        started = services.supervisor.autostart(
            [(r.id, r.plugin) for r in extensions.loaded() if r.plugin is not None and extensions.active(r)])
    except Exception as exc:  # noqa: BLE001 - a plugin service never stops the server
        print(f"Plugin services not started: {exc}")
        return
    for key in started:
        print(f"Started plugin service {key} (log: Settings → Plugins).")


def _reconcile_plans_later(service, scope, *, spawn: bool) -> None:
    """Re-attach to `alfrd plan` runs whose runner stopped (server restart, crash, reboot)."""

    def work() -> None:
        try:
            from alfrd.runtime import scheduler

            for item in service.list_projects():
                if scope and item.identifier not in scope:
                    continue
                root = Path(item.root_path)
                if not (root / ".alfrd" / "plans").is_dir():
                    continue
                for action in scheduler.reconcile(root, spawn=spawn):
                    print(f"Plan {action['plan']} ({item.name}): {action['action']}")
        except Exception as error:  # noqa: BLE001 - never block the server on this
            print(f"Plan reconcile skipped: {error}")

    threading.Thread(target=work, name="alfrd-plan-reconcile", daemon=True).start()


def _chat_schedule_later(service, scope, stopped: threading.Event, *, spawn: bool):
    """Independent one-second clock, including a startup catch-up and dynamic catalogue scope."""
    from alfrd import chat

    def work():
        while not stopped.is_set():
            try:
                roots = {Path(p.root_path).resolve() for p in service.list_projects()
                         if not scope or p.identifier in scope}
                for root in roots:
                    if stopped.is_set():
                        break
                    for result in chat.dispatch_due_schedules(root, spawn=spawn):
                        if result.get("error"):
                            print(f"Chat schedule skipped ({root}): {result['error']}")
            except Exception as error:
                print(f"Chat schedule sweep failed: {error}")
            stopped.wait(1)

    thread = threading.Thread(target=work, name="alfrd-chat-schedule", daemon=True)
    thread.start()
    return thread


def _chat_watch_later(service, scope, stopped: threading.Event, *, spawn: bool):
    """Check scoped projects until the server stops.

    Settings are re-read within 30 seconds, including projects initially disabled.
    """
    from alfrd import chat

    roots = {}
    try:
        projects = service.list_projects()
    except Exception as error:  # noqa: BLE001 - catalogue recovery must not block serving
        print(f"Chat checks not started: {error}")
        return None
    for item in projects:
        if scope and item.identifier not in scope:
            continue
        root = Path(item.root_path)
        try:
            roots[root] = chat.settings(root)["watch"]["interval_seconds"]
        except Exception as error:  # noqa: BLE001 - one project's settings must not stop the server
            print(f"Chat check skipped ({root}): {error}")
    if not roots:
        return None

    def work() -> None:
        import time

        last = {}
        while not stopped.is_set():
            delay = 30.0
            for root in roots:
                if stopped.is_set():
                    break
                try:
                    watch = chat.settings(root)["watch"]
                    interval = watch["interval_seconds"]
                    if not watch.get("enabled", True) or not interval:
                        continue
                    now = time.monotonic()
                    remaining = last.get(root, now - interval) + interval - now
                    if remaining <= 0:
                        # Advance even if checking fails; retry on the next scheduled interval.
                        last[root] = now
                        chat.check(root, spawn=spawn, periodic=True)
                        remaining = interval
                    delay = min(delay, remaining)
                except Exception as error:  # noqa: BLE001 - a failed check must not kill the watcher
                    print(f"Chat check skipped ({root}): {error}")
            stopped.wait(delay)

    thread = threading.Thread(target=work, name="alfrd-chat-watch", daemon=True)
    thread.start()
    return thread


@alfrd_cli.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(5000, min=1, max=65535, help="TCP port to bind."),
    debug: bool = typer.Option(False, help="Enable Flask development debugging."),
    runtime_db: Optional[str] = typer.Option(
        None, help="Path to the runtime SQLite database that backs matrix routes."
    ),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open the Studio in a browser."),
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
    discover: bool = typer.Option(
        True, "--discover/--no-discover",
        help="When the folder is not a project, open every sub-folder that has its own alfrd.yaml.",
    ),
    discover_depth: int = typer.Option(
        2, "--discover-depth", min=0, help="How many folder levels --discover searches below the start folder.",
    ),
    token: Optional[str] = typer.Option(None, "--token", envvar="ALFRD_TOKEN", help="Use a fixed server access token."),
    no_token: bool = typer.Option(False, "--no-token", help="Disable access-token protection (loopback only)."),
    safe_mode: bool = typer.Option(False, "--safe-mode", help="Start without plugins (same as ALFRD_NO_PLUGINS=1)."),
    no_gui_install: bool = typer.Option(
        False, "--no-gui-install", help="Don't install, update or remove plugins from the Studio (CLI only).",
    ),
    no_plugin_actions: bool = typer.Option(
        False, "--no-plugin-actions", help="Refuse plugin project actions that change files or external data.",
    ),
    experimental_features: bool = typer.Option(
        False, "--experimental-features", envvar="ALFRD_EXPERIMENTAL_FEATURES",
        help="Show experimental Studio features: the Chat mode preference and the agent-loop project template.",
    ),
):
    """Serve ALFRD Studio, backed by the runtime database."""

    if safe_mode:
        os.environ["ALFRD_NO_PLUGINS"] = "1"  # also reaches the debug reloader's child
    _serve_web(host, port, debug, runtime_db, no_browser, demo, project, all_projects, live_interval=live_interval,
               discover=discover, discover_depth=discover_depth, token=token, no_token=no_token,
               gui_install=not no_gui_install, plugin_actions=not no_plugin_actions,
               experimental=experimental_features)


@alfrd_cli.command()
def gui(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(5000, min=1, max=65535, help="TCP port to bind."),
    debug: bool = typer.Option(False, help="Enable Flask development debugging."),
    runtime_db: Optional[str] = typer.Option(
        None, help="Path to the runtime SQLite database that backs matrix routes."
    ),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open the Studio in a browser."),
    token: Optional[str] = typer.Option(None, "--token", envvar="ALFRD_TOKEN", help="Use a fixed server access token."),
    no_token: bool = typer.Option(False, "--no-token", help="Disable access-token protection (loopback only)."),
    safe_mode: bool = typer.Option(False, "--safe-mode", help="Start without plugins (same as ALFRD_NO_PLUGINS=1)."),
    no_gui_install: bool = typer.Option(
        False, "--no-gui-install", help="Don't install, update or remove plugins from the Studio (CLI only).",
    ),
    no_plugin_actions: bool = typer.Option(
        False, "--no-plugin-actions", help="Refuse plugin project actions that change files or external data.",
    ),
    experimental_features: bool = typer.Option(
        False, "--experimental-features", envvar="ALFRD_EXPERIMENTAL_FEATURES",
        help="Show experimental Studio features: the Chat mode preference and the agent-loop project template.",
    ),
):
    """Alias for ``alfrd serve``."""

    if safe_mode:
        os.environ["ALFRD_NO_PLUGINS"] = "1"
    _serve_web(host, port, debug, runtime_db, no_browser, token=token, no_token=no_token,
               gui_install=not no_gui_install, plugin_actions=not no_plugin_actions,
               experimental=experimental_features)


@alfrd_cli.command()
def url(port: int = typer.Option(5000, min=1, max=65535, help="Port of the running server.")):
    """Print the access link for a running alfrd serve."""
    from alfrd.gui.auth import read_server_file

    data = read_server_file(port)
    if not data or not isinstance(data.get("url"), str) or not data["url"]:
        typer.echo(f"No alfrd serve found on port {port}", err=True)
        raise typer.Exit(1)
    token = data.get("token")
    typer.echo(data["url"] + ("?" + urlencode({"token": token}) if token else ""))


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
        threading.Thread(target=_open_studio_when_ready, args=(url, stopped), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set()
        httpd.server_close()


projects_cli = typer.Typer(help="Projects remembered in the runtime database (used by `alfrd serve`).")
alfrd_cli.add_typer(projects_cli, name="projects")


@projects_cli.command("create")
def projects_create(
    path: str = typer.Argument(..., help="New or existing project folder."),
    name: Optional[str] = typer.Option(None, "--name"),
    template: str = typer.Option("basic", "--template"),
    task: str = typer.Option("", "--task", help="Initial agent task."),
    task_file: Optional[str] = typer.Option(None, "--task-file", help="Read the initial task from Markdown."),
    iterations: Optional[int] = typer.Option(None, "--iterations", min=1, max=2 * MAX_ITERATIONS,
                                             help="Agent loops: total turns (the project maximum)."),
    sequence: Optional[str] = typer.Option(None, "--sequence", help="Agent loops: comma-separated agents for one pass, e.g. claude,claude,codex."),
    config_location: str = typer.Option(".alfrd", "--config-location", help="Where alfrd.yaml goes: .alfrd (.alfrd/alfrd.yaml, default) or root (alfrd.yaml; lower precedence)."),
    db: Optional[str] = typer.Option(None, "--db", help="Runtime SQLite database."),
):
    """Scaffold and register a project without starting its commands."""
    from alfrd.project_creation import create_project
    from alfrd.manifest_paths import MANIFEST_FILENAME, NESTED_MANIFEST

    if config_location not in (".alfrd", "root"):
        raise typer.BadParameter("use .alfrd or root", param_hint="--config-location")

    try:
        from alfrd.studio_defs import template_path
        import yaml
        source = template_path(template)
        if source is None:
            raise ValueError(f"unknown project template: {template}")
        definition = yaml.safe_load(source.read_text())
        looping = any(w.get("repeat") for w in definition.get("workflows", []))
        if iterations is not None and not looping:
            print("warning: --iterations is ignored for a template without a loop")
        if task_file:
            task = Path(task_file).read_text(encoding="utf-8")
        agents = [a.strip() for a in sequence.split(",") if a.strip()] if sequence else None
        project, _ = create_project(_runtime_service(db), path, name=name, template=template,
                                    task=task, iterations=DEFAULT_ITERATIONS if iterations is None else iterations,
                                    sequence=agents, manifest=NESTED_MANIFEST if config_location == ".alfrd" else MANIFEST_FILENAME)
    except (ValueError, OSError) as error:
        print(f"error: {error}")
        raise typer.Exit(1)
    print(f"Created {project.name}: {project.root_path}\n{project.identifier}")


@projects_cli.command("list")
def projects_list(db: Optional[str] = typer.Option(None, "--db", help="Path to the runtime SQLite database.")):
    """List remembered projects and their folders."""
    service = _runtime_service(db)
    projects = service.list_projects()
    if not projects:
        print("No projects remembered.")
    counts = {p.name: sum(other.name == p.name for other in projects) for p in projects}
    for p in projects:
        exists = "" if Path(p.root_path).is_dir() else "  (folder missing)"
        label = f"{p.name} ({Path(p.root_path).name})" if counts[p.name] > 1 else p.name
        print(f"{label}\t{p.root_path}\t{p.identifier}{exists}")


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
        project = service.get_project_by_selector(name)
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


@manifest_cli.command("default")
def manifest_default(
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write it to this file (default: print)."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
):
    """Print (or write) the default alfrd.yaml used for folders without one."""
    from alfrd.manifest_default import ENV_VAR, default_manifest_path, default_manifest_text

    path = default_manifest_path()
    if path is None:
        print(f"No default alfrd.yaml (check ${ENV_VAR}).")
        raise typer.Exit(code=1)
    if output is None:
        print(path.read_text(encoding="utf-8"), end="")
        return
    target = Path(output).expanduser()
    if target.is_dir():
        from alfrd.manifest_paths import new_manifest_path

        target = new_manifest_path(target)  # the active manifest, else .alfrd/alfrd.yaml (never a shadowed file)
    if target.exists() and not force:
        raise typer.BadParameter(f"{target} exists (use --force to overwrite)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(default_manifest_text(target.resolve().parent) or "", encoding="utf-8")
    from alfrd.manifest_paths import project_manifest

    root = _manifest_root(target.resolve())
    if target.name == "alfrd.yaml" and project_manifest(root) == target.resolve():
        from alfrd import history

        try:
            history.record(root, "alfrd.yaml", source="cli", message="alfrd manifest default")
        except (history.HistoryError, OSError):
            pass
    print(f"Wrote {target} (from {path}).")


@manifest_cli.command("migrate")
def manifest_migrate(
    path: str = typer.Argument(".", help="Project folder (or its alfrd.yaml)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would change; write nothing."),
    keep: str = typer.Option("rename", "--keep", help="rename: the old file becomes <name>.migrated; shadow: leave it (ignored, lower precedence)."),
    force: bool = typer.Option(False, "--force", help="Replace a different .alfrd/alfrd.yaml (kept as .alfrd/alfrd.yaml.bak)."),
):
    """Move alfrd.yaml into .alfrd/alfrd.yaml (copy, validate, rename the old file; nothing is deleted)."""
    from alfrd import manifest_migrate as migrate
    from alfrd.manifest_paths import project_root

    target = Path(path).expanduser().resolve()
    root = project_root(target) if target.is_file() else target
    try:
        info = migrate.plan(root, keep)
        print(f"{info['status']}: {info['source'] or '-'} -> {info['target']}" + (f" (old file -> {info['rename_to']})" if info["rename_to"] else ""))
        if info["diff"]:
            print(info["diff"], end="")
        if info["error"]:
            print(f"error: {info['error']}")
        if dry_run or info["status"] in ("nothing", "no_manifest"):
            raise typer.Exit(0 if info["status"] != "no_manifest" and not info["error"] else 1)
        migrate.apply(root, keep, force)
    except migrate.MigrateConflict:
        print("conflict: .alfrd/alfrd.yaml differs; rerun with --force to replace it")
        raise typer.Exit(1)
    except (migrate.MigrateError, ValueError, OSError) as error:
        print(f"error: {error}")
        raise typer.Exit(1)
    print(f"Moved to {root / info['target']}")


@import_cli.command("avica-run")
def import_avica_run_command(
    reductions_dir: str = typer.Argument(..., help="AVICA target_dir containing result__<target>__<code>__<wd>.csv (or *_result.csv) files."),
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
            project_root=_manifest_root(document.path) if document.path else Path(manifest).parent,
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


# ---------------------------------------------------------------------------
# alfrd targets: the project's target list (alfrd.targets.csv)

targets_cli = typer.Typer(help="The project's target list (target, FITS file names, project code), shared by the Studio and plans.")
alfrd_cli.add_typer(targets_cli, name="targets")


@targets_cli.command("show")
def targets_show(root: str = typer.Option(".", "--root", "-C", help="Project folder (contains alfrd.yaml).")):
    """Print the targets file."""
    from alfrd import targets_csv as tc
    from alfrd.execution import ExecutionError

    try:
        spec = tc.load_spec(root)
        table = tc.read(spec)
    except (ExecutionError, ValueError, OSError) as error:
        print(f"error: {error}")
        raise typer.Exit(code=1)
    if not spec.csv.is_file():
        print(f"{spec.rel}: not created yet (alfrd targets import FILE)")
        return
    print(f"{spec.rel}: {len(table['rows'])} target(s)")
    for row in table["rows"]:
        print(f"  {row['target']}{'@' + row['code'] if row['code'] else ''}  {row['files'] or '-'}")


@targets_cli.command("import")
def targets_import(
    csv_file: str = typer.Argument(..., help="CSV/TSV with a target column and FITS file names (and optionally a project code)."),
    root: str = typer.Option(".", "--root", "-C", help="Project folder (contains alfrd.yaml)."),
    replace: bool = typer.Option(False, "--replace", help="Replace the targets file instead of merging into it."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only show what would change."),
):
    """Merge (or replace) rows into the targets file. Header names are matched with alfrd.yaml targets.columns."""
    from alfrd import targets_csv as tc
    from alfrd.execution import ExecutionError

    mode = "replace" if replace else "merge"
    try:
        spec = tc.load_spec(root)
        text = Path(csv_file).read_text(encoding="utf-8-sig")
        result = tc.preview(spec, text, mode) if dry_run else tc.save(spec, text, mode)
    except (ExecutionError, ValueError, OSError) as error:
        print(f"error: {error}")
        raise typer.Exit(code=1)
    for problem in result.get("problems") or []:
        print(f"  line {problem['line']}: {problem['message']}")
    counts = ", ".join(f"{len(result[k])} {k}" for k in ("added", "updated", "unchanged", "removed") if result[k])
    print(f"{spec.rel}: {counts or 'no change'}{' (dry run)' if dry_run else ''}")


@targets_cli.command("remove")
def targets_remove(
    names: list[str] = typer.Argument(..., help="Target names (every row of it), or TARGET@CODE for one row."),
    root: str = typer.Option(".", "--root", "-C", help="Project folder (contains alfrd.yaml)."),
):
    """Remove targets from the targets file. Result CSVs and work dirs are not touched."""
    from alfrd import targets_csv as tc
    from alfrd.execution import ExecutionError

    try:
        spec = tc.load_spec(root)
        result = tc.remove(spec, names)
    except (ExecutionError, ValueError, OSError) as error:
        print(f"error: {error}")
        raise typer.Exit(code=1)
    if result["missing"]:
        print(f"  not in {spec.rel}: {', '.join(result['missing'])}")
    print(f"{spec.rel}: {len(result['removed'])} row(s) removed")
    if not result["removed"]:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# alfrd plan: run a plan CSV (targets x steps) with the commands in alfrd.yaml

plan_cli = typer.Typer(help="Plan and run workflow steps per target from a plan CSV (keeps running after the server stops).")
alfrd_cli.add_typer(plan_cli, name="plan")

_ROOT_OPT = typer.Option(".", "--root", "-C", help="Project folder (contains alfrd.yaml).")


def _plan_fail(error: Exception) -> None:
    print(f"error: {error}")
    raise typer.Exit(code=1)


def _plan_steps(cfg, steps: Optional[str], first: Optional[str], last: Optional[str]) -> list[str]:
    order = cfg.step_ids
    if steps:
        chosen = [s.strip() for s in steps.split(",") if s.strip()]
        unknown = [s for s in chosen if s not in order]
        if unknown:
            raise ValueError(f"unknown step(s) {', '.join(unknown)}; steps are {', '.join(order)}")
        return chosen
    lo = order.index(first) if first else 0
    hi = order.index(last) if last else len(order) - 1
    return order[lo:hi + 1]


@plan_cli.command("new")
def plan_new(
    root: str = _ROOT_OPT,
    targets: Optional[str] = typer.Option(None, "--targets", "-t", help="Comma-separated target names."),
    from_csv: Optional[str] = typer.Option(None, "--from-csv", help="Dataset table with the key column (TARGET_NAME), FILENAMES, PROJECT_CODE."),
    from_results: bool = typer.Option(False, "--from-results", help="Every target (and project code) found in result CSVs."),
    from_targets: bool = typer.Option(False, "--from-targets", help="Every row of the targets CSV (alfrd.yaml targets.csv, default alfrd.targets.csv)."),
    files: Optional[str] = typer.Option(None, "--files", "-f", help="FITS file names for --targets (comma-separated)."),
    steps: Optional[str] = typer.Option(None, "--steps", help="Steps to mark todo (comma-separated)."),
    first: Optional[str] = typer.Option(None, "--from", help="First step to mark todo."),
    last: Optional[str] = typer.Option(None, "--to", help="Last step to mark todo."),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Plan CSV (default: execution.plan_csv)."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing plan CSV."),
):
    """Write a plan CSV: one row per target, one column per step (todo / empty)."""
    import csv as _csv

    from alfrd.execution import ExecutionError, load_execution
    from alfrd.runtime import plan_csv as pc

    try:
        cfg = load_execution(root)
        chosen = _plan_steps(cfg, steps, first, last)
        rows: list[dict] = []
        if targets:
            rows += [{"target": t.strip(), "files": files or ""} for t in targets.split(",") if t.strip()]
        if from_csv:
            with open(from_csv, newline="", encoding="utf-8-sig") as stream:
                for row in _csv.DictReader(stream):
                    lower = {k.lower(): v for k, v in row.items() if k}
                    name = lower.get(cfg.key_column.lower()) or lower.get("target_name") or lower.get("target")
                    if name:
                        rows.append({"target": name.strip(), "files": lower.get(cfg.files_column.lower(), "") or "",
                                     "code": lower.get(cfg.code_column.lower(), "") or ""})
        if from_targets:
            from alfrd import targets_csv as tc

            spec = tc.load_spec(cfg.root)
            if not spec.csv.is_file():
                raise ValueError(f"{spec.rel} does not exist (create it with `alfrd targets import FILE`)")
            rows += [{"target": r["target"], "files": r["files"], "code": r["code"]} for r in tc.read(spec)["rows"]]
        if from_results:
            from alfrd.avica_layout import scan_layout

            seen = {(r["target"], r.get("code", "")) for r in rows}
            for item in scan_layout(cfg.root)["result_csvs"]:
                key = (item["target"], item.get("project_code", ""))
                if item["target"] and key not in seen:
                    seen.add(key)
                    rows.append({"target": item["target"], "code": item.get("project_code", ""), "workdir": item.get("workdir", "")})
        if not rows:
            raise ValueError("no targets: use --targets, --from-csv, --from-targets or --from-results")
        out = Path(output) if output else cfg.plan_csv
        if out.exists() and not force:
            raise ValueError(f"{out} exists (use --force to overwrite)")
        pc.create(out, rows, cfg.step_ids, chosen, key_column=cfg.key_column, files_column=cfg.files_column,
                  code_column=cfg.code_column, workdir_column=cfg.workdir_column)
    except (ExecutionError, ValueError, OSError) as error:
        _plan_fail(error)
    missing = [r["target"] for r in rows if not r.get("files")]
    print(f"{out}: {len(rows)} target(s), steps {', '.join(chosen)} marked todo.")
    if missing and any("{" + cfg.files_column + "}" in " ".join(s.argv or ()) for s in cfg.steps):
        print(f"Fill the {cfg.files_column} column for: {', '.join(missing[:10])}{' ...' if len(missing) > 10 else ''}")


@plan_cli.command("add-row")
def plan_add_row(
    csv_file: Optional[str] = typer.Argument(None, help="Plan CSV (default: execution.plan_csv)."),
    root: str = _ROOT_OPT,
    target: str = typer.Option(..., "--target", "-t", help="Target name."),
    files: str = typer.Option("", "--filenames", "-f", help="FITS file names (comma, space or newline separated)."),
    code: str = typer.Option("", "--project-code", help="Project code."),
    workdir: str = typer.Option("", "--workdir", help="Work dir (leave empty: AVICA picks one)."),
    steps: Optional[str] = typer.Option(None, "--steps", help="Steps to mark todo (default: all step columns in the file)."),
):
    """Append one row to an existing plan CSV, without touching or rebuilding the rest of the plan.

    Safe while the plan runs: a live runner re-reads the CSV before each unit,
    the same as a hand edit. A paused or finished plan runs the row after
    `alfrd plan resume`. The CSV must exist (create one with `alfrd plan new`).
    """
    from alfrd.execution import ExecutionError, load_execution
    from alfrd.runtime import plan_csv as pc
    from alfrd.runtime import scheduler

    try:
        cfg = load_execution(root)
        path = scheduler.resolve_csv(cfg, csv_file)
        if not path.exists():
            raise ExecutionError(f"plan CSV {path} does not exist (create one with `alfrd plan new`)")
        plan = scheduler.active_plan(cfg.root, path)
        chosen = _plan_steps(cfg, steps, None, None) if steps else None
        table = scheduler.add_row(cfg, path, target=target, files=pc.join_files(files), code=code, workdir=workdir,
                                  selected=chosen, steps=(plan or {}).get("steps") or cfg.step_ids)
    except (ExecutionError, ValueError, OSError) as error:
        _plan_fail(error)
    print(f"{path}: added {pc.row_key(target.strip(), code.strip())} ({len(table.rows)} row(s) total).")


def _print_preview(result: dict) -> None:
    print(f"plan {result['csv']} · mode {result['mode']} · cwd {result['cwd']}")
    if result["missing_step_columns"]:
        print(f"  (no column, not run: {', '.join(result['missing_step_columns'])})")
    for unit in result["units"]:
        label = f"{unit['target'] or '*'}{'@' + unit['code'] if unit.get('code') else ''} · {', '.join(unit['steps'])}"
        if unit.get("error"):
            print(f"  ✗ {label}: {unit['error']}")
        else:
            import shlex

            print(f"  • {label}\n      $ {shlex.join(unit['argv'])}")
    if not result["units"]:
        print("  nothing to do (no todo cells)")


@plan_cli.command("run")
def plan_run(
    csv_file: Optional[str] = typer.Argument(None, help="Plan CSV (default: execution.plan_csv)."),
    root: str = _ROOT_OPT,
    detach: bool = typer.Option(True, "--detach/--foreground", help="Run in the background (default) or in this terminal."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only print the commands, in order."),
    mode: Optional[str] = typer.Option(None, "--mode", help="step | target | batch (default: execution.mode)."),
    concurrency: Optional[int] = typer.Option(None, "--concurrency", "-j", help="Targets at once."),
    on_failure: Optional[str] = typer.Option(None, "--on-failure", help="stop_target | continue | stop_plan."),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Set failed/blocked/interrupted cells back to todo first."),
    target: Optional[str] = typer.Option(None, "--target", help="Agent loops: the task to run."),
    treatment: str = typer.Option("baseline", "--treatment", help="Label recorded on every turn (baseline report)."),
    run_kind: str = typer.Option("production", "--run-kind", help="production | experiment | … (baseline report)."),
    retry_of: Optional[str] = typer.Option(None, "--retry-of", help="Earlier plan whose unaccepted turns this plan retries."),
    at: Optional[str] = typer.Option(None, "--at", help='Start later: "02:00" (the next 02:00) or "2026-10-07 02:00" (at most 7 days ahead).'),
):
    """Start a plan: run every todo cell with its step command (now, or --at a time)."""
    from alfrd.execution import ExecutionError
    from alfrd.runtime import scheduler

    try:
        if dry_run:
            _print_preview(scheduler.preview(root, csv_file, mode=mode, on_failure=on_failure))
            return
        folder = scheduler.create_plan(root, csv_file, mode=mode, concurrency=concurrency,
                                       on_failure=on_failure, retry_failed=retry_failed, target=target,
                                       treatment=treatment, run_kind=run_kind, retry_of=retry_of, start_at=at)
    except (ExecutionError, OSError, ValueError) as error:
        _plan_fail(error)
    if folder.load().get("workspace_warning"):
        print(f"warning: {folder.load()['workspace_warning']}")
    if detach:
        pid = scheduler.spawn_runner(folder)
        starts = folder.load().get("start_at")
        print(f"plan {folder.id} {'scheduled for ' + starts.replace('T', ' ') if starts else 'started'} (runner pid {pid}). "
              "It keeps running when you close this terminal or the Studio.")
        print(f"  alfrd plan status -C {folder.root}      alfrd plan pause|cancel {folder.id} -C {folder.root}")
        return
    raise typer.Exit(code=scheduler.Runner(folder.root, folder.id).run())


@plan_cli.command("turn")
def plan_turn_command(
    plan_id: str = typer.Argument(..., help="A plan, usually running."),
    step: str = typer.Argument(..., help="Step id, e.g. t003-claude."),
    root: str = _ROOT_OPT,
    review: Optional[bool] = typer.Option(None, "--review/--no-review", help="Hold this turn's handoff for human review."),
    manual: Optional[bool] = typer.Option(None, "--manual/--agent", help="A person writes this turn (chat) instead of the agent."),
    after: Optional[str] = typer.Option(None, "--after", help='Delay after the previous step (+1h, 90m), a clock time ("02:00", "2026-10-07 02:00"); 0 runs it now.'),
    model: Optional[str] = typer.Option(None, "--model", help="Model for this turn."),
    clear: bool = typer.Option(False, "--clear", help="Drop this plan's overrides for the step."),
):
    """Change one step of a plan while it runs (review, human turn, delay, model)."""
    import json as _json

    from alfrd.execution import ExecutionError
    from alfrd.runtime import scheduler

    changes = {k: v for k, v in {"human_review": review, "manual": manual, "after": after, "model": model}.items() if v is not None}
    if clear:
        changes = {k: None for k in scheduler.OVERRIDE_KEYS}
    try:
        current = scheduler.set_override(root, plan_id, step, changes)
    except (ExecutionError, OSError) as error:
        _plan_fail(error)
    print(_json.dumps(current.get(step, {})))


@plan_cli.command("reject")
def plan_reject_command(
    plan_id: str = typer.Argument(...),
    unit_id: str = typer.Argument(..., help="The turn awaiting review."),
    reason: str = typer.Option("", "--reason"),
    root: str = _ROOT_OPT,
):
    """Refuse a handoff held for review: nothing is published; the plan stops like a failed turn."""
    from alfrd.agent_loop import reject_response

    try:
        reject_response(Path(root).resolve(), plan_id, unit_id, reason)
    except (ValueError, OSError) as error:
        _plan_fail(error)
    print("rejected")


@plan_cli.command("baseline")
def plan_baseline_command(
    root: str = _ROOT_OPT,
    start: Optional[str] = typer.Option(None, "--from", help="Starting boundary: a logical turn id or plan/unit address."),
    count: int = typer.Option(10, "--count", min=1, max=1000, help="Logical turns to include."),
    exclude: list[str] = typer.Option([], "--exclude", help="TURN_ID=evidence of an infrastructure outage (repeatable)."),
    coverage: float = typer.Option(0.80, "--coverage", min=0.0, max=1.0, help="Minimum share of attempts reporting a field."),
    as_json: bool = typer.Option(False, "--json"),
):
    """Baseline report over consecutive production turns: usage, retries, truncation, acceptance. Read-only."""
    import json as _json

    from alfrd import baseline

    exclusions = {}
    for item in exclude:
        turn, sep, evidence = item.partition("=")
        if not sep or not turn.strip() or not evidence.strip():
            _plan_fail(ValueError("--exclude needs TURN_ID=evidence"))
        exclusions[turn.strip()] = evidence.strip()
    try:
        doc = baseline.report(root, start=start, count=count, exclude=exclusions, coverage=coverage)
    except (ValueError, OSError) as error:
        _plan_fail(error)
    print(_json.dumps(doc, indent=2) if as_json else baseline.markdown(doc))
    raise typer.Exit(code=0 if doc["readiness"]["ready"] else 1)


@plan_cli.command("response")
def plan_response_command(
    plan_id: str = typer.Argument(..., help="Plan awaiting a manual response."),
    unit_id: str = typer.Argument(..., help="Waiting unit id."),
    response_file: Path = typer.Argument(..., help="Markdown response file."),
    root: str = _ROOT_OPT,
):
    """Validate and submit a manual response; rejected responses keep waiting."""
    from alfrd.agent_loop import submit_response

    try:
        submit_response(Path(root), plan_id, unit_id, response_file.read_text(encoding="utf-8"))
    except (ValueError, OSError) as error:
        _plan_fail(error)
    print("Response submitted")


@plan_cli.command("status")
def plan_status_command(
    plan_id: Optional[str] = typer.Argument(None, help="Plan id (default: the latest)."),
    root: str = _ROOT_OPT,
    as_json: bool = typer.Option(False, "--json", help="The alfrd.plan_status/1 document (for scripts and Claude)."),
    detail: str = typer.Option("summary", "--detail", help="summary | rows | full (with --json)."),
    since: Optional[str] = typer.Option(None, "--since", help="Cursor from an earlier call: only say what changed."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Rows per page (--detail rows|full)."),
    offset: int = typer.Option(0, "--offset", help="First row (--detail rows|full)."),
    reconcile: bool = typer.Option(False, "--reconcile", help="Re-attach to a plan whose runner stopped first (the only way this command changes anything)."),
    no_reconcile: bool = typer.Option(False, "--no-reconcile", hidden=True, help="Kept for old scripts: reading never reconciles now."),
):
    """Show a plan: runner, the targets × steps grid, running commands and the queue. Read-only.

    Exit code: 0 finished, 1 finished with failures, 2 running, 3 paused /
    interrupted / cancelled, 4 plan or project not found, 5 runner dead (status is stale).
    """
    import json as _json

    from alfrd.api import status as api
    from alfrd.runtime import scheduler

    try:
        doc = api.plan_status(root, plan_id, detail=detail, since=since, limit=limit, offset=offset, reconcile=reconcile)
    except api.StatusNotFound as error:
        if as_json:
            print(_json.dumps({"schema": api.SCHEMA, "error": {"code": 404, "message": str(error)}}))
        else:
            print(f"{error} (alfrd plan new, then alfrd plan run)")
        raise typer.Exit(code=api.EXIT_NOT_FOUND)
    except ValueError as error:
        _plan_fail(error)
    if as_json:
        print(_json.dumps(doc, indent=1, default=str))
        raise typer.Exit(code=doc["exit_code"])
    data = scheduler.plan_status(root, doc["plan"]["id"])
    plan = data["plan"]
    runner = doc["runner"]
    who = f"runner pid {runner.get('pid')} on {runner.get('host')}" if runner.get("alive") else (
        "no runner (stale: run `alfrd plan reconcile`)" if runner.get("stale") else "no runner")
    print(f"plan {plan['id']} · {plan['status']} · {who} · {plan['csv']} · mode {plan['mode']} × {plan['concurrency']}")
    table = data["table"]
    steps = table.get("steps") or []
    marks = {"done": "✓", "failed": "✗", "running": "▶", "todo": "·", "blocked": "⊘", "interrupted": "!", "cancelled": "–", "skip": " "}
    width = max([len(r["key"]) for r in table.get("rows") or []] + [6])
    print(" " * (width + 2) + " ".join(s[:3] for s in steps))
    waiting = {w["row"]: w["reason"] for w in doc.get("waiting") or []}
    for row in table.get("rows") or []:
        note = f"  {waiting[row['key']]}" if row["key"] in waiting else ""
        print(f"  {row['key']:<{width}} " + " ".join(f"{marks.get(row['cells'].get(s), '?'):^3}" for s in steps) + note)
    for unit in data["running"]:
        print(f"running: {unit['id']} pid {unit.get('pid')} since {unit.get('started')}  log {unit.get('log')}")
    if data["queue"]:
        print(f"queued: {len(data['queue'])} cell(s); next {data['queue'][0]['target']} · {data['queue'][0]['step']}")
    print(doc["summary"])
    raise typer.Exit(code=doc["exit_code"])


@plan_cli.command("wait")
def plan_wait_command(
    plan_id: Optional[str] = typer.Argument(None, help="Plan id (default: the latest)."),
    root: str = _ROOT_OPT,
    until: str = typer.Option("done", "--until", help="done | failed | any-change."),
    timeout: float = typer.Option(3600, "--timeout", help="Seconds before giving up (reason: timeout)."),
    since: Optional[str] = typer.Option(None, "--since", help="Cursor: any-change counts from here."),
    as_json: bool = typer.Option(False, "--json"),
):
    """Block until the plan is done / has a failure / changes (or the timeout). Read-only; same exit codes as status."""
    import json as _json

    from alfrd.api import status as api

    try:
        doc = api.wait(root, plan_id, until=until, timeout=timeout, since=since)
    except api.StatusNotFound as error:
        print(_json.dumps({"schema": api.SCHEMA, "error": {"code": 404, "message": str(error)}}) if as_json else str(error))
        raise typer.Exit(code=api.EXIT_NOT_FOUND)
    except ValueError as error:
        _plan_fail(error)
    print(_json.dumps(doc, indent=1, default=str) if as_json else f"{doc['reason']}: {doc['summary']}")
    raise typer.Exit(code=doc["exit_code"])


@plan_cli.command("events")
def plan_events_command(
    plan_id: Optional[str] = typer.Argument(None, help="Plan id (default: the latest)."),
    root: str = _ROOT_OPT,
    since: Optional[str] = typer.Option(None, "--since", help="Cursor of the last event you saw (each event carries one)."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Keep printing new events until the plan stops."),
    timeout: Optional[float] = typer.Option(None, "--timeout", help="Stop following after this many seconds."),
):
    """Plan events as JSON Lines (started / finished commands, plan history). Read-only."""
    import json as _json

    from alfrd.api import status as api

    try:
        for event in api.follow(root, plan_id, since=since, keep=follow, timeout=timeout):
            print(_json.dumps(event, default=str), flush=True)
    except api.StatusNotFound as error:
        print(_json.dumps({"schema": api.EVENT_SCHEMA, "error": {"code": 404, "message": str(error)}}))
        raise typer.Exit(code=api.EXIT_NOT_FOUND)
    except ValueError as error:
        _plan_fail(error)


@plan_cli.command("log")
def plan_log_command(
    plan_id: Optional[str] = typer.Argument(None, help="Plan id (default: the latest)."),
    root: str = _ROOT_OPT,
    target: Optional[str] = typer.Option(None, "--target", "-t", help="Target (or row key target@CODE)."),
    step: Optional[str] = typer.Option(None, "--step", help="Step (default: the row's latest command)."),
    unit: Optional[str] = typer.Option(None, "--unit", help="Command (unit) id instead of target/step."),
    lines: int = typer.Option(50, "--lines", "-n", help="Last N lines (max 200)."),
    as_json: bool = typer.Option(False, "--json"),
):
    """The end of a plan command's log (ANSI stripped, at most 200 lines / 64 KiB). Read-only."""
    import json as _json

    from alfrd.api import status as api

    if not (target or unit):
        _plan_fail(ValueError("give --target (and --step) or --unit"))
    try:
        doc = api.log_tail(root, plan_id, target=target, step=step, unit=unit, lines=lines)
    except api.StatusNotFound as error:
        print(_json.dumps({"schema": api.LOG_SCHEMA, "error": {"code": 404, "message": str(error)}}) if as_json else str(error))
        raise typer.Exit(code=api.EXIT_NOT_FOUND)
    if as_json:
        print(_json.dumps(doc, indent=1))
    else:
        print(f"# {doc['log']} ({doc['status']})")
        print("\n".join(doc["lines"]))


def _plan_control(action: str, plan_id: Optional[str], root: str, retry_failed: bool = False) -> None:
    from alfrd.execution import ExecutionError
    from alfrd.runtime import scheduler

    try:
        if plan_id is None:
            plans = scheduler.list_plans(root)
            if not plans:
                raise ExecutionError("no plans in this project")
            plan_id = plans[0]["id"]
        plan = scheduler.start_now(root, plan_id) if action == "start-now" else scheduler.control(root, plan_id, action, retry_failed=retry_failed)
    except ExecutionError as error:
        _plan_fail(error)
    print(f"plan {plan_id}: {action} requested (status {plan.get('status')})")


@plan_cli.command("pause")
def plan_pause(plan_id: Optional[str] = typer.Argument(None), root: str = _ROOT_OPT):
    """Finish running commands, start no new ones."""
    _plan_control("pause", plan_id, root)


@plan_cli.command("resume")
def plan_resume(
    plan_id: Optional[str] = typer.Argument(None),
    root: str = _ROOT_OPT,
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Set failed/blocked/interrupted cells back to todo."),
):
    """Continue a paused or interrupted plan (starts a runner if none is alive)."""
    _plan_control("resume", plan_id, root, retry_failed)


@plan_cli.command("start-now")
def plan_start_now(plan_id: Optional[str] = typer.Argument(None), root: str = _ROOT_OPT):
    """Start a plan scheduled with --at now instead of at its time."""
    _plan_control("start-now", plan_id, root)


@plan_cli.command("cancel")
def plan_cancel(
    plan_id: Optional[str] = typer.Argument(None),
    root: str = _ROOT_OPT,
    yes: bool = typer.Option(False, "--yes", help="Confirm without a prompt."),
):
    """Stop running commands (SIGTERM, then SIGKILL) and end the plan."""
    if not yes and not typer.confirm("Stop the running commands and cancel the plan?", default=False):
        raise typer.Exit(code=1)
    _plan_control("cancel", plan_id, root)


@plan_cli.command("reconcile")
def plan_reconcile(root: str = _ROOT_OPT):
    """Re-attach to plans whose runner stopped (after a crash, logout or reboot)."""
    from alfrd.runtime import scheduler

    items = scheduler.reconcile(root)
    for item in items:
        print(f"plan {item['plan']}: {item['action']}")
    if not items:
        print("nothing to reconcile")


@plan_cli.command("runner", hidden=True)
def plan_runner(plan_id: str, root: str = _ROOT_OPT):
    """The background runner (started by `alfrd plan run` / the Studio)."""
    from alfrd.runtime import scheduler

    raise typer.Exit(code=scheduler.Runner(root, plan_id).run())


su_cli = typer.Typer(help="Superuser settings for every alfrd serve / gui of this user (also the Studio page /superuser).",
                     no_args_is_help=True)
alfrd_cli.add_typer(su_cli, name="su")


def _su_call(function, *args, **kwargs):
    from alfrd import superuser

    try:
        return function(*args, **kwargs)
    except (superuser.SuperuserError, OSError) as exc:
        print(f"error: {exc}")
        raise typer.Exit(code=1) from None


@su_cli.command("show")
def su_show(json_out: bool = typer.Option(False, "--json", help="Print the settings as JSON.")):
    """Every setting with its value (`*` marks a value changed from the default)."""
    import json as _json

    from alfrd import superuser

    data = _su_call(superuser.load, strict=True)
    if json_out:
        print(_json.dumps(data, indent=2))
        return
    defaults = superuser.flat(superuser.DEFAULTS)
    print(f"# {superuser.path()}{'' if superuser.path().is_file() else ' (not created yet: all defaults)'}")
    for key, value in superuser.flat(data).items():
        shown = ", ".join(value) if isinstance(value, list) else ("null" if value is None else str(value).lower() if isinstance(value, bool) else value)
        print(f"{'*' if value != defaults[key] else ' '} {key:<30} {shown or '-'}    # {superuser.LABELS.get(key, '')}")


@su_cli.command("set")
def su_set(assignments: list[str] = typer.Argument(..., help="key=value, e.g. features.experimental=true lockdown.runs=false "
                                                          "studio.hidden_sections=results,logs limits.chat_max_cost_usd=2")):
    """Change settings; every running Studio applies them on its next request."""
    from alfrd import superuser

    changes = {}
    for item in assignments:
        key, sep, value = item.partition("=")
        if not sep:
            print(f"error: {item!r} is not key=value")
            raise typer.Exit(code=1)
        changes[key.strip()] = _su_call(superuser.parse_value, key.strip(), value)
    _su_call(superuser.merge, changes)
    for key in changes:
        print(f"{key} = {superuser.flat()[key]}")


@su_cli.command("checker")
def su_checker(enabled: bool = typer.Option(..., "--enabled/--disabled", help="Enable or disable periodic chat recovery."),
               root: str = _ROOT_OPT):
    """Set this project's periodic checker (local OS owner, as with other su commands)."""
    from alfrd import chat

    view = _chat_call(chat.settings_view, root)
    block = {**view["block"], "watch": {**(view["block"].get("watch") or {}), "enabled": enabled}}
    _chat_call(chat.save_settings, root, block, base_hash=view["hash"])
    print(f"chat.watch.enabled = {str(enabled).lower()}")


@su_cli.command("reset")
def su_reset(yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation.")):
    """Remove the superuser file: every setting back to its default."""
    from alfrd import superuser

    if not yes and not typer.confirm(f"Reset all superuser settings ({superuser.path()})?"):
        raise typer.Exit(code=1)
    _su_call(superuser.reset)
    print("superuser settings reset")


@su_cli.command("path")
def su_path():
    """Print where the superuser settings are stored."""
    from alfrd import superuser

    print(superuser.path())


@su_cli.command("status")
def su_status(runtime_db: Optional[str] = typer.Option(None, help="Runtime database (default: the one alfrd serve uses).")):
    """Running alfrd servers, and the active runs (runners and running agents) of every known project."""
    from alfrd.gui.auth import config_dir
    from alfrd.runtime import scheduler

    import json as _json

    print("Servers:")
    for file in sorted(config_dir().glob("server-*.json")):
        try:
            data = _json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pid = data.get("pid")
        alive = bool(pid) and scheduler.pid_alive(int(pid))
        print(f"  {'up  ' if alive else 'gone'}  {data.get('url')}  pid {pid}")
    service = _runtime_service(runtime_db or str(_default_runtime_db()))
    print("Active runs:")
    found = False
    for project in service.list_projects():
        root = Path(project.root_path)
        for plan in scheduler.list_plans(root, include_chat=True) if root.is_dir() else []:
            folder = scheduler.PlanDir(root, plan.get("id") or "")
            alive = bool(plan.get("id")) and folder.runner_alive()
            if not (alive or plan.get("status") in scheduler.ACTIVE + ("interrupted",)):
                continue
            found = True
            units = [u for u in folder.units() if u.get("status") == "running"]
            kind = "chat" if plan.get("chat") else "run"
            print(f"  {project.name}  {kind} {plan['id']}  {plan.get('status')}  runner {'alive' if alive else 'stopped'}"
                  f"  {len(units)} running")
            for unit in units:
                print(f"      {unit.get('id')}  {','.join(unit.get('steps') or [])}  {unit.get('row') or ''}  pid {unit.get('pid')}")
    if not found:
        print("  none")


chat_cli = typer.Typer(help="Project chat: an orchestrator and specialist agents on the plan runner (docs/orchestration.md).",
                       no_args_is_help=True)
alfrd_cli.add_typer(chat_cli, name="chat")


def _chat_call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (LookupError, ValueError, PermissionError) as exc:
        print(f"error: {exc}")
        raise typer.Exit(code=1) from None


@chat_cli.command("new")
def chat_new(title: Optional[str] = typer.Option(None, "--title"), root: str = _ROOT_OPT):
    """Start a chat session; prints its id."""
    from alfrd import chat

    print(_chat_call(chat.create_session, Path(root).resolve(), title)["id"])


@chat_cli.command("list")
def chat_list(root: str = _ROOT_OPT):
    from alfrd import chat

    for item in _chat_call(chat.list_sessions, Path(root).resolve()):
        schedule = item.get("schedule")
        pending = f" · {schedule.get('queued_count', 0)} queued · {schedule.get('run_at', '')}" if schedule else ""
        print(f"{item['id']}  {item['title']}  {item['messages']} messages  {item.get('plan_status') or ''}{pending}")


@chat_cli.command("rename")
def chat_rename(session: str, title: str, root: str = _ROOT_OPT):
    """Rename a chat session."""
    from alfrd import chat

    print(_chat_call(chat.rename_session, Path(root).resolve(), session, title)["title"])


@chat_cli.command("delete")
def chat_delete(session: str, yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation."),
                root: str = _ROOT_OPT):
    """Delete a chat session with its messages, tasks, logs and mailbox (refused while it still runs)."""
    from alfrd import chat

    data = _chat_call(chat.session, Path(root).resolve(), session).load()
    if not yes and not typer.confirm(f"Delete chat {data.get('title') or session!r}? This can't be undone."):
        raise typer.Exit(code=1)
    _chat_call(chat.delete_session, Path(root).resolve(), session)
    print(f"deleted {session}")


@chat_cli.command("send")
def chat_send(session: str, text: str, root: str = _ROOT_OPT):
    """Send a message ("@agent …" goes to that agent directly); the runner starts in the background."""
    from alfrd import chat

    out = _chat_call(chat.post_message, Path(root).resolve(), session, text)
    if out.get("queued"):
        print(f"Queued {out['item']['id']} for {out['schedule']['run_at']}")
        return
    print(f"{out['request']['id']}: {out['request']['mode']} → {out['task']['row']}")


@chat_cli.command("show")
def chat_show(session: str, json_out: bool = typer.Option(False, "--json"), root: str = _ROOT_OPT):
    """Messages, tasks (with status), open decisions and usage of a session."""
    import json as _json

    from alfrd import chat

    state = _chat_call(chat.session_state, Path(root).resolve(), session)
    if json_out:
        print(_json.dumps(state, indent=2, default=str))
        return
    if state.get("schedule"):
        batch = state["schedule"]
        print(f"schedule {batch['id']}: {batch['status']} · {batch['run_at']} ({batch['timezone']})")
        for item in batch["items"]:
            if not item.get("removed_at"):
                print(f"  queued {item['id']}: {item['text']}")
    for m in state["messages"]:
        print(f"[{m['role']}{'/' + m['agent'] if m.get('agent') else ''}] {m['text']}")
    for t in state["tasks"]:
        print(f"  {t['row']:<28} {t['kind']:<8} {t['owner']:<8} {t['status']}")
    for d in state["blockers"]:
        print(f"  decision {d['id']}: {d['question']} {d.get('options') or ''}")
    cost = state["usage"].get("total_cost_usd")
    print(f"usage: {state['usage']['turns']} turns" + (f", ${cost:.4f}" if cost is not None else ""))


@chat_cli.command("probe")
def chat_probe(root: str = _ROOT_OPT):
    """Which chat agents can run here (programs on PATH), a local Ollama and its models, HF_TOKEN set (JSON)."""
    import json as _json

    from alfrd import chat

    print(_json.dumps(_chat_call(chat.probe, Path(root).resolve()), indent=2))


@chat_cli.command("check")
def chat_check(session: str | None = typer.Option(None, "--session", help="Check one session (default: all)."),
               json_out: bool = typer.Option(False, "--json"), root: str = _ROOT_OPT):
    """Recover interrupted chat work without an agent call; suitable for cron/systemd."""
    import json as _json

    from alfrd import chat

    reports = _chat_call(chat.check, Path(root).resolve(), session)
    if json_out:
        print(_json.dumps(reports, indent=2))
        return
    for report in reports:
        print(f"{report['session']}: " + ("; ".join(f["text"] for f in report["findings"]) or "No issues found."))
    if not reports:
        print("No chat sessions found.")


@chat_cli.command("decide")
def chat_decide(session: str, decision: str, choice: Optional[str] = typer.Option(None, "--choice"),
                answer: Optional[str] = typer.Option(None, "--answer"), root: str = _ROOT_OPT):
    from alfrd import chat

    _chat_call(chat.decide, Path(root).resolve(), session, decision, choice=choice, answer=answer)
    print("decided")


plugin_cli = typer.Typer(help="Manage trusted plugins and Studio themes.", no_args_is_help=True)
alfrd_cli.add_typer(plugin_cli, name="plugin")


def _plugin_call(function, *args, **kwargs):
    """Report operational failures without a CLI traceback."""
    try:
        return function(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - CLI boundary, Ctrl-C still propagates
        _plugin_fail(str(exc))


def _plugin_fail(message: str):
    typer.echo(f"alfrd plugin: {message}", err=True)
    raise typer.Exit(code=1) from None


def _plugin_records():
    from alfrd import extensions

    records = _plugin_call(extensions.load, force=True)
    core = _core_command_names(alfrd_cli)
    for record in records:
        if record.status == "ok" and record.plugin and record.plugin.cli is not None and record.id in core:
            record.status, record.error = "error", f"command name {record.id!r} is taken by alfrd"
    return records


def _plugin_find(plugin_id: str, *, load: bool = False):
    from alfrd import extensions

    if not load:
        _plugin_call(extensions.add_site)
    records = _plugin_records() if load else _plugin_call(extensions.discover)
    record = next((record for record in records if record.id == plugin_id), None)
    if record is None:
        _plugin_fail(f"unknown plugin {plugin_id!r}; known: {', '.join(r.id for r in records) or '(none)'}")
    return record


def _plugin_restart():
    typer.echo("restart `alfrd serve` to apply")


@plugin_cli.command("list")
def plugin_list():
    """List installed plugins and themes, including disabled or broken plugins."""
    rows = [("ID", "VERSION", "KINDS", "ENABLED", "STATUS", "SOURCE PACKAGE")]
    rows += [(r.id, r.version or "-", ",".join(r.kinds) or "-", "yes" if r.enabled else "no", r.status,
              r.dist or r.source) for r in _plugin_records()]
    widths = [max(len(str(row[i])) for row in rows) for i in range(6)]
    for row in rows:
        typer.echo("  ".join(str(value).ljust(width) for value, width in zip(row, widths)).rstrip())


@plugin_cli.command("info")
def plugin_info(plugin_id: str):
    """Show a manifest, its contributions and the most recent loading error."""
    import json

    record = _plugin_find(plugin_id, load=True)
    data = record.to_dict()
    if record.plugin:
        data.update(requires_bin=list(record.plugin.requires_bin), web=record.plugin.web,
                    theme=record.plugin.theme, cli=record.plugin.cli is not None)
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False))


def _plugin_enable(plugin_id: str, enabled: bool):
    from alfrd import extensions

    record = _plugin_find(plugin_id)
    if record.source != "entry_point":
        _plugin_fail("select a theme with `alfrd plugin theme <id>`")
    _plugin_call(extensions.set_enabled, plugin_id, enabled)
    typer.echo(f"{plugin_id}: {'enabled' if enabled else 'disabled'}")
    _plugin_restart()


@plugin_cli.command("enable")
def plugin_enable(plugin_id: str):
    """Enable a plugin on the next server start."""
    _plugin_enable(plugin_id, True)


@plugin_cli.command("disable")
def plugin_disable(plugin_id: str):
    """Disable a plugin on the next server start."""
    _plugin_enable(plugin_id, False)


@plugin_cli.command("install")
def plugin_install(spec: str, yes: bool = typer.Option(False, "--yes", help="Trust this package without a prompt.")):
    """Install a trusted Python package, local wheel or source package."""
    import getpass
    from alfrd.extensions import installer

    typer.echo(f"Plugins run as {getpass.getuser()} with access to your files and projects. Install only packages you trust.")
    if not yes and not typer.confirm(f"Trust and install {spec!r}?", default=False):
        raise typer.Exit(code=1)
    for record in _plugin_call(installer.install, spec):
        typer.echo(f"installed {record['id']} {record['version']}")
    _plugin_restart()


@plugin_cli.command("remove")
def plugin_remove(plugin_id: str, yes: bool = typer.Option(False, "--yes", help="Remove without a prompt.")):
    """Remove a package installed with `alfrd plugin install`."""
    from alfrd.extensions import installer

    if not yes and not typer.confirm(f"Remove plugin {plugin_id!r}?", default=False):
        raise typer.Exit(code=1)
    _plugin_call(installer.remove, plugin_id)
    typer.echo(f"removed {plugin_id}")
    _plugin_restart()


@plugin_cli.command("update")
def plugin_update(plugin_id: Optional[str] = typer.Argument(None, help="Plugin to update (default: all installed plugins).")):
    """Update from each plugin's recorded installation source."""
    from alfrd.extensions import installer

    for record in _plugin_call(installer.update, plugin_id):
        typer.echo(f"updated {record['id']} {record['version']}")
    _plugin_restart()


@plugin_cli.command("theme")
def plugin_theme(theme_id: Optional[str] = typer.Argument(None, help="Theme to select (default: list themes).")):
    """List Studio themes or select a theme, then reload Studio to apply."""
    from alfrd import extensions

    records = [r for r in _plugin_records() if "theme" in r.kinds and r.enabled and r.status == "ok"]
    if theme_id is None:
        current = extensions.read_state()["theme"]
        for record in records:
            typer.echo(f"{'*' if record.id == current else ' '} {record.id}  {record.title or record.id}")
        typer.echo(f"Drop-in themes: {extensions.themes_dir()}")
        return
    if theme_id not in {r.id for r in records}:
        typer.echo(f"alfrd plugin: unknown theme {theme_id!r}; available: {', '.join(r.id for r in records) or '(none)'}", err=True)
        raise typer.Exit(code=1)
    _plugin_call(extensions.set_theme, theme_id)
    typer.echo(f"theme: {theme_id}")
    typer.echo("reload Studio to apply")


@plugin_cli.command("new")
def plugin_new(
    plugin_id: str,
    kind: str = typer.Option("viewer", "--kind", help="viewer | converter | theme | panel"),
    directory: Path = typer.Option(Path("."), "--dir", help="Parent folder for the scaffold."),
):
    """Create an editable plugin package or drop-in theme."""
    from alfrd.extensions.scaffold import create

    target = _plugin_call(create, plugin_id, kind=kind, directory=directory)
    typer.echo(f"created {target}")
    typer.echo(f"See {target / 'README.md'} for installation and test commands.")


def _core_command_names(app: typer.Typer) -> set[str]:
    mounted = getattr(app, "_alfrd_plugin_commands", {})
    names = {c.name or (c.callback.__name__.replace("_", "-") if c.callback else "") for c in app.registered_commands}
    return names | {g.name for g in app.registered_groups if mounted.get(g.name) is not g.typer_instance}


def mount_plugin_commands(app: typer.Typer = alfrd_cli) -> list:
    """Load the plugins and add each ok plugin's ``cli`` Typer as ``alfrd <id> …`` (core names win)."""
    from alfrd import extensions

    core = _core_command_names(app)
    mounted = getattr(app, "_alfrd_plugin_commands", {})
    app._alfrd_plugin_commands = mounted
    records = extensions.load()
    for rec in records:
        if rec.status != "ok" or rec.plugin is None or rec.plugin.cli is None:
            continue
        if rec.id in core:
            rec.status, rec.error = "error", f"command name {rec.id!r} is taken by alfrd"
            continue
        if rec.id in mounted:
            continue
        app.add_typer(rec.plugin.cli, name=rec.id)
        mounted[rec.id] = rec.plugin.cli
        core.add(rec.id)
    return records


def main() -> None:
    """The ``alfrd`` console script: plugin commands, then the CLI. Plugin failures never stop it."""
    from alfrd.extensions import safe_mode

    # `alfrd plugin …` must work while a plugin is broken, and must not hold plugin files open.
    argv = sys.argv[1:]
    if "--safe-mode" not in argv and argv[:1] != ["plugin"] and not safe_mode():
        try:
            mount_plugin_commands()
        except Exception as exc:  # noqa: BLE001
            print(f"alfrd: plugins not loaded ({exc})", file=sys.stderr)
    alfrd_cli()


if __name__ == "__main__":
    main()

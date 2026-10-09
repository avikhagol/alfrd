"""`alfrd gsheet init|validate|diff|push|status`; reuses the hook engine, never a second sync path.

Every failure prints one safe line on stderr and exits 1. Mapping/sync/execution
errors are already safe; anything else is reported by exception type only.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import typer

from . import mapping
from . import settings as plugin_settings
from .client import SyncError
from .project import (
    CLI_TIMEOUT,
    HISTORY,
    MAPPING,  # noqa: F401 - compatibility for existing plugin imports
    _plans,
    _runner,
    init_project,
    push_project,
    validate_summary,
)

cli = typer.Typer(help="Sync plan step results to a Google Sheet (alfrd.gsheet.yaml).", no_args_is_help=True)

Project = Annotated[Path, typer.Argument(help="Project folder (holds alfrd.yaml).")]


@cli.callback()
def main():
    """Google Sheet sync commands."""


def fail(message: str) -> None:
    typer.echo("gsheet: " + " ".join(str(message).split())[:800], err=True)
    raise typer.Exit(1)


@contextlib.contextmanager
def errors() -> Iterator[None]:
    from alfrd.execution import ExecutionError

    try:
        yield
    except (typer.Exit, typer.Abort):
        raise
    except (SyncError, mapping.MappingError, ExecutionError) as exc:
        fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - library errors may contain secrets or response bodies
        fail(f"failed ({type(exc).__name__})")


def _changes(result) -> None:
    for address, value in result.changes.items():
        before = result.previous.get(address)
        old = "" if before is None else f"{before!r} → "
        typer.echo(f"  {address}: {old}{value!r}")


@cli.command()
def init(project: Project,
         spreadsheet: Annotated[str | None, typer.Option("--spreadsheet", help="Spreadsheet id or URL "
                                                         "(default: the one in Settings → Plugins).")] = None,
         worksheet: Annotated[str | None, typer.Option("--worksheet", help="Tab title (default: the first tab).")] = None,
         header_row: Annotated[int, typer.Option("--header-row", min=1, help="Sheet row holding column names.")] = 1,
         force: Annotated[bool, typer.Option("--force", help="Replace an existing alfrd.gsheet.yaml.")] = False):
    """Write a starter alfrd.gsheet.yaml: step ids matched to same-named sheet columns."""
    with errors():
        outcome = init_project(project, spreadsheet=spreadsheet, worksheet=worksheet, header_row=header_row, force=force)
    target, worksheet = outcome.path, outcome.worksheet
    matched, key = outcome.matched, outcome.key

    typer.echo(f"Wrote {target} for {worksheet!r}: {len(matched)} of {len(outcome.steps)} steps match a sheet column"
               + (f" ({', '.join(matched)})." if matched else "."))
    if not outcome.key_in_header:
        typer.echo(f"Warning: the key column {key!r} is not in the header; edit rows.key_column.", err=True)
    if not matched:
        typer.echo("Add an outbound rule per step, or add sheet columns named after the steps.", err=True)
    typer.echo(f"Next: alfrd gsheet validate {project}")


@cli.command()
def validate(project: Project,
             offline: Annotated[bool, typer.Option("--offline", help="Check the file only; don't read the sheet.")] = False):
    """Check alfrd.gsheet.yaml against the project's steps and the sheet's header row."""
    level, text = validate_summary(project, offline=offline)
    if level != "ok":
        fail(text)
    typer.echo(text)


@cli.command()
def diff(project: Project,
         unit: Annotated[str, typer.Option("--unit", help="Unit id (see the plan's units/ folder or the Studio).")],
         plan: Annotated[str | None, typer.Option("--plan", help="Plan id (default: search all plans).")] = None):
    """Show the cells the unit's after-sync would change in the current sheet. Never writes."""
    root = project.resolve()
    with errors():
        runners: dict = {}
        found = None
        for plan_id in _plans(root, plan):
            runner = _runner(root, plan_id, runners)
            found = next((u for u in runner.folder.units() if str(u.get("id")) == unit), None)
            if found is not None:
                break
        if found is None:
            fail(f"no unit {unit!r}" + (f" in plan {plan!r}." if plan else " in this project's plans."))
        ctx = runner.step_context(found, after=True)
        result = plugin_settings.make_engine(CLI_TIMEOUT).push([ctx], dry_run=True)
    if result is None:
        typer.echo(f"Nothing to compare: sync is off or no rule maps {', '.join(ctx.steps) or 'these steps'}.")
        return
    if not result.changes:
        typer.echo("No cells would change.")
        return
    typer.echo(f"{len(result.changes)} cell(s) would change (nothing written):")
    _changes(result)


@cli.command()
def push(project: Project,
         targets: Annotated[list[str] | None, typer.Option("--targets", help="Targets (comma-separated or repeated).")] = None,
         steps: Annotated[list[str] | None, typer.Option("--steps", help="Steps (comma-separated or repeated).")] = None,
         plan: Annotated[str | None, typer.Option("--plan", help="Only this plan (default: all plans).")] = None,
         dry_run: Annotated[bool, typer.Option("--dry-run", help="Show the changes; write nothing.")] = False):
    """Backfill the sheet from finished units: the newest result per target and step, changed cells only."""
    with errors():
        outcome = push_project(project, targets=targets, steps=steps, plan=plan, dry_run=dry_run)
    if not outcome.contexts:
        typer.echo("No finished units match; nothing to push.")
        return
    result = outcome.result

    if result is None:
        typer.echo("Nothing to push: sync is off or no rule maps the selected steps.")
        return
    units = f"{outcome.contexts} target/step result(s)"
    if result.result == "dry run":
        why = "" if dry_run else " (dry_run is on in Settings → Plugins → Google Sheet)"
        typer.echo(f"Dry run{why}: {len(result.changes)} cell(s) would change from {units}; nothing written.")
        _changes(result)
    elif result.result == "ok":
        skipped = f"; {len(result.conflicts)} conflicting cell(s) kept" if result.conflicts else ""
        typer.echo(f"ok: wrote {result.cells_written} cell(s) from {units}{skipped}.")
    else:
        typer.echo(f"{result.result}: nothing written from {units}"
                   + (f" ({len(result.conflicts)} cell(s) changed in the sheet)." if result.conflicts else "."))


@cli.command()
def status(project: Project,
           lines: Annotated[int, typer.Option("--lines", "-n", min=1, help="How many recent entries.")] = 20,
           as_json: Annotated[bool, typer.Option("--json", help="Print the raw JSON lines.")] = False):
    """The most recent sync results (newest last)."""
    path = project.resolve() / HISTORY
    if not path.exists():
        typer.echo("No sync history yet: no step has finished since sync was set up.")
        return
    with errors():
        entries = path.read_text(encoding="utf-8").splitlines()[-lines:]
    for line in entries:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if as_json:
            typer.echo(json.dumps(entry, ensure_ascii=False))
            continue
        cells = entry.get("cells_written") or 0
        conflicts = len(entry.get("conflicts") or [])
        parts = [str(entry.get("at", ""))[:19].replace("T", " ") + " UTC",str(entry.get("unit_id", "")),
                 ",".join(entry.get("steps") or []), str(entry.get("result", "")),
                 f"{cells} cell{'s' if cells != 1 else ''}"]
        if conflicts:
            parts.append(f"{conflicts} conflict{'s' if conflicts != 1 else ''}")
        if entry.get("error"):
            parts.append(str(entry["error"]))
        typer.echo("  ".join(p for p in parts if p))

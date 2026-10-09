"""Google Sheet plugin. Importing the manifest never loads Google libraries."""

from __future__ import annotations

from alfrd.extensions import PanelSpec, Plugin, StepHooks

from .actions import ACTIONS
from .cli import cli
from .settings import FIELDS, check, make_engine
from .studio import auto_panel, check_project, evaluate


def before(ctx):
    engine = make_engine()
    snapshot = engine.before(ctx)
    return (engine, snapshot) if snapshot is not None else None


def after(ctx, state):
    if state is not None:
        engine, snapshot = state
        return engine.after(ctx, snapshot)
    if ctx.readopted:
        return make_engine().after(ctx, None)
    return None

plugin = Plugin(
    id="gsheet", version="0.1.0", alfrd_api=">=1,<2", title="Google Sheet",
    description="Two-way sync around launched steps, writing only changed cells.", cli=cli,
    settings=FIELDS, check=check, step_hooks=StepHooks(before=before, after=after, timeout=30),
    panels=(PanelSpec("gsheet_sync", evaluate=evaluate, client=True, title="Google Sheet", auto=auto_panel),),
    web="web", check_project=check_project, project_actions=ACTIONS,
)

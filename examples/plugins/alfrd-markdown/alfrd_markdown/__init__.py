"""Reference Markdown viewer; parsing and sanitizing happen in the browser."""
from alfrd.extensions import Plugin

plugin = Plugin(
    id="markdown", version="0.1.0", title="Markdown",
    description="Render Markdown files in the Studio file viewer.",
    web="web", viewers=["markdown"],
)

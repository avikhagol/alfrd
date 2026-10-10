# ALFRD Markdown viewer

This plugin enables Studio's rendered file viewer for `.md` and `.markdown`
files and `text/markdown` content. Headings, lists, fenced code blocks and tables
are rendered in the current Studio theme. It also enables the **Rendered / Source**
controls for each incoming prompt and response in Results → Read responses /
handoffs → Responses. Rendered opens by default when the viewer is enabled;
Source shows the original Markdown. Without the plugin, handoffs remain readable
as Source and show an installation hint.

Handoff previews render only the pages already loaded. An “incomplete prompt” or
“incomplete response” notice means **Load more** is needed; later pages re-render
the accumulated Markdown. **Copy** still copies the full original artifact,
including pages not yet displayed. Rendering does not edit the artifact or review
response.

From the ALFRD repository root:

```sh
alfrd plugin install ./examples/plugins/alfrd-markdown --yes
alfrd serve
```

Restart a running `alfrd serve` after installing. Open a `.md` or `.markdown`
file, or declare one as a view panel's `source` in `alfrd.yaml`. The viewer parses
Markdown locally, then calls the host's synchronous `api.sanitize` before inserting
HTML. ALFRD preloads DOMPurify before calling `activate(api)`.

No Python runtime dependencies are needed: ALFRD supplies the plugin API.
Building the folder requires Hatchling (`hatchling>=1.25`). Offline folder installs
need that build backend and its dependencies in the installer cache; alternatively
build a wheel beforehand with `uv build --wheel ./examples/plugins/alfrd-markdown`
and install the wheel. The offline integration test creates a wheel directly from
these package files, so it does not exercise Hatchling or folder builds.

Vendored parser: **marked 15.0.12**, unchanged ES module build from
https://cdn.jsdelivr.net/npm/marked@15.0.12/lib/marked.esm.js (upstream:
https://github.com/markedjs/marked/tree/v15.0.12).
`alfrd_markdown/web/LICENSE-marked.md` contains the upstream licence.
SHA-256 of `marked.esm.js`:
`29e25cdf1a06075c90e9885af06ddd23fb22a5424ed23a61dda986c73a8af641`.

Run the reference-plugin checks from the repository root:

```sh
uv --cache-dir /tmp/alfrd-uv-cache run --no-sync python -m pytest tests/test_plugin_examples.py -q
```

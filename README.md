<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/avikhagol/alfrd/HEAD/docs/brand/alfrd-lockup-dark.svg">
    <img src="https://raw.githubusercontent.com/avikhagol/alfrd/HEAD/docs/brand/alfrd-lockup-light.svg" alt="alfrd" width="220">
  </picture>
</p>

<p align="center">
  Run your pipeline steps, see where every target stands, and pick up where you left off.
</p>

<p align="center">
  <a href="https://pypi.org/project/alfrd/"><img src="https://img.shields.io/pypi/v/alfrd?color=6366F1" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-0E1116" alt="Python 3.10+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-BSD--3--Clause-0E1116" alt="BSD-3-Clause license"></a>
</p>

<p align="center">
  <a href="https://raw.githubusercontent.com/avikhagol/alfrd/HEAD/docs/images/overview.png"><img src="https://raw.githubusercontent.com/avikhagol/alfrd/HEAD/docs/images/overview.png" alt="ALFRD Studio overview: target status, progress, runtime and failed-step details; open full-size image" width="400"></a>
</p>

**ALFRD** runs command-line pipelines across many targets, tracks progress in a CSV, and shows
status and logs in an offline web UI. Define your steps in `alfrd.yaml`. Built for radio astronomy
with [AVICA](https://avikhagol.github.io/avica-demos); works with any command-line pipeline.

## Install

Python 3.10+. Install with [uv](https://docs.astral.sh/uv/) (or `pip install alfrd`);

```bash
uv tool install alfrd
```

## Quick start

```bash
alfrd serve --demo
```

Try the demo at `http://127.0.0.1:5000/studio/`; **Ctrl+C** stops it. For your project,
[define `alfrd.yaml`](docs/configuration.md), then run `alfrd serve`. [Remote access →](docs/serve.md#stopping-and-remote-machines)

## What you can do

- **Track and compare targets:** status, failures, live logs, timings and run history.
- **Run and resume work:** select target × step cells in a plan CSV; resume unfinished steps.
- **Extend your workflow:** Claude Code ↔ Codex handoffs, notifications, plugins and themes.

## Documentation

| Start here | Go further |
|---|---|
| [Studio guide](docs/studio-guide.md) · [Serve & screenshots](docs/serve.md) | [Agent loops](docs/agent-loops.md) · [Notifications](docs/notifications.md) |
| [Pipeline configuration](docs/configuration.md) · [Plans](docs/plans.md) | [Plugins & themes](docs/plugins.md) · [Python API](docs/python-api.md) |
| [Search](docs/search.md) · [Metadata panels](docs/template-views.md) | [Status API](docs/status-api.md) · [Changelog](CHANGELOG.md) |

[Report a bug or suggest a feature](https://github.com/avikhagol/alfrd/issues) ·
[Email](mailto:akumar@ia.forth.gr) · [Logo & usage](docs/brand/README.md) · [BSD-3-Clause license](LICENSE)

## Credits

Built in the SMILE project (“Search for Milli-Lenses”), funded by the European Research Council
(ERC), HORIZON ERC Grants 2021, grant agreement No. 101040021. If you use ALFRD, please link to
this repository in a footnote.

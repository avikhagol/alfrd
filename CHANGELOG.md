# Changelog

## [Unreleased]

#### Added

- `alfrd serve` in a folder that is not a project opens every sub-folder with its own `alfrd.yaml` / `.alfrd.yaml` (2 levels deep by default). Options `--discover/--no-discover`, `--discover-depth N`. Never searched: `*.ms`, `raw/`, `tmp_*`, `calibration_tables`, dot-folders, and the inside of a project. The first project (by folder) is opened; the others are in the project picker. Discovered folders are **Rediscover** candidates.
- Import → Connect → **Browse…** (server mode): click through the folders of the machine running `alfrd serve` (useful over an SSH tunnel), with a badge on ALFRD projects, **Connect**, **Connect all projects here** and **Use this folder**. New endpoint `GET /api/studio/fs/list` (loopback + CSRF token, like writes).
- Log Stream tabs: the `>_` button on a log, or **Minimize to Log Stream** in full screen, docks it as a tab of the Log Stream panel. It keeps following the file while you use Overview, Workflow, … Up to 6 tabs, remembered across reloads. The panel can be resized (drag its top edge, or arrow keys).

## [0.2.0.4]

#### Fixed

- The Studio shows the ALFRD project name (`name` in alfrd.yaml, or `name (folder)` when two connected projects share it) instead of the long identifier (`<host>.<path>.<name>`) in pickers, headers, chips, logs and the Forget dialog. The identifier is still the internal key.
- `taret_dir` supports `"."`.


[0.2.0.3]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.3  
[0.2.0.2]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.2
[0.2.0.1]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.1
[0.2.0.0]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.0
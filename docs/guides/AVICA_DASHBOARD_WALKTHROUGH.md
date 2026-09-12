# AVICA 0.3 dashboard walkthrough

This imports AVICA's completed `*_result.csv` histories into an ALFRD runtime database. It does not import AVICA or CASA, execute a pipeline step, or write into the AVICA output directory.

The commands below were verified from the ALFRD source checkout with the legacy-named AVICA data directory mounted at the stated path.

## 1. Install ALFRD with the dashboard dependencies

From the root of an ALFRD checkout:

```sh
uv sync --python 3.13 --extra dev --extra gui --extra polars
```

## 2. Inspect and validate the AVICA manifest

The shipped manifest is `examples/avica_0.3/alfrd.yaml`. It declares the `TARGET_NAME` primary key, the prior main-sheet metadata columns, AVICA's ordered steps, result CSV/log/MS/UVF artifact patterns, and the AVICA CSV fields.

```sh
uv run alfrd manifest validate examples/avica_0.3/alfrd.yaml
```

Expected output starts with:

```text
valid project-manifest-v1:
```

## 3. Import an existing AVICA output directory

Choose a writable location for the ALFRD runtime database. The AVICA source directory remains read-only.

```sh
mkdir -p /tmp/alfrd-avica
```

Then import the existing result histories:

```sh
uv run alfrd import avica-run /mnt/6438D98627D1388F/Intelligence/tests/vasco_0.3/reductions --project avica-real --manifest examples/avica_0.3/alfrd.yaml --db /tmp/alfrd-avica/avica.sqlite
```

The importer validates every CSV and hashes every candidate artifact before opening a database transaction, then creates the project, workflow, datasets, runs, and artifacts in one transaction. It uses the final CSV row for a repeated step, maps failed rows to `failed`, stores `desc` in the cell detail, and reads `avica.inp` into workflow parameters. CSVs require headers, at least one meaningful row, non-negative integer counts, and a non-empty JSON boolean `success` list. A failed import leaves no partial project records and can be retried after correction.

Detail artifacts are read-only. Relative and historical absolute paths are registered only when their fully resolved target is inside the AVICA run root (the parent of `target_dir`); this includes symlink resolution. Paths escaping via `/`, `~`, `..`, or a symlink are skipped and never opened or hashed. Missing contained paths are counted and reported rather than created.

## 4. Serve the dashboard

```sh
uv run alfrd serve --host 127.0.0.1 --port 5187 --runtime-db /tmp/alfrd-avica/avica.sqlite
```

Leave this process running while using the browser. Stop it with `Ctrl-C` when finished.

## 5. Open the dashboard

Open these URLs:

1. `http://127.0.0.1:5187/dashboard/` lists imported projects.
2. `http://127.0.0.1:5187/dashboard/project/avica-real` shows the attached manifest, ordered workflow and steps, AVICA `avica.inp` parameters, dataset columns, artifact declarations, and the **Dataset × step matrix** link.
3. `http://127.0.0.1:5187/dashboard/project/avica-real/workflows/avica/matrix` is the sheet-style dataset × step view. Each target has one row; each declared AVICA step has one cell. Click a completed cell to inspect timestamps, `desc`/error text, and artifacts.
4. `http://127.0.0.1:5187/api/projects/avica-real/workflows/avica/matrix` returns the same matrix as JSON.

For the verified AVICA import, rows included `0742+103`, `1309+555`, and `3C274`; `phaseshift` was `skipped` because it was declared but absent from these result CSVs.

## 6. Export spreadsheet-friendly CSV

On the matrix page, use **Export matrix CSV** for one row per target and one column per step. The direct URL is:

```text
http://127.0.0.1:5187/api/projects/avica-real/workflows/avica/matrix.csv
```

Use **Export details CSV** when a row per target/step, including timestamps, errors, and artifact counts, is needed:

```text
http://127.0.0.1:5187/api/projects/avica-real/workflows/avica/matrix-details.csv
```

## Troubleshooting

- `the sheet-like matrix requires a configured runtime service` or a matrix 404: restart the server with `--runtime-db` pointing at the same database used by `import avica-run`. `serve` without that option is catalog-only and does not expose runtime matrix data.
- `AVICA import failed: No *_result.csv files found`: pass AVICA's `target_dir` (for this run, `.../vasco_0.3/reductions`), not its parent directory.
- `missing_artifacts=N`: this is non-fatal. The verified real import reported `missing_artifacts=8` because some paths recorded by historical AVICA rows no longer existed. Existing MS directories and UVF files were registered; ALFRD never recreates missing source products.
- `project ... already exists`: use a new `--project` name or a fresh runtime DB. Imports deliberately do not overwrite prior runtime history.

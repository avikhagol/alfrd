# Folder hierarchy and Metadata panels

A template (`src/alfrd/web/assets/templates/*.yaml`) or a project's own
`alfrd.yaml` describes where its data lives and what the Studio's **Metadata**
view shows for a target. `alfrd.layout_generic` reads both blocks; with
`alfrd serve` the Metadata view is drawn only from them. JSON Schema:
`alfrd/schemas/template_views.v1.json`.

## `hierarchy`

Folder levels, outermost first. Each has a `level` name, a `dir` pattern (relative
to the level above, or to the project with `{target_dir}`) and optionally
`targets_from`: file patterns inside the folder that name targets.

```yaml
hierarchy:
  - {level: night, dir: "nights/{night}"}
  - {level: chip, dir: "{chip}", targets_from: ["{target}.json", "{target}_*.json"]}
```

A file names one target, by its most specific pattern: `M31_bias.json` gives
`M31`, not `M31_bias`. The avica template takes its patterns from the `avica:`
block (`{from: avica.workdir}`).

## `views.metadata`

`vars` are extra placeholders (a string pattern, or `{from: avica.x, prefix: …}`).
Each panel has a `panel` type, a `title` and a `scope`: a hierarchy level (one
instance per folder, shown as tabs), `target` (one merged instance), or `project`
(one instance evaluated without target/folder values and without merging rows).
Use `project` for plugin summaries such as Google Sheet sync history.

A plugin may also add its panel on its own (`PanelSpec(auto=…)`, see
[plugins.md](plugins.md#automatic-panels)). For example, the Google Sheet panel
appears for every project that has an `alfrd.gsheet.yaml`. Listing it here is
optional. If you do list it, your entry (title, scope) is used instead of the
plugin's, and the panel is not added twice.

| panel | shows | options |
|---|---|---|
| `file_status` | the steps' `metadata:` files, present / invalid (missing required keys, bad JSON) / missing | `files: from_steps` (or a list of `{step, path, label, expect: {require}}`), `base` (folder of the step files, default `{meta_dir}`) |
| `files` | every file of a folder, grouped by the step that writes it; JSON parsed; files a step declares for another target are left out | `source`, `base` |
| `json_fields` | chosen fields of one JSON file (`a.b`, `a[0]`, `a[*].b`) | `source`, `fields` |
| `csv_table` | CSV files (`result_csv`: the AVICA result CSVs of the target) | `source`, `columns` |
| `text` | text files (first 64 KB) | `source`, `limit` |
| `image` | images, served only if the panel lists them | `source`, `limit` |
| `avica_config`, `avica_inputs` | drawn by the Studio (AVICA configuration, rPicard inputs) | — |

```yaml
views:
  vars: {chip_dir: "nights/{night}/{chip}"}
  metadata:
    - {panel: file_status, title: Step outputs, scope: chip, files: from_steps, base: "{chip_dir}"}
    - {panel: files, title: Chip files, scope: chip, source: "{chip_dir}/*", base: "{chip_dir}"}
    - {panel: json_fields, title: Exposures, scope: target, source: "{chip_dir}/{target}.json", fields: ["exp[*].t", filter]}
    - {panel: text, title: Night log, scope: night, source: "nights/{night}/night.log"}
```

`tests/fixtures/imaging_tree/` is a complete non-AVICA example. `GET
/api/studio/projects/<p>/view?entity=target=M31` returns the evaluated panels
(`alfrd.view/1`).

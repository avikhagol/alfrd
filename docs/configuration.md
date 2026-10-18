<img src="brand/alfrd-mark.svg" alt="ALFRD" width="44" align="right">

# Describing your pipeline (`alfrd.yaml`)

The Studio has no built-in pipeline. `alfrd.yaml` describes it.

Minimal:

```yaml
name: my-project
workflows:
  - name: main
    steps: [prepare, calibrate, image]
```

Richer (AVICA example):

```yaml
name: avica-t-0.3
template: avica                      # AVICA defaults: labels, stages, metadata, logs

project_settings:
  field_aliases:                     # old names → new names
    vasco_avg: avica_avg
    "vasco*": "avica*"

overview:
  ms_path:                           # "MS Storage Path" column
    - "{workdir}/wd_{band}_{target}/VLBI_{band}.ms"

stages:
  - {id: ingest, title: Ingestion}

workflows:
  - name: avica
    steps:
      - id: preprocess_fitsidi
        stage: ingest
        category: Preprocessing
        label: FITS-IDI data ingestion
        metadata:                    # → Metadata health
          - {file: fitsfiles_used.avica, require: [filepath]}
      - id: avica_avg
        logs:                        # → step logs
          - "{workdir}/wd_{band}/avica_avg_*log*"
      - rpicard
```

## File locations

A project folder can hold its configuration in three places. The first one that
exists is **active**: it is the only one read, and the only one every save,
history restore and quickstart writes.

| Precedence | File | Notes |
|---|---|---|
| 1 | `.alfrd/alfrd.yaml` | canonical; new projects (CLI and Studio) start here |
| 2 | `alfrd.yaml` | project root; still fully supported |
| 3 | `.alfrd.yaml` | legacy alias |

Lower files are *shadowed*: kept on disk, never read, never written, never
deleted. The project root is the folder that contains `.alfrd/`, so relative
paths (`plan_csv`, `targets.csv`, `avica.target_dir`) resolve the same way
wherever the file lives. Settings → Project shows the active path and says so
when a root file is being ignored.

Move an existing root file into `.alfrd/` (never done silently):

```bash
alfrd manifest migrate DIR --dry-run     # show the plan (and a diff when .alfrd/alfrd.yaml differs)
alfrd manifest migrate DIR               # copy to .alfrd/alfrd.yaml, old file -> alfrd.yaml.migrated
alfrd manifest migrate DIR --keep shadow # leave the old file in place (ignored)
alfrd manifest migrate DIR --force       # replace a different .alfrd/alfrd.yaml (kept as .bak)
```

The Studio offers the same under Settings → Project (**Move to .alfrd/…**).
`alfrd projects create DIR --config-location root` writes a root `alfrd.yaml`
instead of the default `.alfrd/alfrd.yaml`.

## Placeholders

- `{workdir}` – a work folder, e.g. `reductions/RDV41/wd`
- `{band}`, `{target}`, `{step}`, `{logs}`, `{meta_dir}`, `{target_dir}`
- `*` and `?` – wildcards inside one folder

## Tips

- A step can be just a name, or a mapping that overrides the template.
- Artifacts with `kind: log` show up in the **Logs** view.
- The AVICA template lives in `src/alfrd/web/assets/templates/avica.yaml`.
- Check a file: `alfrd manifest validate alfrd.yaml`
- In the Studio, **Settings → All settings** edits every key as a form. See the
  [Studio guide](studio-guide.md).

## AVICA helpers

```bash
alfrd avica summary                     # cache `avica pipe config --summary`
alfrd avica scan . --bundle scan.json   # pack a remote tree for the Studio
```

How folders map to Metadata panels: [Folder hierarchy and Metadata panels](template-views.md).

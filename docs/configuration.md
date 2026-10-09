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

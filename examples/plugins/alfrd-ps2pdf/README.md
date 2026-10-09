# ALFRD PostScript to PDF converter

Install Ghostscript so `gs` is on `PATH`, then from the ALFRD repository root:

```sh
alfrd plugin install ./examples/plugins/alfrd-ps2pdf --yes
alfrd serve
```

Restart a running `alfrd serve` after installing this Python converter. Open a
`.ps` or `.eps` file and choose **Convert to PDF**. EPS conversion crops to the
declared bounding box. The command uses an argument list, `-dSAFER`, and the host's
timeout; failures report a bounded Ghostscript error tail. ALFRD runs conversion
in a worker process and caches successful PDFs inside the project.

Without `gs`, Settings → Plugins shows `missing binary: gs` and the converter is
inactive. Install Ghostscript and restart the server to activate it.

No Python runtime dependencies are needed. Folder builds require Hatchling
(`hatchling>=1.25`); offline installs need that backend and its dependencies cached.
Alternatively build a wheel using `uv build --wheel ./examples/plugins/alfrd-ps2pdf`
before going offline. The offline integration test builds a wheel directly from
the example package files; it does not validate Hatchling or folder builds.

Run checks from the repository root:

```sh
uv --cache-dir /tmp/alfrd-uv-cache run --no-sync python -m pytest tests/test_plugin_examples.py -q
```

The real EPS conversion test skips when `gs` is unavailable.

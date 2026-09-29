"""Studio endpoints for collections (``kind: collection`` artifacts; see alfrd.artifact_collections).

Read-only and lazy: the run list, then one run's files, then one file. A file
is served only when it is a listed file of a declared run (checked on every
request), with ``nosniff`` and a restrictive CSP; SVG/HTML are never inline.
"""

from __future__ import annotations

from flask import jsonify, request, send_file

from alfrd import artifact_collections as ac

from .studio import _json_error, _project_root, studio_api


@studio_api.get("/studio/projects/<project_name>/collections")
def collections_list(project_name: str):
    """Declared collections; with ``?name=`` also that collection's runs (``code``, ``target``, ``band`` filter)."""
    root = _project_root(project_name)
    name = request.args.get("name")
    try:
        if not name:
            return jsonify(collections=[ac.public_spec(c) for c in ac.collections(root)])
        return jsonify(ac.list_runs(root, name, code=request.args.get("code") or None,
                                    target=request.args.get("target") or None, band=request.args.get("band") or None))
    except ac.CollectionError as error:
        return _json_error(error, 404)
    except OSError as error:
        return _json_error(error, 500)


@studio_api.get("/studio/projects/<project_name>/collections/<name>/files")
def collections_files(project_name: str, name: str):
    """Files of one run (``?run=<rel>``; ``&folder=`` limits the list to one sub folder)."""
    root = _project_root(project_name)
    folder = request.args.get("folder")
    try:
        return jsonify(ac.list_files(root, name, request.args.get("run", ""), folder=folder))
    except ac.CollectionError as error:
        return _json_error(error, 404)
    except OSError as error:
        return _json_error(error, 500)


@studio_api.get("/studio/projects/<project_name>/collections/<name>/file")
def collections_file(project_name: str, name: str):
    """One file of a run (``?run=&path=``; ``&download=1`` as an attachment)."""
    root = _project_root(project_name)
    try:
        path, media, inline = ac.resolve_file(root, name, request.args.get("run", ""), request.args.get("path", ""))
    except ac.CollectionError as error:
        return _json_error(error, 404)
    except FileNotFoundError as error:
        return _json_error(error, 404)
    attach = not inline or request.args.get("download") == "1"
    response = send_file(path, mimetype=media, as_attachment=attach, download_name=path.name, conditional=True, max_age=300)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if media != "application/pdf":  # browsers' PDF viewers refuse to run under a sandbox CSP
        response.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    return response

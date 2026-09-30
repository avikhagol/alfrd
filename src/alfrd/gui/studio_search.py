"""Studio full-text search (alfrd.search): read-only GETs.

    GET /api/studio/search?projects=a,b&q=…&limit=50      hits with entity paths and snippets
    GET /api/studio/search/context?project=&path=&line=   the lines around a hit

The first query of a project starts its index build in the background; until
it is ready the files are scanned directly (bounded), and ``index`` in the
reply says how far the build is. Without FTS5 in this Python's sqlite3 the scan
is all there is (``mode: scan``).
"""

from __future__ import annotations

import time

from flask import current_app, jsonify, request

from alfrd import search

from .studio import _json_error, _project_root, studio_api


def _identifier(selector: str) -> str:
    service = current_app.config.get("RUNTIME_SERVICE")
    return service.get_project_by_selector(selector).identifier


def _scope(names: list[str]) -> list[str]:
    scope = current_app.config.get("STUDIO_PROJECTS")
    if isinstance(scope, list):
        names = [n for n in names if n in scope]
    return names[:10]


@studio_api.get("/studio/search")
def studio_search():
    q = (request.args.get("q") or "").strip()
    limit = max(1, min(request.args.get("limit", default=50, type=int) or 50, 200))
    names = _scope([n for n in (request.args.get("projects") or "").split(",") if n])
    if not q:
        return _json_error(ValueError("q is required"), 400)
    t0 = time.perf_counter()
    hits, indexes = [], {}
    fts = search.fts5_available()
    for name in names:
        root = _project_root(name)
        identifier = _identifier(name)
        if fts:
            idx = search.ensure_built(root, identifier)
            state = idx.progress.get("state")
            indexes[identifier] = {k: idx.progress.get(k) for k in ("state", "files_done", "files_total")}
            if state in ("ready", "partial"):
                found, mode = idx.search(q, limit), "index"
            else:
                found, mode = search.scan(root, q, limit=limit, budget=3.0)["hits"], "scan"
        else:
            found, mode = search.scan(root, q, limit=limit, budget=5.0)["hits"], "scan"
            indexes[identifier] = {"state": "no-fts5"}
        for hit in found:
            hits.append({**hit, "project": identifier, "mode": mode,
                         "entity": {"project": identifier, "file": hit["rel"], "line": hit["line"]}})
    hits.sort(key=lambda h: -h.get("score", 0))
    return jsonify(q=q, hits=hits[:limit], took_ms=round((time.perf_counter() - t0) * 1000, 1),
                   index=indexes, fts5=fts)


@studio_api.get("/studio/search/context")
def studio_search_context():
    name = request.args.get("project") or ""
    root = _project_root(name)
    try:
        return jsonify(search.context(root, request.args.get("path") or "", request.args.get("line", default=1, type=int),
                                      around=min(request.args.get("around", default=40, type=int) or 40, 200)))
    except ValueError as error:
        return _json_error(error, 400)
    except FileNotFoundError as error:
        return _json_error(error, 404)


__all__: list[str] = []

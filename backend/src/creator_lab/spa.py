"""Serve the built React app. Any non-API path falls back to index.html so the
client-side router can handle it."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, send_from_directory

bp = Blueprint("spa", __name__)

NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>creator-lab</title>
<body style="font-family:system-ui;padding:2rem">
<h1>creator-lab</h1><p>The frontend is not built yet. In <code>frontend/</code> run
<code>corepack pnpm install</code> and <code>corepack pnpm build</code>, or use
<code>corepack pnpm dev</code> and open the Vite URL.</p></body>"""


@bp.get("/", defaults={"path": ""})
@bp.get("/<path:path>")
def index(path: str):
    if path.startswith("api/"):
        abort(404)
    dist = current_app.config["APP"].frontend_dist
    if path and (dist / path).is_file():
        resp = send_from_directory(dist, path)
        if path.startswith("assets/"):  # hashed file names; safe to cache forever
            resp.cache_control.max_age = 31536000
            resp.cache_control.immutable = True
        return resp
    if (dist / "index.html").is_file():
        resp = send_from_directory(dist, "index.html")
        resp.cache_control.no_cache = True
        return resp
    return NOT_BUILT, 200

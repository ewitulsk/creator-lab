"""Who may call what.

- Browser users log in with a session cookie (Flask-Login).
- Runners send `Authorization: Bearer <RUNNER_TOKEN>`.

Every /api view is login-only unless marked with one of the decorators below.
Session-authenticated writes must carry `X-Requested-With`, which a cross-site
form cannot set, so cookies alone cannot be used for CSRF.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable

from flask import current_app, g, request
from flask_login import current_user

from .config import is_set

PUBLIC = "public"
RUNNER = "runner"
RUNNER_OR_USER = "runner_or_user"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "X-Requested-With"


def _mark(kind: str) -> Callable:
    def deco(fn: Callable) -> Callable:
        fn._auth = kind
        return fn

    return deco


public = _mark(PUBLIC)
runner_only = _mark(RUNNER)
runner_or_user = _mark(RUNNER_OR_USER)


def bearer_is_valid() -> bool:
    token = current_app.config["APP"].runner_token
    header = request.headers.get("Authorization", "")
    if not is_set(token) or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:].strip().encode(), token.strip().encode())


def check_request() -> tuple[dict, int] | None:
    """Blueprint before_request hook. Returns an error response or None."""
    view = current_app.view_functions.get(request.endpoint or "")
    kind = getattr(view, "_auth", "user")
    g.via_runner = False
    if kind == PUBLIC:
        return None
    if kind in (RUNNER, RUNNER_OR_USER) and bearer_is_valid():
        g.via_runner = True
        return None
    if kind == RUNNER:
        return {"error": "runner token required"}, 401
    if not current_user.is_authenticated:
        return {"error": "login required"}, 401
    if request.method not in SAFE_METHODS and not request.headers.get(CSRF_HEADER):
        return {"error": f"missing {CSRF_HEADER} header"}, 400
    return None


def actor() -> str:
    if g.get("via_runner"):
        return "runner"
    return current_user.username if current_user.is_authenticated else "system"

"""creator-lab: a creator-analytics platform."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from flask import Flask
from sqlalchemy import event
from sqlalchemy.engine import Engine

from .config import BACKEND_DIR, Config
from .extensions import db, limiter, login_manager, migrate

__version__ = "0.1.0"
MIGRATIONS_DIR = BACKEND_DIR / "migrations"


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    if dbapi_conn.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")  # readers don't block the scheduler's writes
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()


def create_app(cfg: Config | None = None) -> Flask:
    cfg = cfg or Config.from_env()
    from .storage import ensure_layout

    ensure_layout(cfg.data_root)

    app = Flask(__name__, static_folder=None)
    app.config.update(
        APP=cfg,
        SECRET_KEY=cfg.secret_key or ("test-only-secret" if cfg.testing else None),
        SQLALCHEMY_DATABASE_URI=cfg.database_uri,
        SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"timeout": 30, "check_same_thread": False}},
        TESTING=cfg.testing,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_DURATION=dt.timedelta(days=30),
        RATELIMIT_ENABLED=not cfg.testing or cfg.extra.get("ratelimit", False),
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=64 * 1024 * 1024,
    )
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY is not set. Run `uv run creator-lab setup` in backend/.")

    db.init_app(app)
    migrate.init_app(app, db, directory=str(MIGRATIONS_DIR), render_as_batch=True)
    login_manager.init_app(app)
    limiter.init_app(app)

    from .models import User

    @login_manager.user_loader
    def _load_user(user_id: str):
        return db.session.get(User, int(user_id))

    @login_manager.unauthorized_handler
    def _unauthorized():
        return {"error": "login required"}, 401

    from .analyzers import load_builtin_specs
    from .api import bp as api_bp

    load_builtin_specs()
    from .spa import bp as spa_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(spa_bp)
    check_redirect_uri(cfg)
    return app


OAUTH_CALLBACK_PATH = "/api/youtube/oauth/callback"


def redirect_uri_problem(uri: str | None) -> str | None:
    """Why GOOGLE_OAUTH_REDIRECT_URI won't work, or None if it looks right."""
    import urllib.parse

    from .config import is_set

    if not is_set(uri):
        return None  # not configured; Settings says so
    parsed = urllib.parse.urlparse(uri.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return f"GOOGLE_OAUTH_REDIRECT_URI is not an absolute http(s) URL: {uri!r}"
    if parsed.path != OAUTH_CALLBACK_PATH:
        return f"GOOGLE_OAUTH_REDIRECT_URI path is {parsed.path!r}; it must be {OAUTH_CALLBACK_PATH!r}"
    if parsed.query or parsed.fragment:
        return "GOOGLE_OAUTH_REDIRECT_URI must not have a query string or fragment"
    return None


def check_redirect_uri(cfg: Config) -> None:
    problem = redirect_uri_problem(cfg.google_redirect_uri)
    if problem:
        logging.getLogger("creator_lab").warning("%s (sign-in with YouTube will fail)", problem)


def setup_logging(level: int = logging.INFO, logfile: Path | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if logfile:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    logging.basicConfig(
        level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers, force=True
    )


def main() -> None:
    from .cli import main as cli_main

    cli_main()

"""Command line: `uv run creator-lab <command>` from backend/.

setup      create backend/.env if missing, generate SECRET_KEY and RUNNER_TOKEN,
           create the data folders and migrate the database
serve      migrate, then serve the API and the built frontend (and run the scheduler)
migrate    apply database migrations only
"""

from __future__ import annotations

import argparse
import logging
import re
import secrets
import shutil
import sys

from .config import ENV_FILE, REPO_ROOT, Config, is_set

log = logging.getLogger("creator_lab")


def set_env_values(path, values: dict[str, str]) -> None:
    """Replace KEY=... lines in a .env file (or append them), keeping everything else."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    for key, value in values.items():
        line = f"{key}={value}"
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        text = pattern.sub(line, text) if pattern.search(text) else text.rstrip("\n") + f"\n{line}\n"
    path.write_text(text, encoding="utf-8")


def cmd_setup(_args) -> None:
    if not ENV_FILE.exists():
        shutil.copy(REPO_ROOT / ".env.example", ENV_FILE)
        print(f"Created {ENV_FILE} from .env.example")
    cfg = Config.from_env()
    generated = {}
    if not is_set(cfg.secret_key):
        generated["SECRET_KEY"] = secrets.token_urlsafe(48)
    if not is_set(cfg.runner_token):
        generated["RUNNER_TOKEN"] = secrets.token_urlsafe(32)
    if generated:
        set_env_values(ENV_FILE, generated)
        print("Generated " + " and ".join(generated) + f" in {ENV_FILE}")
    cfg = Config.from_env()
    cmd_migrate(None)
    missing = [
        k
        for k, v in [("ADMIN_PASSWORD", cfg.admin_password), ("YOUTUBE_API_KEY", cfg.youtube_api_key)]
        if not is_set(v)
    ]
    if missing:
        print("Still to fill in " + str(ENV_FILE) + ": " + ", ".join(missing))
    print(f"Data folder: {cfg.data_root}")


def cmd_migrate(_args) -> None:
    from flask_migrate import upgrade

    from . import create_app
    from .bootstrap import bootstrap

    app = create_app()
    with app.app_context():
        upgrade()
        bootstrap(app.config["APP"])
    print("Database is up to date.")


def cmd_serve(args) -> None:
    from flask_migrate import upgrade

    from . import create_app, setup_logging
    from .bootstrap import bootstrap

    cfg = Config.from_env()
    setup_logging(logfile=cfg.data_root / "logs" / "server.log")
    app = create_app(cfg)
    with app.app_context():
        upgrade()
        bootstrap(cfg)
    if not args.no_scheduler and cfg.start_scheduler:
        from .scheduler import start_scheduler

        start_scheduler(app)
    from .levers import worker as levers_worker

    with app.app_context():
        levers_worker.start(app)
    log.info("Serving on http://%s:%s (data in %s)", args.host, args.port, cfg.data_root)
    # Threaded werkzeug is enough for one user on a desktop and handles SSE well.
    app.run(host=args.host, port=args.port, threaded=True, debug=False, use_reloader=False)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(
        prog="creator-lab", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup").set_defaults(fn=cmd_setup)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=5050)
    s.add_argument("--no-scheduler", action="store_true")
    s.set_defaults(fn=cmd_serve)
    from .commands import register_commands

    register_commands(sub)
    args = p.parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        sys.exit(130)

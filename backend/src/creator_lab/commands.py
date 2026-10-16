"""Extra CLI commands."""

from __future__ import annotations


def _app():
    from . import create_app, setup_logging

    setup_logging()
    return create_app()


def cmd_collect(_args) -> None:
    """Run one collection pass now (snapshots, new uploads, thumbnails)."""
    from . import tasks

    app = _app()
    with app.app_context():
        tasks.collect(app)
        tasks.thumbnails(app)


def register_commands(sub) -> None:
    sub.add_parser("collect", help="run one collection pass now").set_defaults(fn=cmd_collect)

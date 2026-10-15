"""Run slow in-app work (channel backfills) off the request thread."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, current_app

from .extensions import db

log = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bg")
_running: set[str] = set()
_lock = threading.Lock()


def submit(key: str, fn, *args) -> bool:
    """Run fn(*args) in an app context in the background, once per key at a time.
    In tests it runs inline. Returns False if a task with this key is already running."""
    app: Flask = current_app._get_current_object()
    if app.config.get("TESTING"):
        fn(*args)
        return True
    with _lock:
        if key in _running:
            return False
        _running.add(key)

    def run():
        try:
            with app.app_context():
                fn(*args)
        except Exception:
            log.exception("background task %s failed", key)
        finally:
            with app.app_context():
                db.session.remove()
            with _lock:
                _running.discard(key)

    _executor.submit(run)
    return True


def is_running(key: str) -> bool:
    with _lock:
        return key in _running

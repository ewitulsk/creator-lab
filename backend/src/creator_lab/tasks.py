"""Scheduled collection: snapshots, new uploads and thumbnails."""

from __future__ import annotations

import logging

from flask import Flask

from . import ingest
from .config import is_set
from .extensions import db
from .models import utcnow
from .scheduler import task
from .youtube.quota import QuotaExceeded

log = logging.getLogger(__name__)


@task("collect", minutes=15)
def collect(app: Flask) -> None:
    if not is_set(app.config["APP"].youtube_api_key):
        return
    try:
        n = ingest.snapshot_channels()
        if n:
            log.info("snapshotted %d channels", n)
        for ch in ingest.channels_due_for_upload_check(utcnow()):
            try:
                new = ingest.sync_new_uploads(ch.id)
                if new:
                    log.info("%s: %d new uploads", ch.title, len(new))
            except QuotaExceeded:
                raise
            except Exception as err:
                log.warning("upload check failed for %s: %s", ch.title, err)
                db.session.rollback()
                ch.sync_error = str(err)[:1000]
                db.session.commit()
        n = ingest.snapshot_videos()
        if n:
            log.info("snapshotted %d videos", n)
    except QuotaExceeded as err:
        log.warning("%s; collection paused until the quota resets", err)


@task("thumbnails", minutes=10)
def thumbnails(_app: Flask) -> None:
    n = ingest.fetch_missing_thumbnails(limit=300)
    if n:
        log.info("fetched %d thumbnails", n)


@task("requeue_stale_jobs", minutes=1)
def requeue_stale(_app: Flask) -> None:
    from .jobs import requeue_stale_jobs

    n = requeue_stale_jobs()
    if n:
        log.warning("requeued %d jobs whose runner went silent", n)


@task("backup_database", minutes=30)
def backup_database(_app: Flask) -> None:
    from . import backups

    if backups.due():
        backups.backup_now()

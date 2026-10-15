"""The store: every tracked channel, video, snapshot, asset, analyzer result, job and study."""

from __future__ import annotations

import datetime as dt
from typing import Any

from flask_login import UserMixin
from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow() -> dt.datetime:
    # Stored naive in UTC; SQLite has no time zones.
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def iso(value: dt.datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") + "Z" if value else None


# ------------------------------------------------------------------ users and settings


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    password_changed_at: Mapped[dt.datetime | None]

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)
        self.password_changed_at = utcnow()

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        return {"id": self.id, "username": self.username}


class Setting(db.Model):
    """Editable app settings (schedules and the like). Secrets stay in .env."""

    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ApiUsage(db.Model):
    """YouTube Data API quota units spent per (Pacific) day."""

    __tablename__ = "api_usage"
    day: Mapped[dt.date] = mapped_column(primary_key=True)
    api: Mapped[str] = mapped_column(String(40), primary_key=True, default="youtube_data")
    units: Mapped[int] = mapped_column(default=0)
    calls: Mapped[int] = mapped_column(default=0)


# ------------------------------------------------------------------ channels and videos


class Platform(db.Model):
    __tablename__ = "platforms"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))


class Channel(db.Model):
    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("platform_id", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    external_id: Mapped[str] = mapped_column(String(64))  # UC... for YouTube
    handle: Mapped[str | None] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(8))
    thumbnail_url: Mapped[str | None] = mapped_column(String(500))
    uploads_playlist: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[dt.datetime | None]
    # Followed: daily channel stats and new-upload checks. Opening a channel in
    # Discover stores it without following it.
    followed: Mapped[bool] = mapped_column(default=False)
    # Pull new uploads automatically (track + media jobs). Off unless Evan turns it on.
    auto_track_new: Mapped[bool] = mapped_column(default=False)
    is_mine: Mapped[bool] = mapped_column(default=False)  # Evan's own channels
    tags: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    backfilled_at: Mapped[dt.datetime | None]  # last full uploads listing
    last_synced_at: Mapped[dt.datetime | None]
    last_snapshot_at: Mapped[dt.datetime | None]
    sync_error: Mapped[str | None] = mapped_column(Text)

    platform: Mapped[Platform] = relationship()
    videos: Mapped[list[Video]] = relationship(back_populates="channel", cascade="all, delete-orphan")
    snapshots: Mapped[list[ChannelSnapshot]] = relationship(
        back_populates="channel", cascade="all, delete-orphan", order_by="ChannelSnapshot.captured_at"
    )


class ChannelSnapshot(db.Model):
    __tablename__ = "channel_snapshots"
    __table_args__ = (Index("ix_channel_snapshots_channel_time", "channel_id", "captured_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    subscribers: Mapped[int | None]
    views: Mapped[int | None]
    video_count: Mapped[int | None]
    captured_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    channel: Mapped[Channel] = relationship(back_populates="snapshots")


VIDEO_FORMATS = ("long", "short", "live", "premiere")
# discovered: metadata only (listed while browsing). tracked: Evan pulled it, so it gets
# scheduled stats snapshots and media.
SELECTIONS = ("discovered", "tracked")


class Video(db.Model):
    __tablename__ = "videos"
    __table_args__ = (
        UniqueConstraint("platform_id", "external_id"),
        Index("ix_videos_channel_published", "channel_id", "published_at"),
        Index("ix_videos_selection", "selection"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    external_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(500), default="")
    description: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[dt.datetime | None]
    duration_s: Mapped[int | None]
    format: Mapped[str] = mapped_column(String(16), default="long")
    aspect: Mapped[float | None]  # width / height; < 1 is vertical
    tags: Mapped[list] = mapped_column(JSON, default=list)
    category_id: Mapped[str | None] = mapped_column(String(16))
    language: Mapped[str | None] = mapped_column(String(16))
    privacy: Mapped[str | None] = mapped_column(String(16))
    thumbnail_url: Mapped[str | None] = mapped_column(String(500))
    discovered_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    selection: Mapped[str] = mapped_column(String(16), default="discovered")
    tracked_at: Mapped[dt.datetime | None]
    metadata_at: Mapped[dt.datetime | None]
    last_snapshot_at: Mapped[dt.datetime | None]
    # Latest stats, denormalized from video_snapshots for fast sorting.
    views: Mapped[int | None]
    likes: Mapped[int | None]
    comments: Mapped[int | None]
    outlier_score: Mapped[float | None]
    outlier_baseline: Mapped[float | None]
    gone: Mapped[bool] = mapped_column(default=False)  # deleted or made private

    channel: Mapped[Channel] = relationship(back_populates="videos")
    snapshots: Mapped[list[VideoSnapshot]] = relationship(
        back_populates="video", cascade="all, delete-orphan", order_by="VideoSnapshot.captured_at"
    )
    assets: Mapped[list[Asset]] = relationship(back_populates="video", cascade="all, delete-orphan")

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.external_id}"


class VideoSnapshot(db.Model):
    __tablename__ = "video_snapshots"
    __table_args__ = (Index("ix_video_snapshots_video_time", "video_id", "captured_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    views: Mapped[int | None]
    likes: Mapped[int | None]
    comments: Mapped[int | None]
    captured_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    source: Mapped[str] = mapped_column(String(40), default="youtube_data_api")
    video: Mapped[Video] = relationship(back_populates="snapshots")


ASSET_KINDS = ("video", "audio", "thumbnail", "captions", "captions_text", "title_text", "info")


class Asset(db.Model):
    """A file on disk that belongs to a video. Paths are relative to DATA_ROOT."""

    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("video_id", "kind", "variant"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    variant: Mapped[str] = mapped_column(String(32), default="")  # e.g. caption language
    path: Mapped[str] = mapped_column(String(500))
    bytes: Mapped[int | None]
    sha256: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int | None]
    height: Mapped[int | None]
    codec: Mapped[str | None] = mapped_column(String(40))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    video: Mapped[Video] = relationship(back_populates="assets")


# ------------------------------------------------------------------ analyzers and jobs

RESULT_STATUSES = ("done", "queued", "running", "failed", "missing")


class Analyzer(db.Model):
    __tablename__ = "analyzers"
    __table_args__ = (UniqueConstraint("name", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    version: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    inputs: Mapped[list] = mapped_column(JSON, default=list)  # other analyzer names or asset kinds
    runner_type: Mapped[str] = mapped_column(String(20))  # builtin | cpu | gpu
    output_format: Mapped[str] = mapped_column(String(80), default="json")
    applies_to: Mapped[list] = mapped_column(JSON, default=list)  # video formats; empty = all
    is_current: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


class AnalysisResult(db.Model):
    __tablename__ = "analysis_results"
    __table_args__ = (
        UniqueConstraint("video_id", "analyzer_id"),
        Index("ix_analysis_results_analyzer_status", "analyzer_id", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    analyzer_id: Mapped[int] = mapped_column(ForeignKey("analyzers.id"))
    status: Mapped[str] = mapped_column(String(16), default="missing")
    output_path: Mapped[str | None] = mapped_column(String(500))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)
    computed_at: Mapped[dt.datetime | None]
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    analyzer: Mapped[Analyzer] = relationship()
    video: Mapped[Video] = relationship()


JOB_STATUSES = ("queued", "claimed", "running", "done", "failed", "cancelled")
ACTIVE_JOB_STATUSES = ("queued", "claimed", "running")


class Job(db.Model):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_type", "status", "job_type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    analyzer_id: Mapped[int] = mapped_column(ForeignKey("analyzers.id"))
    job_type: Mapped[str] = mapped_column(String(80))  # analyzer name; runners claim by this
    runner_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    priority: Mapped[int] = mapped_column(default=0)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    claimed_by: Mapped[int | None] = mapped_column(ForeignKey("runners.id", ondelete="SET NULL"))
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    stage_timings: Mapped[list] = mapped_column(JSON, default=list)
    log_path: Mapped[str | None] = mapped_column(String(500))
    log_tail: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    claimed_at: Mapped[dt.datetime | None]
    started_at: Mapped[dt.datetime | None]
    finished_at: Mapped[dt.datetime | None]
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    analyzer: Mapped[Analyzer] = relationship()
    runner: Mapped[Runner | None] = relationship()
    items: Mapped[list[JobItem]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobItem.id"
    )


class JobItem(db.Model):
    __tablename__ = "job_items"
    __table_args__ = (UniqueConstraint("job_id", "video_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    stage: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime | None]
    finished_at: Mapped[dt.datetime | None]
    job: Mapped[Job] = relationship(back_populates="items")
    video: Mapped[Video] = relationship()


class Runner(db.Model):
    __tablename__ = "runners"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    type: Mapped[str] = mapped_column(String(20))  # cpu | gpu
    gpu: Mapped[str | None] = mapped_column(String(120))
    capabilities: Mapped[list] = mapped_column(JSON, default=list)  # job types it can run
    host: Mapped[str | None] = mapped_column(String(120))
    version: Mapped[str | None] = mapped_column(String(40))
    info: Mapped[dict] = mapped_column(JSON, default=dict)  # VRAM, free disk, etc.
    registered_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    last_heartbeat: Mapped[dt.datetime | None]


# ------------------------------------------------------------------ studies

STUDY_STATUSES = ("draft", "locked", "collecting", "analyzed")


class Study(db.Model):
    __tablename__ = "studies"
    __table_args__ = (UniqueConstraint("slug", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(default=1)
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")
    methodology_md: Mapped[str] = mapped_column(Text, default="")
    criteria: Mapped[dict] = mapped_column(JSON, default=dict)
    measures: Mapped[list] = mapped_column(JSON, default=list)
    requires: Mapped[list] = mapped_column(JSON, default=list)
    module: Mapped[str] = mapped_column(String(120))
    locked_at: Mapped[dt.datetime | None]
    locked_hash: Mapped[str | None] = mapped_column(String(64))
    locked_by: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    members: Mapped[list[StudyVideo]] = relationship(
        back_populates="study", cascade="all, delete-orphan", order_by="StudyVideo.id"
    )
    results: Mapped[list[StudyResult]] = relationship(
        back_populates="study", cascade="all, delete-orphan", order_by="StudyResult.computed_at"
    )


class StudyVideo(db.Model):
    __tablename__ = "study_videos"
    __table_args__ = (UniqueConstraint("study_id", "video_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"))
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(40))  # e.g. hit / flop
    group: Mapped[str | None] = mapped_column(String(80))  # e.g. pair id
    reason: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    added_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    study: Mapped[Study] = relationship(back_populates="members")
    video: Mapped[Video] = relationship()


class StudyResult(db.Model):
    __tablename__ = "study_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"))
    code_version: Mapped[str] = mapped_column(String(80))
    module_hash: Mapped[str | None] = mapped_column(String(64))
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    figures: Mapped[list] = mapped_column(JSON, default=list)
    n_members: Mapped[int | None]
    computed_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    study: Mapped[Study] = relationship(back_populates="results")

"""'Sync data from YT': everything the owner's OAuth grant exposes, for every upload.

No schedule. Each run is a versioned OwnerSync:
  mode "full": every upload, a new retention snapshot version (old ones are kept)
  mode "new":  uploads published since the last finished sync, plus any never synced

Per video:
  Data API     full videos.list item (owner parts: fileDetails, processingDetails, ...)
  lifetime     views, watch time, average view duration/percentage, likes, dislikes,
               comments, shares, subscribers gained/lost, card metrics; impressions and
               CTR when the API accepts them (they're usually not exposed)
  daily        the core metrics per day since upload
  retention    elapsedVideoTimeRatio x (audienceWatchRatio, relativeRetentionPerformance),
               for all viewers and per audience type where available
  breakdowns   traffic source, playback location, device, country, age x gender,
               subscribed status
Every response is stored raw (analytics_reports) and parsed into typed tables.
"""

from __future__ import annotations

import datetime as dt
import logging

from . import ingest
from .extensions import db
from .models import (
    AnalyticsReport,
    Channel,
    OwnerCredential,
    OwnerSync,
    RetentionPoint,
    Video,
    VideoBreakdown,
    VideoDailyStat,
    VideoLifetimeStat,
    utcnow,
)
from .settings import get_setting
from .youtube import oauth
from .youtube.analytics import AnalyticsClient, AnalyticsError, rows_as_dicts
from .youtube.client import VIDEO_PARTS, YouTubeClient
from .youtube.quota import pacific_today

log = logging.getLogger(__name__)

OWNER_VIDEO_PARTS = VIDEO_PARTS + ",recordingDetails,localizations,fileDetails,processingDetails"
LIFETIME_METRICS = [
    "views",
    "estimatedMinutesWatched",
    "averageViewDuration",
    "averageViewPercentage",
    "likes",
    "dislikes",
    "comments",
    "shares",
    "subscribersGained",
    "subscribersLost",
    "videosAddedToPlaylists",
    "videosRemovedFromPlaylists",
    "cardImpressions",
    "cardClicks",
    "cardClickRate",
    "cardTeaserImpressions",
    "cardTeaserClicks",
    "cardTeaserClickRate",
]
IMPRESSION_METRICS = ["videoThumbnailImpressions", "videoThumbnailImpressionsClickRate"]
DAILY_METRICS = [
    "views",
    "estimatedMinutesWatched",
    "averageViewDuration",
    "averageViewPercentage",
    "likes",
    "dislikes",
    "comments",
    "shares",
    "subscribersGained",
    "subscribersLost",
]
RETENTION_METRICS = ["audienceWatchRatio", "relativeRetentionPerformance"]
AUDIENCE_TYPES = ["ORGANIC", "AD_INSTREAM", "AD_INDISPLAY"]
BREAKDOWNS = {
    "traffic_source": ("insightTrafficSourceType", ["views", "estimatedMinutesWatched"]),
    "playback_location": ("insightPlaybackLocationType", ["views", "estimatedMinutesWatched"]),
    "device": ("deviceType", ["views", "estimatedMinutesWatched"]),
    "country": ("country", ["views", "estimatedMinutesWatched", "averageViewDuration"]),
    "age_gender": ("ageGroup,gender", ["viewerPercentage"]),
    "subscribed_status": ("subscribedStatus", ["views", "estimatedMinutesWatched", "averageViewDuration"]),
}
DAILY_COLUMNS = {
    "views": "views",
    "estimatedMinutesWatched": "estimated_minutes_watched",
    "averageViewDuration": "average_view_duration",
    "averageViewPercentage": "average_view_percentage",
    "likes": "likes",
    "dislikes": "dislikes",
    "comments": "comments",
    "shares": "shares",
    "subscribersGained": "subscribers_gained",
    "subscribersLost": "subscribers_lost",
}


class SyncLog:
    def __init__(self, sync: OwnerSync):
        self.sync = sync

    def __call__(self, msg: str) -> None:
        log.info("owner sync #%s: %s", self.sync.id, msg)
        self.sync.log = [*self.sync.log, f"{utcnow().isoformat(timespec='seconds')}Z {msg}"][-500:]

    def count(self, key: str, n: int = 1) -> None:
        self.sync.counts = {**self.sync.counts, key: self.sync.counts.get(key, 0) + n}


def _date(d: dt.datetime | dt.date | None) -> str:
    if d is None:
        return "2005-02-14"  # YouTube's first day
    return (d.date() if isinstance(d, dt.datetime) else d).isoformat()


def start_sync(channel_id: int, mode: str, started_by: str) -> OwnerSync:
    if mode not in ("full", "new"):
        raise ValueError("mode must be full or new")
    running = db.session.scalar(
        db.select(OwnerSync).where(OwnerSync.channel_id == channel_id, OwnerSync.status == "running")
    )
    if running is not None:
        raise ValueError(f"sync #{running.id} is already running")
    sync = OwnerSync(channel_id=channel_id, mode=mode, status="running", started_by=started_by, counts={}, log=[])
    db.session.add(sync)
    db.session.commit()
    return sync


def run_sync(sync_id: int) -> None:
    sync = db.session.get(OwnerSync, sync_id)
    say = SyncLog(sync)
    try:
        _run(sync, say)
        sync.status = "done"
        say(f"finished: {sync.counts}")
    except Exception as err:
        db.session.rollback()
        sync = db.session.get(OwnerSync, sync_id)
        sync.status = "failed"
        sync.error = f"{type(err).__name__}: {err}"[:2000]
        SyncLog(sync)(f"failed: {sync.error}")
        log.exception("owner sync #%s failed", sync_id)
    sync.finished_at = utcnow()
    db.session.commit()


def _run(sync: OwnerSync, say: SyncLog) -> None:
    ch = db.session.get(Channel, sync.channel_id)
    cred = db.session.scalar(db.select(OwnerCredential).where(OwnerCredential.channel_id == ch.id))
    if cred is None:
        raise oauth.ReauthRequired("this channel is not connected; sign in with YouTube first")

    def token() -> str:
        return oauth.access_token(cred)

    def store(report: str, video_id: int | None, api: str):
        def on_call(params, payload, error):
            db.session.add(
                AnalyticsReport(
                    sync_id=sync.id,
                    video_id=video_id,
                    report=report,
                    api=api,
                    params=params,
                    payload=payload or {},
                    error=error,
                )
            )

        return on_call

    data = YouTubeClient(
        None, budget=lambda: get_setting("daily_quota_budget"), transport=oauth._transport, access_token=token()
    )
    me = data._get("channels", {"part": "snippet,contentDetails,statistics,brandingSettings,status", "mine": "true"})
    items = me.get("items") or []
    if not items or items[0]["id"] != ch.external_id:
        raise oauth.ReauthRequired("the signed-in Google account doesn't own this channel")
    ingest.apply_channel_item(ch, items[0])
    ingest.snapshot_channel_item(ch, items[0], utcnow())
    store("channels.list:mine", None, "data")({"mine": "true"}, items[0], None)
    db.session.commit()

    # Every upload, including private and unlisted ones (the owner can list them).
    now = utcnow()
    listed = []
    for ids in data.playlist_video_ids(ch.uploads_playlist or ""):
        payload = data._get("videos", {"part": OWNER_VIDEO_PARTS, "id": ",".join(ids), "maxResults": 50})
        batch = payload.get("items") or []
        for it in batch:
            listed.append(it)
        videos = ingest.upsert_videos(ch, batch, now, shorts_check=data.is_short)
        by_ext = {v.external_id: v for v in videos}
        for it in batch:
            store("videos.list", by_ext[it["id"]].id, "data")({"id": it["id"], "part": OWNER_VIDEO_PARTS}, it, None)
        db.session.commit()
    say.count("videos_listed", len(listed))
    say(f"listed {len(listed)} uploads")

    ext_ids = [it["id"] for it in listed]
    videos = list(db.session.scalars(db.select(Video).where(Video.channel_id == ch.id, Video.external_id.in_(ext_ids))))
    if sync.mode == "new":
        last = db.session.scalar(
            db.select(OwnerSync)
            .where(OwnerSync.channel_id == ch.id, OwnerSync.status == "done", OwnerSync.id != sync.id)
            .order_by(OwnerSync.started_at.desc())
        )
        synced = set(db.session.scalars(db.select(VideoLifetimeStat.video_id).distinct()))
        cutoff = last.started_at if last else None
        videos = [v for v in videos if v.id not in synced or (cutoff and v.published_at and v.published_at >= cutoff)]
    say(f"{len(videos)} videos to sync ({sync.mode})")

    # Owned videos are analysis targets: track them and queue downloads (1080p cap).
    for v in videos:
        if v.selection != "tracked":
            v.selection, v.tracked_at = "tracked", now
    db.session.commit()
    from .jobs import queue_media_jobs

    queue_media_jobs([v.id for v in videos], created_by=f"owner sync #{sync.id}")

    end = _date(pacific_today())
    accepted: dict[str, list[str]] = {}
    unavailable: dict[str, list[str]] = dict(sync.unavailable or {})
    for i, v in enumerate(videos, 1):
        try:
            _sync_video(v, sync, token, store, accepted, unavailable, end, say)
            say.count("videos_synced")
        except oauth.ReauthRequired:
            raise
        except Exception as err:
            db.session.rollback()
            say.count("videos_failed")
            say(f"{v.external_id}: {type(err).__name__}: {err}")
        sync.unavailable = unavailable
        if i % 5 == 0 or i == len(videos):
            say(f"synced {i} of {len(videos)} videos")
        db.session.commit()
    if any(m in unavailable.get("lifetime", []) for m in IMPRESSION_METRICS):
        say("impressions and click-through rate are not available from the Analytics API for this channel")


def _sync_video(v, sync, token, store, accepted, unavailable, end, say) -> None:
    start = _date(v.published_at)
    flt = f"video=={v.external_id}"

    def client(report: str) -> AnalyticsClient:
        return AnalyticsClient(token, transport=oauth._transport, on_call=store(report, v.id, "analytics"))

    def degrading(report: str, metrics: list[str], **kwargs):
        if report in accepted:
            if not accepted[report]:
                return None
            return client(report).report(metrics=accepted[report], **kwargs)
        payload, rejected = client(report).report_degrading(metrics, **kwargs)
        accepted[report] = [m for m in metrics if m not in rejected]
        if rejected:
            unavailable[report] = sorted(set(unavailable.get(report, [])) | set(rejected))
        return payload

    # Lifetime totals (impression metrics are probed separately so they can't sink the rest).
    life = degrading("lifetime", LIFETIME_METRICS, start=start, end=end, filters=flt)
    metrics = rows_as_dicts(life)[0] if life and life.get("rows") else {}
    imp = degrading("impressions", IMPRESSION_METRICS, start=start, end=end, filters=flt)
    if imp and imp.get("rows"):
        metrics |= rows_as_dicts(imp)[0]
    db.session.add(VideoLifetimeStat(video_id=v.id, sync_id=sync.id, metrics=metrics))

    # Daily series.
    daily = degrading("daily", DAILY_METRICS, start=start, end=end, dimensions="day", filters=flt, sort="day")
    for row in rows_as_dicts(daily) if daily else []:
        day = dt.date.fromisoformat(row.pop("day"))
        rec = db.session.scalar(
            db.select(VideoDailyStat).where(VideoDailyStat.video_id == v.id, VideoDailyStat.day == day)
        )
        if rec is None:
            rec = VideoDailyStat(video_id=v.id, day=day)
            db.session.add(rec)
        for api_name, value in row.items():
            if api_name in DAILY_COLUMNS:
                setattr(rec, DAILY_COLUMNS[api_name], value)
        rec.sync_id = sync.id
    say.count("daily_rows", len(rows_as_dicts(daily)) if daily else 0)

    # Audience retention, all viewers and per audience type.
    for audience in ["ALL", *AUDIENCE_TYPES]:
        report = f"retention:{audience}"
        f = flt if audience == "ALL" else f"{flt};audienceType=={audience}"
        try:
            payload = client(report).report(
                start=start, end=end, metrics=RETENTION_METRICS, dimensions="elapsedVideoTimeRatio", filters=f
            )
        except AnalyticsError as err:
            if err.status == 400:
                unavailable["retention"] = sorted(set(unavailable.get("retention", [])) | {audience})
                continue
            raise
        rows = rows_as_dicts(payload)
        for row in rows:
            db.session.add(
                RetentionPoint(
                    video_id=v.id,
                    sync_id=sync.id,
                    audience_type=audience,
                    elapsed_ratio=float(row["elapsedVideoTimeRatio"]),
                    audience_watch_ratio=row.get("audienceWatchRatio"),
                    relative_retention_performance=row.get("relativeRetentionPerformance"),
                    video_duration_s=v.duration_s,
                )
            )
        if rows:
            say.count("retention_curves")

    # Breakdowns.
    for name, (dims, mets) in BREAKDOWNS.items():
        try:
            payload = client(f"breakdown:{name}").report(
                start=start, end=end, metrics=mets, dimensions=dims, filters=flt
            )
        except AnalyticsError as err:
            if err.status == 400:
                unavailable["breakdowns"] = sorted(set(unavailable.get("breakdowns", [])) | {name})
                continue
            raise
        keys = dims.split(",")
        for row in rows_as_dicts(payload):
            key = " / ".join(str(row.pop(k)) for k in keys)
            db.session.add(VideoBreakdown(video_id=v.id, sync_id=sync.id, dimension=name, key=key, metrics=row))

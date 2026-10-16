"""Collect channels, videos and stats from the YouTube Data API into the store.

Ported from Dataset/build_dataset.py's listing logic, but on the Data API instead of
scraping: the uploads playlist (playlistItems.list) plus batched videos.list.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Iterable

import httpx
from flask import current_app

from .extensions import db
from .models import Asset, Channel, ChannelSnapshot, Platform, Video, VideoSnapshot, utcnow
from .settings import get_setting
from .storage import media_dir, rel
from .youtube.client import YouTubeClient, YouTubeError, parse_channel_input

log = logging.getLogger(__name__)

SHORT_MAX_SECONDS = 180
DURATION_RE = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")

_client_factory = None  # tests swap this for a fake


def get_client() -> YouTubeClient:
    if _client_factory is not None:
        return _client_factory()
    cfg = current_app.config["APP"]
    return YouTubeClient(cfg.youtube_api_key or "", budget=lambda: get_setting("daily_quota_budget"))


def set_client_factory(factory) -> None:
    global _client_factory
    _client_factory = factory


# ------------------------------------------------------------------ parsing


def parse_duration(text: str | None) -> int | None:
    """ISO 8601 duration (PT1H2M3S) to seconds."""
    if not text:
        return None
    m = DURATION_RE.fullmatch(text)
    if not m:
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def parse_time(text: str | None) -> dt.datetime | None:
    if not text:
        return None
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(dt.UTC).replace(tzinfo=None)


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def best_thumbnail(thumbs: dict | None) -> str | None:
    if not thumbs:
        return None
    for key in ("maxres", "standard", "high", "medium", "default"):
        if key in thumbs:
            return thumbs[key]["url"]
    return None


def aspect_from_player(player: dict | None) -> float | None:
    w, h = _int((player or {}).get("embedWidth")), _int((player or {}).get("embedHeight"))
    return round(w / h, 4) if w and h else None


def classify_format(item: dict, duration: int | None, aspect: float | None, shorts_check=None) -> str:
    """long | short | live | premiere.

    - Anything with liveStreamingDetails is a stream or a premiere; the Data API cannot
      tell those apart reliably, so both are 'live' here (the CPU runner refines it to
      'premiere' from yt-dlp metadata when it downloads). Either way they are excluded
      from long-form baselines.
    - Shorts are at most 180 s and vertical. youtube.com/shorts/<id> is checked when
      available because some vertical uploads are not Shorts.
    """
    if "liveStreamingDetails" in item or item.get("snippet", {}).get("liveBroadcastContent") in (
        "live",
        "upcoming",
    ):
        return "live"
    if duration is not None and duration <= SHORT_MAX_SECONDS:
        verdict = shorts_check(item["id"]) if shorts_check else None
        if verdict is not None:
            return "short" if verdict else "long"
        if aspect is not None:
            return "short" if aspect < 1 else "long"
        return "short" if duration <= 60 else "long"
    return "long"


# ------------------------------------------------------------------ channels


def youtube_platform() -> Platform:
    return db.session.scalar(db.select(Platform).where(Platform.slug == "youtube"))


def apply_channel_item(ch: Channel, item: dict) -> None:
    sn = item.get("snippet", {})
    ch.title = sn.get("title") or ch.title
    ch.description = sn.get("description")
    ch.handle = sn.get("customUrl") or ch.handle
    if ch.handle and not ch.handle.startswith("@"):
        ch.handle = "@" + ch.handle
    ch.country = sn.get("country") or (item.get("brandingSettings", {}).get("channel", {}).get("country"))
    ch.thumbnail_url = best_thumbnail(sn.get("thumbnails"))
    ch.published_at = parse_time(sn.get("publishedAt"))
    ch.uploads_playlist = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")


def channel_card(item: dict) -> dict:
    """Search/lookup result shape: enough to pick a channel."""
    sn, st = item.get("snippet", {}), item.get("statistics", {})
    handle = sn.get("customUrl")
    if handle and not handle.startswith("@"):
        handle = "@" + handle
    return {
        "external_id": item["id"],
        "title": sn.get("title") or "",
        "handle": handle,
        "description": (sn.get("description") or "")[:300],
        "thumbnail_url": best_thumbnail(sn.get("thumbnails")),
        "subscribers": None if st.get("hiddenSubscriberCount") else _int(st.get("subscriberCount")),
        "video_count": _int(st.get("videoCount")),
        "total_views": _int(st.get("viewCount")),
    }


def snapshot_channel_item(ch: Channel, item: dict, now: dt.datetime) -> None:
    st = item.get("statistics", {})
    db.session.add(
        ChannelSnapshot(
            channel_id=ch.id,
            subscribers=None if st.get("hiddenSubscriberCount") else _int(st.get("subscriberCount")),
            views=_int(st.get("viewCount")),
            video_count=_int(st.get("videoCount")),
            captured_at=now,
        )
    )
    ch.last_snapshot_at = now


def find_channel(external_id: str) -> Channel | None:
    return db.session.scalar(
        db.select(Channel).where(Channel.platform_id == youtube_platform().id, Channel.external_id == external_id)
    )


def add_channel(
    text: str,
    *,
    follow: bool | None = None,
    is_mine: bool | None = None,
    tags: list[str] | None = None,
) -> tuple[Channel, bool]:
    """Resolve and store a channel (about 2 units). Returns (channel, created).
    Does not list uploads and never downloads anything."""
    client = get_client()
    channel_id = client.resolve_channel_id(text)
    ch = find_channel(channel_id)
    created = ch is None
    items = client.channels([channel_id])
    if not items:
        raise YouTubeError(f"channel {channel_id} not found")
    if created:
        ch = Channel(platform_id=youtube_platform().id, external_id=channel_id, tags=tags or [])
        db.session.add(ch)
    if follow is not None:
        ch.followed = follow
    if is_mine is not None:
        ch.is_mine = is_mine
    apply_channel_item(ch, items[0])
    db.session.flush()
    snapshot_channel_item(ch, items[0], utcnow())
    db.session.commit()
    return ch, created


def search_channels(query: str, limit: int = 10) -> list[dict]:
    """Channel search by name. search.list costs 100 units, so only on explicit search.
    A URL, @handle or UC... id is resolved directly instead (1-2 units)."""
    client = get_client()
    try:
        kind, _ = parse_channel_input(query)
    except ValueError:
        kind = "name"
    if kind in ("id", "url", "username") or query.strip().startswith(("@", "http")):
        ids = [client.resolve_channel_id(query)]
    else:
        ids = client.search_channel_ids(query, limit)
    cards = [channel_card(it) for it in client.channels(ids)] if ids else []
    order = {cid: i for i, cid in enumerate(ids)}
    cards.sort(key=lambda c: order.get(c["external_id"], 99))
    known = {c.external_id: c.id for c in db.session.scalars(db.select(Channel).where(Channel.external_id.in_(ids)))}
    for c in cards:
        c["channel_id"] = known.get(c["external_id"])
    return cards


# ------------------------------------------------------------------ videos


def upsert_videos(ch: Channel, items: Iterable[dict], now: dt.datetime, shorts_check=None) -> list[Video]:
    """Store or refresh video metadata plus a stats snapshot. New videos are 'discovered'."""
    platform_id = ch.platform_id
    items = list(items)
    existing = {
        v.external_id: v
        for v in db.session.scalars(
            db.select(Video).where(Video.platform_id == platform_id, Video.external_id.in_([it["id"] for it in items]))
        )
    }
    out = []
    for it in items:
        sn, cd, st = it.get("snippet", {}), it.get("contentDetails", {}), it.get("statistics", {})
        v = existing.get(it["id"])
        is_new = v is None
        if is_new:
            v = Video(
                channel_id=ch.id,
                platform_id=platform_id,
                external_id=it["id"],
                discovered_at=now,
                selection="discovered",
            )
            db.session.add(v)
        duration = parse_duration(cd.get("duration"))
        aspect = aspect_from_player(it.get("player"))
        if is_new or not v.format:
            # Classify once; the Shorts check is a free but slow HEAD request.
            fmt = classify_format(it, duration, aspect, shorts_check)
        else:
            fmt = v.format
            if "liveStreamingDetails" in it and fmt not in ("live", "premiere"):
                fmt = "live"
        v.title = sn.get("title") or ""
        v.description = sn.get("description")
        v.published_at = parse_time(sn.get("publishedAt"))
        v.duration_s = duration
        v.format = fmt
        v.aspect = aspect or v.aspect
        v.tags = sn.get("tags") or []
        v.category_id = sn.get("categoryId")
        v.language = sn.get("defaultAudioLanguage") or sn.get("defaultLanguage")
        v.privacy = it.get("status", {}).get("privacyStatus")
        v.thumbnail_url = best_thumbnail(sn.get("thumbnails"))
        v.metadata_at = now
        v.gone = False
        add_video_snapshot(v, st, now)
        out.append(v)
    db.session.flush()
    for v in out:
        if v.selection == "tracked":
            write_title(ch, v)
    return out


def add_video_snapshot(v: Video, stats: dict, now: dt.datetime, source: str = "youtube_data_api") -> None:
    views, likes, comments = (_int(stats.get(k)) for k in ("viewCount", "likeCount", "commentCount"))
    snap = VideoSnapshot(views=views, likes=likes, comments=comments, captured_at=now, source=source)
    v.snapshots.append(snap)
    v.views, v.likes, v.comments = views, likes, comments
    v.last_snapshot_at = now


def write_title(ch: Channel, v: Video) -> None:
    """title.txt: the TRIBE text input for title-only runs."""
    folder = media_dir(ch.external_id, v.external_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "title.txt"
    text = (v.title or "").strip() + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    upsert_asset(v, "title_text", path)


def upsert_asset(v: Video, kind: str, path, variant: str = "", **fields) -> Asset:
    asset = db.session.scalar(
        db.select(Asset).where(Asset.video_id == v.id, Asset.kind == kind, Asset.variant == variant)
    )
    if asset is None:
        asset = Asset(video_id=v.id, kind=kind, variant=variant, path=rel(path))
        db.session.add(asset)
    asset.path = rel(path)
    asset.bytes = path.stat().st_size
    for k, value in fields.items():
        setattr(asset, k, value)
    return asset


LISTING_FRESH_HOURS = 6


def listing_is_fresh(ch: Channel, now: dt.datetime | None = None) -> bool:
    now = now or utcnow()
    return ch.backfilled_at is not None and now - ch.backfilled_at < dt.timedelta(hours=LISTING_FRESH_HOURS)


def list_uploads(channel_id: int, max_pages: int | None = None) -> dict:
    """List every upload of a channel (metadata + a stats snapshot each). Never downloads.

    Costs about 2 units per 50 videos (one playlistItems page plus one videos.list batch).
    This is how discovered videos are refreshed: by reopening their channel.
    """
    ch = db.session.get(Channel, channel_id)
    client = get_client()
    now = utcnow()
    try:
        items = client.channels([ch.external_id])
        if not items:
            raise YouTubeError("channel no longer exists")
        apply_channel_item(ch, items[0])
        snapshot_channel_item(ch, items[0], now)
        seen, stored = set(), 0
        if ch.uploads_playlist:
            for ids in client.playlist_video_ids(ch.uploads_playlist, max_pages=max_pages):
                seen.update(ids)
                videos = upsert_videos(ch, client.videos(ids), now, shorts_check=client.is_short)
                stored += len(videos)
                db.session.commit()
        # Videos we knew but the full listing no longer returns were deleted or made private.
        if max_pages is None and seen:
            for v in db.session.scalars(db.select(Video).where(Video.channel_id == ch.id)):
                if v.external_id not in seen:
                    v.gone = True
        ch.backfilled_at = now
        ch.last_synced_at = now
        ch.sync_error = None
        db.session.commit()
    except Exception as err:
        db.session.rollback()
        ch = db.session.get(Channel, channel_id)
        ch.sync_error = str(err)[:1000]
        ch.last_synced_at = now
        db.session.commit()
        raise
    from .analyzers.builtin import compute_channel

    compute_channel(ch.id)
    return {"videos": stored}


def sync_new_uploads(channel_id: int) -> list[int]:
    """For a followed channel: check the newest page of uploads (1 unit), store unseen
    videos as discovered, and pull them if the channel auto-tracks new uploads.
    Returns the new video ids."""
    ch = db.session.get(Channel, channel_id)
    if not ch.uploads_playlist:
        return []
    client = get_client()
    now = utcnow()
    known = set(db.session.scalars(db.select(Video.external_id).where(Video.channel_id == ch.id)))
    new_ids = []
    for ids in client.playlist_video_ids(ch.uploads_playlist, max_pages=1):
        new_ids = [i for i in ids if i not in known]
    videos = upsert_videos(ch, client.videos(new_ids), now, shorts_check=client.is_short) if new_ids else []
    ch.last_synced_at = now
    ch.sync_error = None
    db.session.commit()
    if videos and ch.auto_track_new:
        pull_videos([v.id for v in videos], created_by="auto-track")
    return [v.id for v in videos]


# ------------------------------------------------------------------ pulling (discovered -> tracked)


def pull_videos(video_ids: list[int], created_by: str = "system") -> dict:
    """Mark videos tracked: a fresh stats snapshot (1 unit per 50), title.txt, and the
    media jobs (download, captions; thumbnails are fetched in-app). Also follows their
    channels so channel stats are collected alongside."""
    videos = list(db.session.scalars(db.select(Video).where(Video.id.in_(video_ids))))
    now = utcnow()
    newly = [v for v in videos if v.selection != "tracked"]
    for v in newly:
        v.selection = "tracked"
        v.tracked_at = now
        v.channel.followed = True
    db.session.flush()
    if newly:
        client = get_client()
        by_id = {v.external_id: v for v in newly}
        for it in client.video_stats(list(by_id)):
            add_video_snapshot(by_id[it["id"]], it.get("statistics", {}), now)
    for v in videos:
        write_title(v.channel, v)
    db.session.commit()
    from .jobs import queue_media_jobs

    jobs = queue_media_jobs([v.id for v in videos], created_by=created_by)
    return {
        "pulled": len(newly),
        "already_tracked": len(videos) - len(newly),
        "jobs": [j.id for j in jobs],
    }


# ------------------------------------------------------------------ snapshots


def due_videos(now: dt.datetime, limit: int) -> list[Video]:
    """Tracked videos whose next stats snapshot is due (daily while young, weekly after)."""
    young_days = get_setting("snapshot_young_days")
    young_every = dt.timedelta(hours=get_setting("snapshot_young_interval_hours"))
    old_every = dt.timedelta(hours=get_setting("snapshot_old_interval_hours"))
    young_cutoff = now - dt.timedelta(days=young_days)
    # Small slack so a daily schedule doesn't drift later each day.
    slack = dt.timedelta(minutes=30)
    q = (
        db.select(Video)
        .where(Video.selection == "tracked", Video.gone.is_(False))
        .where(
            db.or_(
                Video.last_snapshot_at.is_(None),
                db.and_(Video.published_at >= young_cutoff, Video.last_snapshot_at <= now - young_every + slack),
                db.and_(Video.published_at < young_cutoff, Video.last_snapshot_at <= now - old_every + slack),
            )
        )
        .order_by(Video.last_snapshot_at.is_not(None), Video.last_snapshot_at)
        .limit(limit)
    )
    return list(db.session.scalars(q))


def snapshot_videos(limit: int = 5000) -> int:
    """Take stats snapshots of every tracked video that is due. 1 unit per 50 videos."""
    now = utcnow()
    videos = due_videos(now, limit)
    if not videos:
        return 0
    client = get_client()
    by_id = {v.external_id: v for v in videos}
    touched = set()
    for i in range(0, len(videos), 50):
        chunk = [v.external_id for v in videos[i : i + 50]]
        for it in client.video_stats(chunk):
            v = by_id.get(it["id"])
            if v is None:
                continue
            add_video_snapshot(v, it.get("statistics", {}), now)
            touched.add(v.channel_id)
        db.session.commit()
    # Videos the API did not return are gone (deleted/private); stop polling them.
    for v in videos:
        if v.last_snapshot_at != now:
            v.gone = True
    db.session.commit()
    from .analyzers.builtin import compute_channel

    for cid in touched:
        compute_channel(cid)
    return len(videos)


def snapshot_channels() -> int:
    """Daily stats for followed channels (1 unit per 50 channels)."""
    now = utcnow()
    every = dt.timedelta(hours=get_setting("channel_snapshot_interval_hours"))
    slack = dt.timedelta(minutes=30)
    chans = list(
        db.session.scalars(
            db.select(Channel).where(
                Channel.followed,
                db.or_(Channel.last_snapshot_at.is_(None), Channel.last_snapshot_at <= now - every + slack),
            )
        )
    )
    if not chans:
        return 0
    client = get_client()
    by_id = {c.external_id: c for c in chans}
    for it in client.channels(list(by_id)):
        ch = by_id[it["id"]]
        apply_channel_item(ch, it)
        snapshot_channel_item(ch, it, now)
    db.session.commit()
    return len(chans)


def channels_due_for_upload_check(now: dt.datetime) -> list[Channel]:
    every = dt.timedelta(hours=get_setting("new_upload_check_hours"))
    return list(
        db.session.scalars(
            db.select(Channel).where(
                Channel.followed,
                Channel.backfilled_at.is_not(None),
                db.or_(Channel.last_synced_at.is_(None), Channel.last_synced_at <= now - every),
            )
        )
    )


# ------------------------------------------------------------------ thumbnails

THUMB_NAMES = ("maxresdefault", "sddefault", "hqdefault")


def fetch_thumbnail(v: Video, http: httpx.Client | None = None) -> bool:
    """Largest available thumbnail as thumb.jpg. Note: it is today's thumbnail; creators
    sometimes change or A/B test them after upload."""
    http = http or httpx.Client(timeout=30)
    folder = media_dir(v.channel.external_id, v.external_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "thumb.jpg"
    urls = [f"https://i.ytimg.com/vi/{v.external_id}/{n}.jpg" for n in THUMB_NAMES]
    if v.thumbnail_url and v.thumbnail_url not in urls:
        urls.insert(1, v.thumbnail_url)
    for url in urls:
        try:
            resp = http.get(url)
        except httpx.HTTPError:
            continue
        # YouTube serves a tiny grey placeholder for some missing sizes.
        if resp.status_code == 200 and len(resp.content) > 2000:
            path.write_bytes(resp.content)
            w, h = jpeg_size(resp.content)
            upsert_asset(v, "thumbnail", path, width=w, height=h, codec="jpeg", meta={"url": url})
            return True
    return False


def jpeg_size(data: bytes) -> tuple[int | None, int | None]:
    """Width and height from a JPEG's SOF marker, without an imaging library."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h = int.from_bytes(data[i + 5 : i + 7], "big")
            w = int.from_bytes(data[i + 7 : i + 9], "big")
            return w, h
        seg_len = int.from_bytes(data[i + 2 : i + 4], "big")
        i += 2 + seg_len
    return None, None


def fetch_missing_thumbnails(limit: int = 200) -> int:
    """Thumbnails for tracked videos (discovered ones use YouTube's copy)."""
    has_thumb = db.select(Asset.video_id).where(Asset.kind == "thumbnail")
    videos = list(
        db.session.scalars(
            db.select(Video)
            .where(Video.selection == "tracked", Video.gone.is_(False), Video.id.not_in(has_thumb))
            .order_by(Video.tracked_at.desc())
            .limit(limit)
        )
    )
    n = 0
    with httpx.Client(timeout=30) as http:
        for v in videos:
            if fetch_thumbnail(v, http):
                n += 1
            db.session.commit()
    return n

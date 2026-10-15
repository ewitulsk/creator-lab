"""YouTube owner sync: sign in with Google, pull owner-only data, show real engagement."""

from __future__ import annotations

import json
import urllib.parse

from flask import redirect, request
from sqlalchemy import func

from .. import background, ingest, owner_sync
from ..auth import actor, public
from ..extensions import db
from ..models import (
    Channel,
    OwnerCredential,
    OwnerSync,
    RetentionPoint,
    Video,
    VideoBreakdown,
    VideoDailyStat,
    VideoLifetimeStat,
    iso,
    utcnow,
)
from ..settings import get_setting
from ..storage import absolute
from ..youtube import oauth
from ..youtube.client import YouTubeClient
from . import ApiError, body, bp


def sync_json(s: OwnerSync | None) -> dict | None:
    if s is None:
        return None
    return {
        "id": s.id,
        "channel_id": s.channel_id,
        "mode": s.mode,
        "status": s.status,
        "counts": s.counts or {},
        "unavailable": s.unavailable or {},
        "error": s.error,
        "log": (s.log or [])[-40:],
        "started_by": s.started_by,
        "started_at": iso(s.started_at),
        "finished_at": iso(s.finished_at),
    }


def credential_json(c: OwnerCredential) -> dict:
    last = db.session.scalar(
        db.select(OwnerSync).where(OwnerSync.channel_id == c.channel_id).order_by(OwnerSync.id.desc())
    )
    last_done = db.session.scalar(
        db.select(OwnerSync)
        .where(OwnerSync.channel_id == c.channel_id, OwnerSync.status == "done")
        .order_by(OwnerSync.id.desc())
    )
    synced_videos = db.session.scalar(
        db.select(func.count(func.distinct(VideoLifetimeStat.video_id)))
        .join(Video, Video.id == VideoLifetimeStat.video_id)
        .where(Video.channel_id == c.channel_id)
    )
    return {
        "channel": {
            "id": c.channel.id,
            "title": c.channel.title,
            "handle": c.channel.handle,
            "thumbnail_url": c.channel.thumbnail_url,
        },
        "status": c.status,
        "status_detail": c.status_detail,
        "scopes": c.scopes or [],
        "connected_by": c.connected_by,
        "connected_at": iso(c.connected_at),
        "last_used_at": iso(c.last_used_at),
        "last_sync": sync_json(last),
        "last_finished_sync": sync_json(last_done),
        "synced_videos": synced_videos or 0,
    }


@bp.get("/youtube/owner")
def owner_status():
    from flask import current_app

    creds = db.session.scalars(db.select(OwnerCredential).order_by(OwnerCredential.id)).all()
    return {
        "configured": oauth.is_configured(),
        "redirect_uri": current_app.config["APP"].google_redirect_uri if oauth.is_configured() else None,
        "scopes": oauth.SCOPES,
        "credentials": [credential_json(c) for c in creds],
    }


@bp.post("/youtube/oauth/start")
def oauth_start():
    data = request.get_json(silent=True) or {}
    path = str(data.get("return_to") or "/settings")
    if not path.startswith("/") or path.startswith("//"):
        raise ApiError("return_to must be a path on this site")
    try:
        url = oauth.start(actor(), request.host_url.rstrip("/") + path)
    except oauth.OAuthError as err:
        raise ApiError(str(err), 400) from err
    return {"auth_url": url}


def _back(return_to: str, **params) -> object:
    sep = "&" if "?" in return_to else "?"
    return redirect(return_to + sep + urllib.parse.urlencode(params))


@bp.get("/youtube/oauth/callback")
@public
def oauth_callback():
    """Google redirects here. Authenticated by the single-use state, not the session cookie
    (the browser may come back on another host name)."""
    state = request.args.get("state", "")
    try:
        pending = oauth.take_state(state)
    except oauth.OAuthError as err:
        return f"<p>creator-lab: {err}</p><p><a href='/settings'>Back to Settings</a></p>", 400
    if request.args.get("error"):
        return _back(pending.return_to, youtube="error", message=request.args["error"])
    try:
        tokens = oauth.exchange_code(request.args.get("code", ""), pending.code_verifier)
        client = YouTubeClient(
            None,
            budget=lambda: get_setting("daily_quota_budget"),
            transport=oauth._transport,
            access_token=tokens["access_token"],
        )
        me = client._get("channels", {"part": "snippet,contentDetails,statistics,brandingSettings", "mine": "true"})
        items = me.get("items") or []
        if not items:
            raise oauth.OAuthError("this Google account has no YouTube channel")
        item = items[0]
        ch = ingest.find_channel(item["id"])
        if ch is None:
            ch = Channel(platform_id=ingest.youtube_platform().id, external_id=item["id"], tags=[])
            db.session.add(ch)
        ingest.apply_channel_item(ch, item)
        ch.owned = ch.is_mine = ch.followed = True
        db.session.flush()
        ingest.snapshot_channel_item(ch, item, utcnow())
        cred = db.session.scalar(db.select(OwnerCredential).where(OwnerCredential.channel_id == ch.id))
        if cred is None:
            cred = OwnerCredential(channel_id=ch.id, connected_by=pending.created_by)
            db.session.add(cred)
        cred.refresh_token_enc = oauth.encrypt(tokens["refresh_token"])
        cred.scopes = (tokens.get("scope") or "").split()
        cred.status, cred.status_detail, cred.connected_at = "active", None, utcnow()
        db.session.commit()
    except oauth.OAuthError as err:
        db.session.rollback()
        return _back(pending.return_to, youtube="error", message=str(err)[:300])
    return _back(pending.return_to, youtube="connected", channel_id=ch.id)


def _credential(channel_id: int) -> OwnerCredential:
    cred = db.session.scalar(db.select(OwnerCredential).where(OwnerCredential.channel_id == channel_id))
    if cred is None:
        raise ApiError("this channel is not connected", 404)
    return cred


@bp.post("/youtube/owner/<int:channel_id>/sync")
def owner_sync_start(channel_id: int):
    cred = _credential(channel_id)
    if cred.status != "active":
        raise ApiError(cred.status_detail or "sign in with YouTube again", 409)
    mode = str(body().get("mode", "new"))
    try:
        sync = owner_sync.start_sync(channel_id, mode, actor())
    except ValueError as err:
        raise ApiError(str(err), 409) from err
    background.submit(f"owner-sync:{channel_id}", owner_sync.run_sync, sync.id)
    db.session.refresh(sync)
    return {"sync": sync_json(sync)}


@bp.get("/youtube/owner/syncs/<int:sync_id>")
def owner_sync_get(sync_id: int):
    s = db.session.get(OwnerSync, sync_id)
    if s is None:
        raise ApiError("sync not found", 404)
    return {"sync": sync_json(s)}


@bp.delete("/youtube/owner/<int:channel_id>")
def owner_disconnect(channel_id: int):
    cred = _credential(channel_id)
    oauth.revoke(cred)
    cred.channel.owned = False
    db.session.delete(cred)
    db.session.commit()
    return {"ok": True}


# ------------------------------------------------------------------ real engagement for one video


def tribe_engagement_curve(video: Video) -> dict | None:
    """The TRIBE draft engagement curve: z(attention) minus z(default mode), from the
    runner's network curves. Replaced by the tribe_engagement analyzer when it exists."""
    from ..analyzers import current
    from ..models import AnalysisResult

    try:
        analyzer = current("tribe_video")
    except KeyError:
        return None
    row = db.session.scalar(
        db.select(AnalysisResult).where(
            AnalysisResult.video_id == video.id,
            AnalysisResult.analyzer_id == analyzer.id,
            AnalysisResult.status == "done",
        )
    )
    if row is None or not row.output_path:
        return None
    path = absolute(row.output_path) / "networks.json"
    if not path.exists():
        return None
    nets = json.loads(path.read_text(encoding="utf-8"))
    t = nets.get("t") or []
    series = nets.get("networks") or {}
    if not t or "attention" not in series or "default_mode" not in series:
        return None

    def z(values):
        vals = [v for v in values if v is not None]
        if len(vals) < 2:
            return [None] * len(values)
        mean = sum(vals) / len(vals)
        sd = (sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5 or 1.0
        return [None if v is None else (v - mean) / sd for v in values]

    att, dmn = z(series["attention"]), z(series["default_mode"])
    engagement = [None if a is None or d is None else round(a - d, 4) for a, d in zip(att, dmn, strict=False)]
    return {"t": t, "engagement": engagement, "networks": series, "definition": "z(attention) - z(default_mode)"}


@bp.get("/videos/<int:video_id>/engagement")
def video_engagement(video_id: int):
    v = db.session.get(Video, video_id)
    if v is None:
        raise ApiError("video not found", 404)
    syncs = list(
        db.session.scalars(
            db.select(RetentionPoint.sync_id)
            .where(RetentionPoint.video_id == v.id)
            .distinct()
            .order_by(RetentionPoint.sync_id)
        )
    )
    version = int(request.args.get("sync_id", syncs[-1] if syncs else 0))
    points = db.session.scalars(
        db.select(RetentionPoint)
        .where(RetentionPoint.video_id == v.id, RetentionPoint.sync_id == version)
        .order_by(RetentionPoint.audience_type, RetentionPoint.elapsed_ratio)
    ).all()
    curves: dict[str, list] = {}
    for p in points:
        duration = p.video_duration_s or v.duration_s
        curves.setdefault(p.audience_type, []).append(
            {
                "ratio": p.elapsed_ratio,
                "seconds": round(p.elapsed_ratio * duration, 2) if duration else None,
                "watch_ratio": p.audience_watch_ratio,
                "relative": p.relative_retention_performance,
            }
        )
    lifetime = db.session.scalar(
        db.select(VideoLifetimeStat)
        .where(VideoLifetimeStat.video_id == v.id)
        .order_by(VideoLifetimeStat.sync_id.desc())
    )
    daily = db.session.scalars(
        db.select(VideoDailyStat).where(VideoDailyStat.video_id == v.id).order_by(VideoDailyStat.day)
    ).all()
    breakdowns: dict[str, list] = {}
    if lifetime is not None:
        for b in db.session.scalars(
            db.select(VideoBreakdown).where(VideoBreakdown.video_id == v.id, VideoBreakdown.sync_id == lifetime.sync_id)
        ):
            breakdowns.setdefault(b.dimension, []).append({"key": b.key, **b.metrics})
    return {
        "owned": bool(v.channel.owned),
        "duration_s": v.duration_s,
        "retention": {"sync_id": version or None, "versions": syncs, "curves": curves},
        "lifetime": lifetime.metrics if lifetime else None,
        "daily": [
            {
                "day": d.day.isoformat(),
                "views": d.views,
                "estimated_minutes_watched": d.estimated_minutes_watched,
                "average_view_duration": d.average_view_duration,
                "average_view_percentage": d.average_view_percentage,
                "likes": d.likes,
                "comments": d.comments,
                "shares": d.shares,
                "subscribers_gained": d.subscribers_gained,
                "subscribers_lost": d.subscribers_lost,
            }
            for d in daily
        ],
        "breakdowns": breakdowns,
        "tribe": tribe_engagement_curve(v),
    }

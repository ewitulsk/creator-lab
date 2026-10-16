"""Videos: list/search, detail with history and analyzer results, thumbnails and files."""

from __future__ import annotations

from flask import redirect, request, send_file

from .. import ingest
from ..analyzers.builtin import compute_channel
from ..extensions import db
from ..models import AnalysisResult, Analyzer, Asset, Channel, Video, iso, utcnow
from ..serializers import asset as asset_json
from ..serializers import video_snapshot, video_summary
from ..storage import absolute
from . import ApiError, bp

SORTS = {
    "published_at": Video.published_at,
    "views": Video.views,
    "outlier_score": Video.outlier_score,
    "duration_s": Video.duration_s,
    "title": Video.title,
    "likes": Video.likes,
    "comments": Video.comments,
}
MAX_LIMIT = 5000


def video_list_response(channel_id: int | None = None) -> dict:
    args = request.args
    q = db.select(Video)
    if channel_id is not None:
        q = q.where(Video.channel_id == channel_id)
    elif args.get("channel_id"):
        q = q.where(Video.channel_id.in_([int(x) for x in args["channel_id"].split(",") if x.isdigit()]))
    if args.get("is_mine") == "1":
        q = q.join(Channel).where(Channel.is_mine)
    if args.get("format"):
        q = q.where(Video.format.in_(args["format"].split(",")))
    if args.get("selection") in ("discovered", "tracked"):
        q = q.where(Video.selection == args["selection"])
    if args.get("include_gone") != "1":
        q = q.where(Video.gone.is_(False))
    if args.get("q"):
        q = q.where(Video.title.ilike(f"%{args['q']}%"))
    if args.get("min_outlier"):
        q = q.where(Video.outlier_score >= float(args["min_outlier"]))
    if args.get("max_outlier"):
        q = q.where(Video.outlier_score <= float(args["max_outlier"]))
    total = db.session.scalar(db.select(db.func.count()).select_from(q.subquery()))
    col = SORTS.get(args.get("sort", "published_at"))
    if col is None:
        raise ApiError(f"sort must be one of {', '.join(SORTS)}")
    order = col.asc() if args.get("order") == "asc" else col.desc()
    q = q.order_by(col.is_(None), order, Video.id)
    limit = MAX_LIMIT if args.get("limit") == "all" else min(int(args.get("limit", 100)), MAX_LIMIT)
    q = q.limit(limit).offset(int(args.get("offset", 0)))
    videos = list(db.session.scalars(q))
    thumbs = set(
        db.session.scalars(
            db.select(Asset.video_id).where(Asset.kind == "thumbnail", Asset.video_id.in_([v.id for v in videos]))
        )
    )
    return {"videos": [video_summary(v, v.id in thumbs) for v in videos], "total": total}


def get_video(video_id: int) -> Video:
    v = db.session.get(Video, video_id)
    if v is None:
        raise ApiError("video not found", 404)
    return v


def analyzer_statuses(v: Video) -> list[dict]:
    """Every current analyzer that applies to this video's format, with its status here."""
    analyzers = db.session.scalars(db.select(Analyzer).where(Analyzer.is_current).order_by(Analyzer.id)).all()
    results = {
        r.analyzer_id: r for r in db.session.scalars(db.select(AnalysisResult).where(AnalysisResult.video_id == v.id))
    }
    out = []
    for a in analyzers:
        if a.applies_to and v.format not in a.applies_to:
            continue
        r = results.get(a.id)
        out.append(
            {
                "analyzer": a.name,
                "version": a.version,
                "title": a.title,
                "runner_type": a.runner_type,
                "status": r.status if r else "missing",
                "summary": r.summary if r else {},
                "error": r.error if r else None,
                "job_id": r.job_id if r else None,
                "output_path": r.output_path if r else None,
                "computed_at": iso(r.computed_at) if r else None,
            }
        )
    return out


@bp.get("/videos")
def list_videos():
    return video_list_response()


@bp.get("/videos/<int:video_id>")
def video_detail(video_id: int):
    v = get_video(video_id)
    ch = v.channel
    has_thumb = any(a.kind == "thumbnail" for a in v.assets)
    return {
        "video": video_summary(v, has_thumb)
        | {
            "description": v.description,
            "tags": v.tags or [],
            "category_id": v.category_id,
            "language": v.language,
            "privacy": v.privacy,
            "discovered_at": iso(v.discovered_at),
            "last_snapshot_at": iso(v.last_snapshot_at),
            "channel": {"id": ch.id, "title": ch.title, "handle": ch.handle, "is_mine": ch.is_mine, "owned": ch.owned},
        },
        "snapshots": [video_snapshot(s) for s in v.snapshots],
        "assets": [asset_json(a) for a in v.assets],
        "analyzers": analyzer_statuses(v),
    }


@bp.post("/videos/<int:video_id>/refresh")
def refresh_video(video_id: int):
    from .channels import youtube_call

    v = get_video(video_id)
    client = youtube_call(ingest.get_client)
    items = youtube_call(client.videos, [v.external_id])
    if not items:
        v.gone = True
        db.session.commit()
        raise ApiError("YouTube no longer returns this video (deleted or private)", 404)
    ingest.upsert_videos(v.channel, items, utcnow())
    db.session.commit()
    compute_channel(v.channel_id)
    return video_detail(video_id)


@bp.get("/videos/<int:video_id>/thumbnail")
def video_thumbnail(video_id: int):
    v = get_video(video_id)
    a = next((a for a in v.assets if a.kind == "thumbnail"), None)
    if a is not None:
        path = absolute(a.path)
        if path.is_file():
            resp = send_file(path, mimetype="image/jpeg", conditional=True, max_age=86400)
            return resp
    if v.thumbnail_url:
        return redirect(v.thumbnail_url)
    raise ApiError("no thumbnail", 404)


MIME = {
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".vtt": "text/vtt",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json",
    ".wav": "audio/wav",
}


@bp.get("/assets/<int:asset_id>/file")
def asset_file(asset_id: int):
    a = db.session.get(Asset, asset_id)
    if a is None:
        raise ApiError("asset not found", 404)
    path = absolute(a.path)
    if not path.is_file():
        raise ApiError("file is missing on disk", 404)
    # conditional=True gives Range support, which the video player needs for seeking.
    return send_file(path, mimetype=MIME.get(path.suffix.lower()), conditional=True, max_age=3600)


@bp.get("/videos/<int:video_id>/heatmap")
def video_heatmap(video_id: int):
    """YouTube's 'Most replayed' heatmap and chapters, per capture (latest with a heatmap by default)."""
    from ..models import VideoChapter, VideoHeatmapPoint, VideoInfoCapture

    v = get_video(video_id)
    captures = list(
        db.session.scalars(
            db.select(VideoInfoCapture).where(VideoInfoCapture.video_id == v.id).order_by(VideoInfoCapture.captured_at)
        )
    )
    chosen = None
    if request.args.get("capture_id"):
        chosen = next((c for c in captures if c.id == int(request.args["capture_id"])), None)
        if chosen is None:
            raise ApiError("capture not found", 404)
    else:
        with_points = [c for c in captures if c.heatmap_points]
        chosen = (with_points or captures or [None])[-1]
    points, chapters = [], []
    if chosen is not None:
        points = [
            {"start_s": p.start_s, "end_s": p.end_s, "value": p.value}
            for p in db.session.scalars(
                db.select(VideoHeatmapPoint)
                .where(VideoHeatmapPoint.capture_id == chosen.id)
                .order_by(VideoHeatmapPoint.start_s)
            )
        ]
        chapters = [
            {"start_s": c.start_s, "end_s": c.end_s, "title": c.title}
            for c in db.session.scalars(
                db.select(VideoChapter).where(VideoChapter.capture_id == chosen.id).order_by(VideoChapter.start_s)
            )
        ]
    return {
        "captures": [
            {
                "id": c.id,
                "captured_at": iso(c.captured_at),
                "source": c.source,
                "heatmap_points": c.heatmap_points,
                "chapter_count": c.chapter_count,
                "snapshot": c.snapshot or {},
            }
            for c in captures
        ],
        "capture_id": chosen.id if chosen else None,
        "duration_s": v.duration_s,
        "points": points,
        "chapters": chapters,
    }

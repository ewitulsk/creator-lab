"""Compare creators, Shorts, exports and database backups."""

from __future__ import annotations

import csv
import io
import json

from flask import Response, request, send_file

from .. import backups
from ..analyzers import by_ref
from ..compare import channel_profile
from ..extensions import db
from ..models import AnalysisResult, Channel, ChannelSnapshot, Video, VideoSnapshot, iso
from . import ApiError, bp


@bp.get("/compare")
def compare():
    ids = [int(x) for x in request.args.get("channel_ids", "").split(",") if x.strip().isdigit()]
    if not 1 <= len(ids) <= 8:
        raise ApiError("pass 1 to 8 channel_ids")
    months = min(max(int(request.args.get("months", 12)), 1), 120)
    chans = [db.session.get(Channel, i) for i in ids]
    if any(c is None for c in chans):
        raise ApiError("unknown channel", 404)
    return {"months": months, "channels": [channel_profile(c, months) for c in chans]}


@bp.get("/shorts")
def shorts():
    """Shorts across channels, scored against the channel's Shorts baseline."""
    q = db.select(Video).where(Video.format == "short", Video.gone.is_(False))
    if request.args.get("channel_id"):
        q = q.where(Video.channel_id.in_([int(x) for x in request.args["channel_id"].split(",") if x.isdigit()]))
    sort = {"outlier_score": Video.outlier_score, "views": Video.views, "published_at": Video.published_at}.get(
        request.args.get("sort", "published_at")
    )
    if sort is None:
        raise ApiError("sort must be outlier_score, views or published_at")
    total = db.session.scalar(db.select(db.func.count()).select_from(q.subquery()))
    limit = min(int(request.args.get("limit", 120)), 1000)
    rows = db.session.scalars(
        q.order_by(sort.is_(None), sort.desc()).limit(limit).offset(int(request.args.get("offset", 0)))
    )
    from ..serializers import video_summary

    return {
        "videos": [video_summary(v) | {"channel_title": v.channel.title} for v in rows],
        "total": total,
    }


# ------------------------------------------------------------------ exports

EXPORTS = ("videos", "video_snapshots", "channel_snapshots", "analyzer")


def _rows(kind: str) -> list[dict]:
    args = request.args
    channel_ids = [int(x) for x in args.get("channel_id", "").split(",") if x.isdigit()]
    if kind == "videos":
        q = db.select(Video)
        if channel_ids:
            q = q.where(Video.channel_id.in_(channel_ids))
        if args.get("selection") in ("tracked", "discovered"):
            q = q.where(Video.selection == args["selection"])
        return [
            {
                "video_id": v.id,
                "youtube_id": v.external_id,
                "channel": v.channel.title,
                "channel_id": v.channel_id,
                "title": v.title,
                "published_at": iso(v.published_at),
                "duration_s": v.duration_s,
                "format": v.format,
                "views": v.views,
                "likes": v.likes,
                "comments": v.comments,
                "outlier_score": v.outlier_score,
                "outlier_baseline": v.outlier_baseline,
                "selection": v.selection,
                "privacy": v.privacy,
                "gone": v.gone,
            }
            for v in db.session.scalars(q.order_by(Video.channel_id, Video.published_at))
        ]
    if kind == "video_snapshots":
        q = db.select(VideoSnapshot, Video).join(Video)
        if channel_ids:
            q = q.where(Video.channel_id.in_(channel_ids))
        return [
            {
                "video_id": v.id,
                "youtube_id": v.external_id,
                "captured_at": iso(s.captured_at),
                "views": s.views,
                "likes": s.likes,
                "comments": s.comments,
            }
            for s, v in db.session.execute(q.order_by(Video.id, VideoSnapshot.captured_at))
        ]
    if kind == "channel_snapshots":
        q = db.select(ChannelSnapshot, Channel).join(Channel)
        if channel_ids:
            q = q.where(Channel.id.in_(channel_ids))
        return [
            {
                "channel_id": c.id,
                "channel": c.title,
                "captured_at": iso(s.captured_at),
                "subscribers": s.subscribers,
                "views": s.views,
                "video_count": s.video_count,
            }
            for s, c in db.session.execute(q.order_by(Channel.id, ChannelSnapshot.captured_at))
        ]
    if kind == "analyzer":
        a = by_ref(args.get("analyzer", ""))
        if a is None:
            raise ApiError("pass analyzer=name or name@version")
        q = db.select(AnalysisResult, Video).join(Video).where(AnalysisResult.analyzer_id == a.id)
        if channel_ids:
            q = q.where(Video.channel_id.in_(channel_ids))
        out = []
        for r, v in db.session.execute(q.order_by(Video.id)):
            row = {
                "video_id": v.id,
                "youtube_id": v.external_id,
                "analyzer": a.ref,
                "status": r.status,
                "computed_at": iso(r.computed_at),
            }
            for k, val in (r.summary or {}).items():
                row[k] = json.dumps(val) if isinstance(val, (dict, list)) else val
            out.append(row)
        return out
    raise ApiError(f"export must be one of {', '.join(EXPORTS)}", 404)


@bp.get("/exports/<kind>.<fmt>")
def export(kind: str, fmt: str):
    rows = _rows(kind)
    name = kind if kind != "analyzer" else f"analyzer-{request.args.get('analyzer', '').replace('@', '-v')}"
    if fmt == "csv":
        buf = io.StringIO()
        fields: list[str] = []
        for r in rows:
            fields += [k for k in r if k not in fields]
        w = csv.DictWriter(buf, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
        return Response(
            buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'}
        )
    if fmt == "parquet":
        import pandas as pd

        buf = io.BytesIO()
        pd.DataFrame(rows).to_parquet(buf, index=False)
        return Response(
            buf.getvalue(),
            mimetype="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{name}.parquet"'},
        )
    raise ApiError("format must be csv or parquet", 404)


# ------------------------------------------------------------------ backups


@bp.get("/backups")
def list_backups():
    return {"backups": backups.list_backups()}


@bp.post("/backups")
def create_backup():
    return {"backup": backups.backup_now(), "backups": backups.list_backups()}


@bp.get("/backups/<name>")
def download_backup(name: str):
    path = (backups.backups_dir() / name).resolve()
    if (
        not name.startswith(backups.PREFIX)
        or not path.is_relative_to(backups.backups_dir().resolve())
        or not path.is_file()
    ):
        raise ApiError("backup not found", 404)
    return send_file(path, as_attachment=True, download_name=name)

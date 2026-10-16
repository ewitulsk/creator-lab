"""Brain player data: the fsaverage5 mesh and atlas, and quantized TRIBE predictions."""

from __future__ import annotations

import json

from flask import Response, request, send_file

from .. import brain
from ..analyzers import by_ref
from ..extensions import db
from ..models import AnalysisResult, Video
from ..storage import absolute, data_root
from . import ApiError, bp

MESH_FILES = {"mesh.json": "application/json", "mesh.bin": "application/octet-stream", "atlas.json": "application/json"}
MAX_FRAMES = 600


@bp.get("/brain/assets/<name>")
def brain_asset(name: str):
    """fsaverage5 inflated mesh (mesh.json + mesh.bin) and the HCP atlas, exported by the TRIBE runner."""
    if name not in MESH_FILES:
        raise ApiError("unknown brain asset", 404)
    path = data_root() / "brain" / name
    if not path.is_file():
        raise ApiError(
            "the brain mesh hasn't been exported yet: start the TRIBE runner once "
            "(it writes DATA_ROOT/brain), or run `python -m runners.tribe --export-brain-assets`",
            404,
        )
    return send_file(path, mimetype=MESH_FILES[name], max_age=86400)


def tribe_result(video_id: int) -> tuple[Video, AnalysisResult]:
    v = db.session.get(Video, video_id)
    if v is None:
        raise ApiError("video not found", 404)
    analyzer = by_ref(request.args.get("analyzer", "tribe_video"))
    if analyzer is None or not analyzer.name.startswith("tribe_"):
        raise ApiError("analyzer must be a TRIBE analyzer (tribe_video or tribe_title)")
    row = db.session.scalar(
        db.select(AnalysisResult).where(
            AnalysisResult.video_id == v.id,
            AnalysisResult.analyzer_id == analyzer.id,
            AnalysisResult.status == "done",
        )
    )
    if row is None or not row.output_path:
        raise ApiError(f"no {analyzer.name} result for this video yet", 404)
    folder = absolute(row.output_path)
    if not (folder / "preds.npy").is_file():
        raise ApiError("the result folder has no preds.npy", 404)
    return v, row


@bp.get("/videos/<int:video_id>/brain")
def video_brain(video_id: int):
    v, row = tribe_result(video_id)
    folder = absolute(row.output_path)
    q, scale = brain.load_quantized(folder)
    timing = (
        json.loads((folder / "timing.json").read_text(encoding="utf-8")) if (folder / "timing.json").exists() else {}
    )
    networks = {}
    if (folder / "networks.json").exists():
        networks = json.loads((folder / "networks.json").read_text(encoding="utf-8"))
    peaks = {
        name: brain.network_peaks(networks.get("t", []), series)
        for name, series in (networks.get("networks") or {}).items()
    }
    return {
        "video_id": v.id,
        "analyzer": row.analyzer.name,
        "result_id": row.id,
        "n_timesteps": int(q.shape[0]),
        "n_vertices": int(q.shape[1]),
        "tr_s": float(timing.get("tr_s", 1.0)),
        "start_s": timing.get("start_s") or [float(i) for i in range(q.shape[0])],
        "hrf_offset_s": timing.get("hrf_offset_s"),
        "scale": scale,
        "networks": networks or None,
        "peaks": peaks,
        "frames_url": f"/api/videos/{v.id}/brain/frames?analyzer={row.analyzer.name}",
        "max_frames_per_request": MAX_FRAMES,
    }


@bp.get("/videos/<int:video_id>/brain/frames")
def video_brain_frames(video_id: int):
    """uint8 frames (count x n_vertices, row-major). 0 = no prediction, 1..255 = scale lo..hi."""
    _v, row = tribe_result(video_id)
    q, _scale = brain.load_quantized(absolute(row.output_path))
    start = max(0, int(request.args.get("start", 0)))
    count = max(1, min(int(request.args.get("count", 120)), MAX_FRAMES))
    data = brain.frames_bytes(q, start, count)
    n = len(data) // q.shape[1]
    return Response(
        data,
        mimetype="application/octet-stream",
        headers={
            "X-Frame-Start": str(start),
            "X-Frame-Count": str(n),
            "X-Vertices": str(q.shape[1]),
            "Cache-Control": "private, max-age=3600",
        },
    )

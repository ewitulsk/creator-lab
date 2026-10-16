"""Viral levers: Gemini settings, estimates, jobs, per-video scores, pairwise and the URL A/B."""

from __future__ import annotations

from flask import request

from .. import jobs as jobsvc
from ..auth import actor
from ..extensions import db
from ..levers import config as lcfg
from ..levers import gemini, rubric, service
from ..models import Job, LeverPairwise, StudyVideo, Video
from . import ApiError, body, bp
from .jobs import job_summary


def _ids(data: dict, key: str = "video_ids", limit: int = 2000) -> list[int]:
    ids = data.get(key)
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
        raise ApiError(f"{key} must be a non-empty list of ids")
    if len(ids) > limit:
        raise ApiError(f"at most {limit} at a time")
    return ids


def _config_out() -> dict:
    r = rubric.load()
    return {
        "key_set": lcfg.key_is_set(),  # never the key itself
        "config": lcfg.get_config(),
        "models": [{"id": k, "title": v["title"], "price_per_mtok": lcfg.price(k)} for k, v in lcfg.MODELS.items()],
        "input_modes": [
            {"id": "upload", "title": "Upload a blinded low-res copy (default)", "blinded": True},
            {"id": "youtube_url", "title": "YouTube URL (not blinded; for the A/B only)", "blinded": False},
        ],
        "rubric": {"version": r.version, "hash": r.hash, "path": "rubrics/levers_v1.yaml"},
    }


@bp.get("/levers/config")
def levers_config():
    return _config_out()


@bp.put("/levers/config")
def levers_config_update():
    try:
        lcfg.update_config(body())
    except ValueError as err:
        raise ApiError(str(err)) from err
    return _config_out()


@bp.post("/levers/test")
def levers_test():
    """Check the key and that the chosen model id exists. One cheap metadata call, no video."""
    if not lcfg.key_is_set():
        raise ApiError("GEMINI_API_KEY is not set in backend/.env")
    model = (request.get_json(silent=True) or {}).get("model") or lcfg.get_config()["model"]
    if model not in lcfg.MODELS:
        raise ApiError("unknown model")
    try:
        info = gemini.make_client(lcfg.api_key()).check_model(model)
    except gemini.GeminiError as err:
        return {"ok": False, "model": model, "error": service.scrub(str(err))}
    return {"ok": True, "model": model, "info": info}


@bp.post("/levers/estimate")
def levers_estimate():
    data = body()
    cfg = {**lcfg.get_config(), **(data.get("config") or {})}
    if data.get("pairs"):
        return {"estimate": service.estimate_pairwise(_pairs(data), cfg)}
    return {"estimate": service.estimate(_ids(data), cfg)}


@bp.post("/levers/score")
def levers_score():
    """Issue a scoring batch (skips videos already scored with this model + rubric)."""
    data = body()
    ids = _ids(data)
    est = service.estimate(ids)
    try:
        job = jobsvc.issue_job(service.ANALYZER, ids, created_by=actor(), params={"estimate": est})
    except jobsvc.JobError as err:
        raise ApiError(str(err)) from err
    return {"job": job_summary(job) if job else None, "estimate": est}


@bp.post("/levers/ab")
def levers_ab():
    """URL-vs-upload A/B: scores each video both ways (real API calls; up to 5 videos)."""
    data = body()
    ids = _ids(data, limit=5)
    try:
        job = jobsvc.issue_job(service.ANALYZER, ids, created_by=actor(), params={"ab": True}, force=True)
    except jobsvc.JobError as err:
        raise ApiError(str(err)) from err
    return {"job": job_summary(job) if job else None}


@bp.get("/levers/ab")
def levers_ab_report():
    ids = [int(x) for x in request.args.get("video_ids", "").split(",") if x.isdigit()]
    if not ids:
        raise ApiError("pass video_ids")
    return {"rows": service.ab_comparison(ids, request.args.get("model"))}


def _pairs(data: dict) -> list[tuple[int, int]]:
    pairs = data.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ApiError("pairs must be a non-empty list of [video_a, video_b]")
    out = []
    for p in pairs:
        if not (isinstance(p, list) and len(p) == 2 and all(isinstance(i, int) for i in p)) or p[0] == p[1]:
            raise ApiError("each pair must be two different video ids")
        out.append((p[0], p[1]))
    return out


def _issue_pairwise(pairs: list[tuple[int, int]], study_id: int | None, keys: list[str | None]) -> dict:
    ids = sorted({i for p in pairs for i in p})
    if db.session.scalar(db.select(db.func.count()).select_from(Video).where(Video.id.in_(ids))) != len(ids):
        raise ApiError("some videos do not exist", 404)
    est = service.estimate_pairwise(pairs)
    params = {
        "pairs": [{"a": a, "b": b, "pair_key": k} for (a, b), k in zip(pairs, keys, strict=True)],
        "study_id": study_id,
        "estimate": est,
    }
    try:
        job = jobsvc.issue_job(service.PAIRWISE, ids, created_by=actor(), params=params, force=True)
    except jobsvc.JobError as err:
        raise ApiError(str(err)) from err
    if job is None:
        raise ApiError("those videos are already in an active pairwise job")
    got = {i.video_id for i in job.items}
    dropped = [p for p in params["pairs"] if p["a"] not in got or p["b"] not in got]
    if dropped:
        job.params = {**job.params, "pairs": [p for p in params["pairs"] if p not in dropped]}
        db.session.commit()
    return {"job": job_summary(job), "estimate": est, "dropped_pairs": len(dropped)}


@bp.post("/levers/pairwise")
def levers_pairwise():
    """Judge explicit pairs (e.g. from the Compare page)."""
    pairs = _pairs(body())
    return _issue_pairwise(pairs, None, [None] * len(pairs))


@bp.post("/studies/<int:study_id>/levers-pairwise")
def study_levers_pairwise(study_id: int):
    """Judge every matched pair in a study (members sharing a group with exactly two videos)."""
    groups: dict[str, list[int]] = {}
    for m in db.session.scalars(db.select(StudyVideo).where(StudyVideo.study_id == study_id)):
        if m.group:
            groups.setdefault(m.group, []).append(m.video_id)
    pairs, keys = [], []
    for g, vids in sorted(groups.items()):
        if len(vids) == 2:
            pairs.append((vids[0], vids[1]))
            keys.append(g)
    if not pairs:
        raise ApiError("this study has no matched pairs (groups of exactly two videos)")
    if (request.get_json(silent=True) or {}).get("estimate_only"):
        return {"estimate": service.estimate_pairwise(pairs), "pairs": len(pairs)}
    return _issue_pairwise(pairs, study_id, keys)


@bp.get("/levers/pairwise")
def levers_pairwise_list():
    q = db.select(LeverPairwise)
    if request.args.get("study_id"):
        q = q.where(LeverPairwise.study_id == int(request.args["study_id"]))
    if request.args.get("video_id"):
        vid = int(request.args["video_id"])
        q = q.where((LeverPairwise.video_a == vid) | (LeverPairwise.video_b == vid))
    rows = db.session.scalars(q.order_by(LeverPairwise.created_at.desc()).limit(2000))
    return {
        "rows": [
            {
                "video_a": r.video_a,
                "video_b": r.video_b,
                "order": r.order,
                "lever": r.lever,
                "winner_video_id": r.winner_video_id,
                "confidence": r.confidence,
                "reason": r.reason,
                "pair_key": r.pair_key,
                "study_id": r.study_id,
                "model": r.model,
                "input_mode": r.input_mode,
                "created_at": r.created_at.isoformat() + "Z",
            }
            for r in rows
        ]
    }


@bp.get("/videos/<int:video_id>/levers")
def video_levers(video_id: int):
    if db.session.get(Video, video_id) is None:
        raise ApiError("video not found", 404)
    return service.video_levers(video_id)


@bp.get("/levers/jobs/<int:job_id>/cost")
def levers_job_cost(job_id: int):
    job = db.session.get(Job, job_id)
    if job is None:
        raise ApiError("job not found", 404)
    return {"job_id": job_id, "estimate": (job.params or {}).get("estimate"), "actual": service.job_cost(job_id)}

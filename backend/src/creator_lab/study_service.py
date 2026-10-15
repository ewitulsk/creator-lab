"""Studies: modules in studies/ plus a DB row each.

Lock flow: draft -> locked (criteria params, measures and the module's source hash are
frozen) -> collecting (gaps being filled) -> analyzed. If a locked study's module changes,
it is flagged and a new version must be created. Results record the code version.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import importlib
import io
import json
import logging
import os
import pkgutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType

from .analyzers import by_ref
from .config import REPO_ROOT
from .extensions import db
from .models import (
    AnalysisResult,
    Asset,
    Channel,
    RetentionPoint,
    Study,
    StudyResult,
    StudyVideo,
    Video,
    VideoHeatmapPoint,
    VideoInfoCapture,
    iso,
    utcnow,
)
from .storage import absolute

log = logging.getLogger(__name__)
# Small, committable study outputs. Override (e.g. for demos) with CREATOR_LAB_RESULTS_DIR.
RESULTS_ROOT = Path(os.environ.get("CREATOR_LAB_RESULTS_DIR") or REPO_ROOT / "results")

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class StudyError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ modules


def discover_modules() -> dict[str, ModuleType]:
    import studies

    out = {}
    for info in pkgutil.iter_modules(studies.__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        mod = importlib.import_module(f"studies.{info.name}")
        if hasattr(mod, "SLUG") and hasattr(mod, "criteria") and hasattr(mod, "analyze"):
            out[mod.SLUG] = mod
    return out


def load_module(study: Study) -> ModuleType:
    # Reload so analysis always runs the code whose hash drift() checks.
    return importlib.reload(importlib.import_module(study.module))


def module_hash(module_name: str) -> str:
    spec = importlib.util.find_spec(module_name)
    if spec is None or spec.origin is None:
        raise StudyError(f"study module {module_name} not found", 500)
    return hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest()


def code_version(module_name: str) -> str:
    """git commit (+dirty) of the repo, or the module hash when git isn't available."""
    try:
        sha = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--", "studies"], capture_output=True, text=True
        ).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except Exception:
        return "module-" + module_hash(module_name)[:12]


def sync_studies() -> None:
    """Create a draft row for every study module; drafts follow the code, locked ones don't."""
    for slug, mod in discover_modules().items():
        rows = db.session.scalars(db.select(Study).where(Study.slug == slug).order_by(Study.version)).all()
        study = rows[-1] if rows else None
        if study is None:
            study = Study(slug=slug, version=1, module=mod.__name__, status="draft")
            db.session.add(study)
        if study.status == "draft":
            study.title = mod.TITLE
            study.summary = mod.SUMMARY
            study.methodology_md = mod.METHODOLOGY.strip()
            study.criteria = dict(mod.PARAMS)
            study.measures = list(mod.MEASURES)
            study.requires = list(mod.REQUIRES)
            study.module = mod.__name__
    db.session.commit()


# ------------------------------------------------------------------ the store modules read from


class DBStore:
    """Read-only facade over the database and result files for study modules."""

    def channels(self, *, is_mine: bool | None = None, owned: bool | None = None) -> list[dict]:
        q = db.select(Channel)
        if is_mine is not None:
            q = q.where(Channel.is_mine.is_(is_mine))
        if owned is not None:
            q = q.where(Channel.owned.is_(owned))
        return [
            {"id": c.id, "title": c.title, "handle": c.handle, "is_mine": c.is_mine, "owned": c.owned}
            for c in db.session.scalars(q.order_by(Channel.id))
        ]

    def videos(self, *, channel_ids=None, formats=None, min_duration_s=None, video_ids=None) -> list[dict]:
        q = db.select(Video).where(Video.gone.is_(False))
        if channel_ids is not None:
            q = q.where(Video.channel_id.in_(channel_ids))
        if video_ids is not None:
            q = q.where(Video.id.in_(video_ids))
        if formats:
            q = q.where(Video.format.in_(formats))
        if min_duration_s:
            q = q.where(Video.duration_s >= min_duration_s)
        return [
            {
                "id": v.id,
                "external_id": v.external_id,
                "channel_id": v.channel_id,
                "title": v.title,
                "published_at": iso(v.published_at),
                "duration_s": v.duration_s,
                "format": v.format,
                "views": v.views,
                "outlier_score": v.outlier_score,
                "selection": v.selection,
                "privacy": v.privacy,
            }
            for v in db.session.scalars(q.order_by(Video.published_at))
        ]

    def result(self, video_id: int, analyzer: str) -> dict | None:
        a = by_ref(analyzer)
        if a is None:
            return None
        r = db.session.scalar(
            db.select(AnalysisResult).where(AnalysisResult.video_id == video_id, AnalysisResult.analyzer_id == a.id)
        )
        if r is None:
            return None
        return {
            "status": r.status,
            "summary": r.summary or {},
            "path": absolute(r.output_path) if r.output_path else None,
        }

    def load_json(self, path):
        return _load_json(str(path), Path(path).stat().st_mtime if Path(path).exists() else 0)

    def retention(self, video_id: int, audience: str = "ALL") -> list[dict]:
        latest = db.session.scalar(
            db.select(db.func.max(RetentionPoint.sync_id)).where(RetentionPoint.video_id == video_id)
        )
        if latest is None:
            return []
        rows = db.session.scalars(
            db.select(RetentionPoint)
            .where(
                RetentionPoint.video_id == video_id,
                RetentionPoint.sync_id == latest,
                RetentionPoint.audience_type == audience,
            )
            .order_by(RetentionPoint.elapsed_ratio)
        )
        return [
            {
                "ratio": p.elapsed_ratio,
                "watch_ratio": p.audience_watch_ratio,
                "relative": p.relative_retention_performance,
            }
            for p in rows
        ]

    def heatmap(self, video_id: int) -> list[dict]:
        cap = db.session.scalar(
            db.select(VideoInfoCapture)
            .where(VideoInfoCapture.video_id == video_id, VideoInfoCapture.heatmap_points > 0)
            .order_by(VideoInfoCapture.captured_at.desc())
        )
        if cap is None:
            return []
        return [
            {"start_s": p.start_s, "end_s": p.end_s, "value": p.value}
            for p in db.session.scalars(
                db.select(VideoHeatmapPoint)
                .where(VideoHeatmapPoint.capture_id == cap.id)
                .order_by(VideoHeatmapPoint.start_s)
            )
        ]

    def captions_cues(self, video_id: int) -> list[dict]:
        a = db.session.scalar(db.select(Asset).where(Asset.video_id == video_id, Asset.kind == "captions"))
        if a is None:
            return []
        path = Path((a.meta or {}).get("cues_path") or absolute(a.path).with_suffix(".json"))
        if not path.exists():
            return []
        return self.load_json(path).get("cues", [])


@lru_cache(maxsize=256)
def _load_json(path: str, _mtime: float):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ study operations


def get_study(study_id: int) -> Study:
    s = db.session.get(Study, study_id)
    if s is None:
        raise StudyError("study not found", 404)
    return s


def drift(study: Study) -> dict:
    """Has the module changed since the study was locked?"""
    if not study.locked_hash:
        return {"changed": False, "current_hash": None}
    try:
        now = module_hash(study.module)
    except StudyError:
        return {"changed": True, "current_hash": None}
    return {"changed": now != study.locked_hash, "current_hash": now}


def run_criteria(study: Study, channel_id: int | None = None):
    mod = load_module(study)
    return mod.criteria(DBStore(), dict(study.criteria or mod.PARAMS), channel_id)


def select_members(study: Study) -> dict:
    if study.status == "analyzed":
        raise StudyError("this study is analyzed; create a new version to change its sample", 409)
    if drift(study)["changed"]:
        raise StudyError("the study's code changed since it was locked; create a new version", 409)
    crit = run_criteria(study)
    keep_manual = {m.video_id for m in study.members if m.role == "manual"}
    for m in list(study.members):
        if m.role != "manual":
            db.session.delete(m)
    db.session.flush()
    for m in crit.members:
        if m.video_id in keep_manual:
            continue
        db.session.add(
            StudyVideo(study_id=study.id, video_id=m.video_id, role=m.role, group=m.group, reason=m.reason, data=m.data)
        )
    study.criteria = {
        **(study.criteria or {}),
        "_last_selection": {"rules": crit.rules, "stats": crit.stats, "at": iso(utcnow())},
    }
    db.session.commit()
    return {"members": len(crit.members), "rules": crit.rules, "stats": crit.stats}


def add_members(study: Study, video_ids: list[int], channel_id: int | None) -> int:
    """Add pulled videos to a study, with their criteria role when the criteria pick them."""
    roles = {}
    if channel_id is not None:
        roles = {m.video_id: m for m in run_criteria(study, channel_id).members}
    existing = {m.video_id for m in study.members}
    added = 0
    for vid in video_ids:
        if vid in existing:
            continue
        m = roles.get(vid)
        db.session.add(
            StudyVideo(
                study_id=study.id,
                video_id=vid,
                role=m.role if m else "manual",
                group=m.group if m else None,
                reason=m.reason if m else "added from Discover",
                data=m.data if m else {},
            )
        )
        added += 1
    db.session.commit()
    return added


def lock(study: Study, by: str) -> None:
    if study.status != "draft":
        raise StudyError(f"study is {study.status}, not a draft", 409)
    study.locked_hash = module_hash(study.module)
    study.locked_at = utcnow()
    study.locked_by = by
    study.status = "locked"
    db.session.commit()


def new_version(study: Study) -> Study:
    latest = db.session.scalar(db.select(db.func.max(Study.version)).where(Study.slug == study.slug))
    mod = load_module(study)
    s = Study(
        slug=study.slug,
        version=latest + 1,
        module=study.module,
        status="draft",
        title=mod.TITLE,
        summary=mod.SUMMARY,
        methodology_md=mod.METHODOLOGY.strip(),
        criteria=dict(mod.PARAMS),
        measures=list(mod.MEASURES),
        requires=list(mod.REQUIRES),
    )
    db.session.add(s)
    db.session.commit()
    return s


def completeness(study: Study) -> dict:
    """Per member and required analyzer: done / queued / running / failed / missing / n/a."""
    reqs = []
    for ref in study.requires or []:
        a = by_ref(ref)
        if a is not None:
            reqs.append(a)
    ids = [m.video_id for m in study.members]
    status: dict[tuple[int, int], str] = {}
    if ids and reqs:
        for vid, aid, st in db.session.execute(
            db.select(AnalysisResult.video_id, AnalysisResult.analyzer_id, AnalysisResult.status).where(
                AnalysisResult.video_id.in_(ids), AnalysisResult.analyzer_id.in_([a.id for a in reqs])
            )
        ):
            status[(vid, aid)] = st
    members = []
    missing_total = 0
    for m in study.members:
        cells = {}
        for a in reqs:
            st = (
                "n/a"
                if a.applies_to and m.video.format not in a.applies_to
                else status.get((m.video_id, a.id), "missing")
            )
            cells[a.ref] = st
            if st in ("missing", "failed"):
                missing_total += 1
        members.append(
            {
                "video_id": m.video_id,
                "title": m.video.title,
                "channel_id": m.video.channel_id,
                "role": m.role,
                "group": m.group,
                "reason": m.reason,
                "selection": m.video.selection,
                "outlier_score": m.video.outlier_score,
                "duration_s": m.video.duration_s,
                "requirements": cells,
                "complete": all(v in ("done", "n/a") for v in cells.values()),
            }
        )
    return {
        "requirements": [a.ref for a in reqs],
        "members": members,
        "complete": sum(1 for m in members if m["complete"]),
        "missing_cells": missing_total,
    }


def fill_gaps(study: Study, by: str) -> dict:
    """Pull discovered members and issue jobs for every missing requirement."""
    from .ingest import pull_videos
    from .jobs import issue_job

    if drift(study)["changed"]:
        raise StudyError("the study's code changed since it was locked; create a new version", 409)
    ids = [m.video_id for m in study.members]
    if not ids:
        raise StudyError("the study has no members yet; select members first")
    discovered = [m.video_id for m in study.members if m.video.selection != "tracked"]
    pulled = pull_videos(discovered, created_by=by)["pulled"] if discovered else 0
    issued, skipped = [], []
    for ref in study.requires or []:
        a = by_ref(ref)
        if a is None or a.runner_type == "builtin":
            continue
        job = issue_job(a.name, ids, created_by=by)
        (issued if job else skipped).append(job.id if job else a.name)
    if study.status == "locked":
        study.status = "collecting"
    db.session.commit()
    return {"pulled": pulled, "jobs": issued, "nothing_to_do": skipped}


def analyze(study: Study) -> StudyResult:
    if drift(study)["changed"]:
        raise StudyError("the study's code changed since it was locked; create a new version", 409)
    mod = load_module(study)
    members = [{"video_id": m.video_id, "role": m.role, "group": m.group} for m in study.members]
    if not members:
        raise StudyError("the study has no members yet")
    out = RESULTS_ROOT / study.slug / f"v{study.version}"
    started = utcnow()
    res = mod.analyze(DBStore(), members, dict(study.criteria or mod.PARAMS), out)
    version = code_version(study.module)
    meta = {
        "study": study.slug,
        "version": study.version,
        "status_at_analysis": study.status,
        "pre_registered": study.status in ("locked", "collecting", "analyzed"),
        "code_version": version,
        "module_hash": module_hash(study.module),
        "locked_hash": study.locked_hash,
        "members": len(members),
        "computed_at": iso(utcnow()),
        "seconds": round((utcnow() - started).total_seconds(), 2),
    }
    (out / "results.json").write_text(
        json.dumps({"meta": meta, **res["results"]}, indent=1, default=_json_default), encoding="utf-8"
    )
    row = StudyResult(
        study_id=study.id,
        code_version=version,
        module_hash=meta["module_hash"],
        results=json.loads(json.dumps(res["results"], default=_json_default)),
        figures=res.get("figures", []),
        n_members=len(members),
    )
    db.session.add(row)
    if study.status in ("locked", "collecting"):
        study.status = "analyzed"
    db.session.commit()
    return row


def _json_default(o):
    import numpy as np

    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, dt.datetime | dt.date):
        return o.isoformat()
    return str(o)


def members_table(study: Study) -> list[dict]:
    out = []
    for m in study.members:
        v = m.video
        out.append(
            {
                "study": study.slug,
                "study_version": study.version,
                "video_id": v.id,
                "youtube_id": v.external_id,
                "channel": v.channel.title,
                "title": v.title,
                "published_at": iso(v.published_at),
                "duration_s": v.duration_s,
                "views": v.views,
                "outlier_score": v.outlier_score,
                "role": m.role,
                "group": m.group,
                "reason": m.reason,
            }
        )
    return out


def members_csv(study: Study) -> str:
    rows = members_table(study)
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return buf.getvalue()


def members_parquet(study: Study) -> bytes:
    import pandas as pd

    buf = io.BytesIO()
    pd.DataFrame(members_table(study)).to_parquet(buf, index=False)
    return buf.getvalue()


def figure_path(study: Study, name: str) -> Path:
    path = (RESULTS_ROOT / study.slug / f"v{study.version}" / name).resolve()
    if not path.is_relative_to(RESULTS_ROOT.resolve()) or not path.is_file():
        raise StudyError("figure not found", 404)
    return path

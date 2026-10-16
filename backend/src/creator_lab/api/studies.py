"""Studies: list, detail with completeness, select members, lock, new version, fill gaps,
analyze, export, figures."""

from __future__ import annotations

from flask import Response, send_file

from .. import background
from .. import study_service as svc
from ..auth import actor
from ..extensions import db
from ..models import Study, StudyResult, iso
from . import ApiError, bp


def call(fn, *args):
    try:
        return fn(*args)
    except svc.StudyError as err:
        raise ApiError(str(err), err.status) from err


def study_summary(s: Study) -> dict:
    latest = s.results[-1] if s.results else None
    return {
        "id": s.id,
        "slug": s.slug,
        "version": s.version,
        "title": s.title,
        "summary": s.summary,
        "status": s.status,
        "locked_at": iso(s.locked_at),
        "locked_by": s.locked_by,
        "members": len(s.members),
        "last_result_at": iso(latest.computed_at) if latest else None,
        "analyzing": background.is_running(f"study:{s.id}"),
    }


def result_json(r: StudyResult) -> dict:
    return {
        "id": r.id,
        "code_version": r.code_version,
        "module_hash": r.module_hash,
        "n_members": r.n_members,
        "computed_at": iso(r.computed_at),
        "results": r.results,
        "figures": [f"/api/studies/{r.study_id}/figures/{name}" for name in r.figures or []],
    }


@bp.get("/studies")
def list_studies():
    rows = db.session.scalars(db.select(Study).order_by(Study.slug, Study.version.desc())).all()
    return {"studies": [study_summary(s) for s in rows]}


@bp.get("/studies/<int:study_id>")
def get_study(study_id: int):
    s = call(svc.get_study, study_id)
    selection = (s.criteria or {}).get("_last_selection")
    params = {k: v for k, v in (s.criteria or {}).items() if not k.startswith("_")}
    return {
        "study": study_summary(s)
        | {
            "methodology_md": s.methodology_md,
            "params": params,
            "measures": s.measures or [],
            "requires": s.requires or [],
            "module": s.module,
            "locked_hash": s.locked_hash,
            "drift": svc.drift(s),
            "selection": selection,
        },
        "completeness": svc.completeness(s),
        "results": [result_json(r) for r in reversed(s.results)][:5],
    }


@bp.post("/studies/<int:study_id>/select")
def select_members(study_id: int):
    s = call(svc.get_study, study_id)
    return call(svc.select_members, s)


@bp.post("/studies/<int:study_id>/lock")
def lock_study(study_id: int):
    s = call(svc.get_study, study_id)
    call(svc.lock, s, actor())
    return {"study": study_summary(s)}


@bp.post("/studies/<int:study_id>/new-version")
def new_version(study_id: int):
    s = call(svc.get_study, study_id)
    return {"study": study_summary(call(svc.new_version, s))}


@bp.post("/studies/<int:study_id>/fill-gaps")
def fill_gaps(study_id: int):
    s = call(svc.get_study, study_id)
    return call(svc.fill_gaps, s, actor())


@bp.post("/studies/<int:study_id>/analyze")
def analyze(study_id: int):
    s = call(svc.get_study, study_id)
    if svc.drift(s)["changed"]:
        raise ApiError("the study's code changed since it was locked; create a new version", 409)
    if not s.members:
        raise ApiError("the study has no members yet")

    def run(sid):
        svc.analyze(svc.get_study(sid))

    started = background.submit(f"study:{s.id}", run, s.id)
    if not started:
        raise ApiError("analysis is already running", 409)
    db.session.refresh(s)
    return {"study": study_summary(s), "started": True}


@bp.get("/studies/<int:study_id>/members.csv")
def export_csv(study_id: int):
    s = call(svc.get_study, study_id)
    return Response(
        svc.members_csv(s),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{s.slug}-v{s.version}-members.csv"'},
    )


@bp.get("/studies/<int:study_id>/members.parquet")
def export_parquet(study_id: int):
    s = call(svc.get_study, study_id)
    return Response(
        svc.members_parquet(s),
        mimetype="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{s.slug}-v{s.version}-members.parquet"'},
    )


@bp.get("/studies/<int:study_id>/figures/<name>")
def figure(study_id: int, name: str):
    s = call(svc.get_study, study_id)
    return send_file(call(svc.figure_path, s, name), mimetype="image/png", max_age=60)

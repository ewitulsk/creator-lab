"""Scoring videos on the six levers with Gemini, storing every run, and pairwise judgments.

Blinding: the prompt is built from the rubric only (title, channel, description, views and
upload date never reach it). The default input is a low-resolution copy with no metadata
and a random file name, deleted from Gemini right after scoring. A YouTube URL is only sent
when input_mode is explicitly youtube_url (not blinded; for the URL-vs-upload A/B).
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import logging
import re
import statistics
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..extensions import db
from ..models import (
    Asset,
    Job,
    LeverCall,
    LeverMoment,
    LeverPairwise,
    LeverProbe,
    LeverScore,
    LeverSubscore,
    Video,
)
from ..storage import absolute, analyzer_dir, rel
from . import config as lcfg
from . import gemini, media, rubric

log = logging.getLogger(__name__)

ANALYZER = "levers_gemini"
PAIRWISE = "levers_gemini_pairwise"
ANALYZER_VERSION = "1"
METHOD = rubric.METHOD
HIGH_SPREAD = 2  # flag a lever when max - min across runs is at least this

# Rough token model for estimates (static sampling at low media resolution: 66 tokens per
# frame at 1 frame/s, plus ~32 tokens/s of audio). Agentic mode may read more frames.
TOKENS_PER_VIDEO_SECOND = 98
PROMPT_TOKENS = 2500
OUTPUT_TOKENS_PER_RUN = 3000
THOUGHT_TOKENS_PER_RUN = 3000
PROBE_OUTPUT_TOKENS = 200
PAIRWISE_OUTPUT_TOKENS = 1500


class LeverError(RuntimeError):
    pass


def youtube_url(v: Video) -> str:
    return f"https://www.youtube.com/watch?v={v.external_id}"


def scrub(text: str | None) -> str | None:
    """Never let the key reach a log line, an error column or an API response."""
    key = lcfg.api_key()
    if text and key:
        text = text.replace(key, "***")
    return text


# ------------------------------------------------------------------ resume / skip


def current_key(cfg: dict, r: rubric.Rubric) -> dict:
    return {
        "method": METHOD,
        "analyzer_version": ANALYZER_VERSION,
        "rubric_hash": r.hash,
        "model": cfg["model"],
        "input_mode": cfg["input_mode"],
    }


def already_scored(video_ids: list[int], cfg: dict, r: rubric.Rubric) -> set[int]:
    """Videos with an aggregate at this analyzer version + rubric hash + model + input mode."""
    k = current_key(cfg, r)
    q = db.select(LeverScore.video_id).where(
        LeverScore.video_id.in_(video_ids),
        LeverScore.run_idx.is_(None),
        LeverScore.method == k["method"],
        LeverScore.analyzer_version == k["analyzer_version"],
        LeverScore.rubric_hash == k["rubric_hash"],
        LeverScore.model == k["model"],
    )
    # An upload fallback counts as done for a youtube_url request (that's what fallback means).
    if k["input_mode"] == "upload":
        q = q.where(LeverScore.input_mode == "upload")
    return set(db.session.scalars(q))


# ------------------------------------------------------------------ estimates


def estimate(video_ids: list[int], cfg: dict | None = None) -> dict:
    cfg = cfg or lcfg.get_config()
    r = rubric.load()
    videos = list(db.session.scalars(db.select(Video).where(Video.id.in_(video_ids))))
    done = already_scored([v.id for v in videos], cfg, r)
    todo = [v for v in videos if v.id not in done]
    seconds = sum(v.duration_s or 0 for v in todo)
    runs = int(cfg["runs"])
    video_tokens = seconds * TOKENS_PER_VIDEO_SECOND
    input_tokens = (runs + 1) * video_tokens + len(todo) * (runs * PROMPT_TOKENS + 200)
    output_tokens = len(todo) * (runs * OUTPUT_TOKENS_PER_RUN + PROBE_OUTPUT_TOKENS)
    thought = len(todo) * runs * THOUGHT_TOKENS_PER_RUN
    return {
        "model": cfg["model"],
        "input_mode": cfg["input_mode"],
        "videos": len(todo),
        "already_scored": len(done),
        "video_seconds": seconds,
        "calls": len(todo) * (runs + 1),
        "runs_per_video": runs,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens + thought),
        "cost_usd": round(lcfg.cost_usd(cfg["model"], int(input_tokens), int(output_tokens), int(thought)), 4),
        "assumptions": f"~{TOKENS_PER_VIDEO_SECOND} tokens per video second, {OUTPUT_TOKENS_PER_RUN} output and "
        f"{THOUGHT_TOKENS_PER_RUN} thinking tokens per run; agentic mode can read more frames, so treat this as "
        "a rough guide",
    }


def estimate_pairwise(pairs: list[tuple[int, int]], cfg: dict | None = None) -> dict:
    cfg = cfg or lcfg.get_config()
    ids = {i for p in pairs for i in p}
    dur = {v.id: v.duration_s or 0 for v in db.session.scalars(db.select(Video).where(Video.id.in_(ids)))}
    input_tokens = sum(
        2 * ((dur.get(a, 0) + dur.get(b, 0)) * TOKENS_PER_VIDEO_SECOND + PROMPT_TOKENS) for a, b in pairs
    )
    output_tokens = len(pairs) * 2 * (PAIRWISE_OUTPUT_TOKENS + THOUGHT_TOKENS_PER_RUN)
    return {
        "model": cfg["model"],
        "pairs": len(pairs),
        "calls": 2 * len(pairs),
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cost_usd": round(lcfg.cost_usd(cfg["model"], int(input_tokens), int(output_tokens)), 4),
    }


def job_cost(job_id: int) -> dict:
    row = db.session.execute(
        db.select(
            db.func.count(LeverCall.id),
            db.func.coalesce(db.func.sum(LeverCall.input_tokens), 0),
            db.func.coalesce(db.func.sum(LeverCall.output_tokens + LeverCall.thought_tokens), 0),
            db.func.coalesce(db.func.sum(LeverCall.cost_usd), 0.0),
        ).where(LeverCall.job_id == job_id)
    ).one()
    return {"calls": row[0], "input_tokens": int(row[1]), "output_tokens": int(row[2]), "cost_usd": round(row[3], 4)}


# ------------------------------------------------------------------ running


@dataclass
class Ctx:
    """What a scoring run needs from its job runner."""

    job: Job | None
    client: object
    cfg: dict
    rubric: rubric.Rubric
    progress: Callable[[str], None] = lambda msg: None
    cancelled: Callable[[], bool] = lambda: False
    sleep: Callable[[float], None] = time.sleep
    workdir: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "creator-lab-levers")
    _last_call: float = 0.0

    def pace(self) -> None:
        gap = float(self.cfg.get("min_seconds_between_calls", 0))
        wait = self._last_call + gap - time.monotonic()
        if wait > 0:
            self.sleep(wait)
        self._last_call = time.monotonic()


class VideoInput:
    """The video part for a call: the blinded upload (default) or the YouTube URL."""

    def __init__(self, ctx: Ctx, video: Video):
        self.ctx, self.video = ctx, video
        self.mode = ctx.cfg["input_mode"]
        self.fallback = False
        self._uploaded: gemini.Uploaded | None = None
        self._copy: Path | None = None

    def part(self) -> dict:
        if self.mode == "youtube_url":
            return {"kind": "video", "uri": youtube_url(self.video)}
        if self._uploaded is None:
            self._uploaded = self._upload()
        return {"kind": "video", "uri": self._uploaded.uri, "mime_type": self._uploaded.mime_type}

    def fall_back_to_upload(self) -> None:
        self.mode, self.fallback = "upload", True

    def _upload(self) -> gemini.Uploaded:
        asset = db.session.scalar(db.select(Asset).where(Asset.video_id == self.video.id, Asset.kind == "video"))
        if asset is None or not absolute(asset.path).exists():
            raise LeverError("the video isn't downloaded (needed for the blinded upload)")
        self._copy = media.make_blind_copy(
            absolute(asset.path), self.ctx.workdir, int(self.ctx.cfg["upload_height"]), int(self.ctx.cfg["upload_fps"])
        )
        return gemini.with_retries(lambda: self.ctx.client.upload(self._copy), sleep=self.ctx.sleep)

    def close(self) -> None:
        if self._uploaded is not None:
            try:
                self.ctx.client.delete(self._uploaded.name)
            except Exception as err:  # never lose scores over a cleanup failure
                log.warning("could not delete the uploaded file from Gemini: %s", scrub(str(err)))
            self._uploaded = None
        if self._copy is not None:
            self._copy.unlink(missing_ok=True)
            self._copy = None


def _call(ctx: Ctx, kind: str, video: Video, parts: list[dict], schema: dict, temperature: float, *, run_idx=None,
          other: Video | None = None, input_mode: str | None = None) -> tuple[dict | None, LeverCall]:  # fmt: skip
    """One Gemini call, retried on rate limits; JSON parsed (validation is the caller's)."""
    ctx.pace()
    model = ctx.cfg["model"]
    call = LeverCall(
        job_id=ctx.job.id if ctx.job else None,
        video_id=video.id,
        other_video_id=other.id if other else None,
        kind=kind,
        run_idx=run_idx,
        model=model,
        input_mode=input_mode,
    )
    db.session.add(call)
    try:
        out: gemini.Generated = gemini.with_retries(
            lambda: ctx.client.generate(model, parts, schema, temperature, bool(ctx.cfg.get("agentic", True))),
            sleep=ctx.sleep,
        )
    except gemini.GeminiError as err:
        call.ok, call.error = False, scrub(str(err))
        db.session.commit()
        raise
    call.input_tokens, call.output_tokens, call.thought_tokens = out.input_tokens, out.output_tokens, out.thought_tokens
    call.cost_usd = lcfg.cost_usd(model, out.input_tokens, out.output_tokens, out.thought_tokens)
    # Raw response under the data folder (never the repo). The request has no secrets in it.
    folder = analyzer_dir(PAIRWISE if kind == "pairwise" else ANALYZER, ANALYZER_VERSION, video.external_id) / "raw"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = folder / f"{stamp}-{kind}{'' if run_idx is None else f'-run{run_idx}'}.json"
    path.write_text(
        json.dumps(
            {
                "kind": kind,
                "model": model,
                "input_mode": input_mode,
                "rubric_hash": ctx.rubric.hash,
                "run_idx": run_idx,
                "other_video_id": other.id if other else None,
                "temperature": temperature,
                "usage": {"input": out.input_tokens, "output": out.output_tokens, "thought": out.thought_tokens},
                "meta": out.raw,
                "text": out.text,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    call.raw_path = rel(path)
    db.session.commit()
    try:
        return json.loads(_strip_fences(out.text)), call
    except json.JSONDecodeError:
        return None, call


def _strip_fences(text: str) -> str:
    m = re.match(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", text or "", re.S)
    return m.group(1) if m else (text or "")


def _validated(
    ctx: Ctx, kind, video, parts, schema, temperature, validate, *, inp: VideoInput, run_idx=None, other=None
):
    """Call, validate, and retry once on bad JSON. A rejected URL falls back to upload."""
    for attempt in (1, 2):
        try:
            data, _ = _call(ctx, kind, video, parts(), schema, temperature, run_idx=run_idx, other=other,
                            input_mode=inp.mode)  # fmt: skip
        except gemini.UrlRejected:
            if inp.mode != "youtube_url":
                raise
            log.warning("Gemini rejected the YouTube URL for video %s; falling back to upload", video.id)
            inp.fall_back_to_upload()
            data, _ = _call(ctx, kind, video, parts(), schema, temperature, run_idx=run_idx, other=other,
                            input_mode=inp.mode)  # fmt: skip
        try:
            if data is None:
                raise rubric.InvalidResponse("response was not valid JSON")
            return validate(data)
        except rubric.InvalidResponse as err:
            if attempt == 2:
                raise LeverError(f"Gemini returned an invalid {kind} response twice: {err}") from err
            log.warning("invalid %s response for video %s (%s); retrying once", kind, video.id, err)
    raise AssertionError("unreachable")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def is_contaminated(probe: dict, video: Video) -> bool:
    """The probe named the creator or the video."""
    if not probe["identified"]:
        return False
    creator = _norm(probe["creator"])
    names = {_norm(video.channel.title or ""), _norm((video.channel.handle or "").lstrip("@"))} - {""}
    if creator not in ("", "unknown") and any(n in creator or creator in n for n in names):
        return True
    guess, title = _norm(probe["title"]), _norm(video.title or "")
    return guess not in ("", "unknown") and difflib.SequenceMatcher(None, guess, title).ratio() >= 0.6


def aggregate(runs: list[dict], r: rubric.Rubric) -> dict:
    """Median per lever and sub-item, spread = max - min, flags for big spreads."""
    out = {}
    for lever in rubric.LEVERS:
        scores = [run["levers"][lever]["score"] for run in runs]
        med = statistics.median(scores)
        spread = max(scores) - min(scores)
        closest = min(runs, key=lambda run: abs(run["levers"][lever]["score"] - med))["levers"][lever]
        conf = Counter(run["levers"][lever]["confidence"] for run in runs).most_common(1)[0][0]
        subs = {
            item: statistics.median(run["levers"][lever]["subscores"][item] for run in runs) for item in r.items(lever)
        }
        if lever == "attention":
            subs["hook_score"] = statistics.median(run["hook_score"] for run in runs)
        out[lever] = {
            "score": float(med),
            "spread": float(spread),
            "confidence": conf,
            "reason": closest["reason"],
            "evidence": closest["evidence"],
            "subscores": subs,
            "flags": ["high_spread"] if spread >= HIGH_SPREAD else [],
        }
    return out


def score_video(ctx: Ctx, video: Video) -> dict:
    """Score one video (runs + probe), store everything, return the item summary."""
    r, cfg = ctx.rubric, ctx.cfg
    prompt = rubric.score_prompt(r)
    schema = rubric.score_schema(r)
    n_runs = int(cfg["runs"])
    inp = VideoInput(ctx, video)
    try:
        runs = []
        for k in range(1, n_runs + 1):
            if ctx.cancelled():
                raise LeverError("cancelled")
            ctx.progress(f"run {k} of {n_runs}")
            runs.append(
                _validated(
                    ctx,
                    "score",
                    video,
                    lambda: [{"kind": "text", "text": prompt}, inp.part()],
                    schema,
                    float(cfg["temperature"]),
                    lambda d: rubric.validate_scores(d, r),
                    inp=inp,
                    run_idx=k,
                )  # fmt: skip
            )
        ctx.progress("contamination probe")
        probe = _validated(
            ctx, "probe", video, lambda: [{"kind": "text", "text": rubric.PROBE_PROMPT}, inp.part()],
            rubric.PROBE_SCHEMA, 0.0, rubric.validate_probe, inp=inp,
        )  # fmt: skip
    finally:
        inp.close()
    contaminated = is_contaminated(probe, video)
    return store_scores(ctx, video, runs, probe, contaminated, inp.mode, inp.fallback)


def store_scores(ctx: Ctx, video: Video, runs: list[dict], probe: dict, contaminated: bool, mode: str,
                 fallback: bool) -> dict:  # fmt: skip
    r, cfg = ctx.rubric, ctx.cfg
    base = {
        "video_id": video.id,
        "method": METHOD,
        "analyzer_version": ANALYZER_VERSION,
        "rubric_hash": r.hash,
        "model": cfg["model"],
        "input_mode": mode,
        "job_id": ctx.job.id if ctx.job else None,
    }
    common_flags = []
    if contaminated:
        common_flags.append("possibly_contaminated")
    if mode == "youtube_url":
        common_flags.append("not_blinded_url_input")
    if fallback:
        common_flags.append("url_rejected_fell_back_to_upload")
    db.session.add(
        LeverProbe(
            video_id=video.id,
            model=cfg["model"],
            input_mode=mode,
            identified=probe["identified"],
            creator_guess=probe["creator"],
            title_guess=probe["title"],
            confidence=probe["confidence"],
            contaminated=contaminated,
            job_id=base["job_id"],
        )
    )
    for k, run in enumerate(runs, 1):
        for lever in rubric.LEVERS:
            x = run["levers"][lever]
            row = LeverScore(**base, lever=lever, rubric_version=r.version, run_idx=k, score=float(x["score"]),
                             confidence=x["confidence"], reason=x["reason"], evidence=x["evidence"],
                             flags=list(common_flags))  # fmt: skip
            db.session.add(row)
            db.session.flush()
            subs = dict(x["subscores"])
            if lever == "attention":
                subs["hook_score"] = run["hook_score"]
            for item, s in subs.items():
                db.session.add(
                    LeverSubscore(score_id=row.id, video_id=video.id, lever=lever, item=item, score=float(s))
                )
            for e in x["evidence"]:
                db.session.add(_moment(base, k, "evidence", lever, e["t_start"], e["t_end"], None, e["note"]))
        for m in run["emotional_moments"]:
            db.session.add(_moment(base, k, m["kind"], "emotion", m["t"], None, m["intensity"], m["emotion"]))
    agg = aggregate(runs, r)
    for lever, x in agg.items():
        row = LeverScore(**base, lever=lever, rubric_version=r.version, run_idx=None, score=x["score"],
                         spread=x["spread"], n_runs=len(runs), confidence=x["confidence"], reason=x["reason"],
                         evidence=x["evidence"], flags=common_flags + x["flags"])  # fmt: skip
        db.session.add(row)
        db.session.flush()
        for item, s in x["subscores"].items():
            db.session.add(LeverSubscore(score_id=row.id, video_id=video.id, lever=lever, item=item, score=float(s)))
    db.session.commit()
    return {
        "scores": {lv: agg[lv]["score"] for lv in rubric.LEVERS},
        "spread": {lv: agg[lv]["spread"] for lv in rubric.LEVERS},
        "flags": sorted({f for lv in agg.values() for f in lv["flags"]} | set(common_flags)),
        "runs": len(runs),
        "model": cfg["model"],
        "input_mode": mode,
        "rubric_hash": r.hash[:12],
    }


def _moment(base, run_idx, kind, lever, t0, t1, intensity, label) -> LeverMoment:
    return LeverMoment(
        video_id=base["video_id"],
        method=base["method"],
        analyzer_version=base["analyzer_version"],
        rubric_hash=base["rubric_hash"],
        model=base["model"],
        input_mode=base["input_mode"],
        run_idx=run_idx,
        kind=kind,
        lever=lever,
        t_start=float(t0),
        t_end=None if t1 is None else float(t1),
        intensity=intensity,
        label=(label or "")[:500],
        job_id=base["job_id"],
    )


def judge_pair(ctx: Ctx, a: Video, b: Video, *, study_id: int | None = None, pair_key: str | None = None) -> dict:
    """Pairwise judgment in both orders (cancels position bias). Stores 12 rows."""
    r = ctx.rubric
    prompt = rubric.pairwise_prompt(r)
    schema = rubric.pairwise_schema()
    ia, ib = VideoInput(ctx, a), VideoInput(ctx, b)
    results = {}
    try:
        for order, first, second, i1, i2 in (("ab", a, b, ia, ib), ("ba", b, a, ib, ia)):
            if ctx.cancelled():
                raise LeverError("cancelled")
            ctx.progress(f"pair {a.id} vs {b.id}, order {order}")

            def parts(i1=i1, i2=i2):
                return [
                    {"kind": "text", "text": prompt},
                    {"kind": "text", "text": "Video A:"},
                    i1.part(),
                    {"kind": "text", "text": "Video B:"},
                    i2.part(),
                ]

            temperature = float(ctx.cfg["temperature"])
            judged = _validated(
                ctx, "pairwise", first, parts, schema, temperature, rubric.validate_pairwise, inp=i1, other=second
            )
            results[order] = (first, second, judged)
    finally:
        ia.close()
        ib.close()
    mode = ia.mode if ia.mode == ib.mode else "mixed"
    for order, (first, second, data) in results.items():
        for lever, x in data["levers"].items():
            winner = {"A": first.id, "B": second.id}.get(x["winner"])
            db.session.add(
                LeverPairwise(
                    study_id=study_id,
                    pair_key=pair_key,
                    video_a=first.id,
                    video_b=second.id,
                    order=order,
                    lever=lever,
                    winner_video_id=winner,
                    confidence=x["confidence"],
                    reason=x["reason"],
                    method=METHOD,
                    analyzer_version=ANALYZER_VERSION,
                    rubric_hash=r.hash,
                    model=ctx.cfg["model"],
                    input_mode=mode,
                    job_id=ctx.job.id if ctx.job else None,
                )  # fmt: skip
            )
    db.session.commit()
    agree = sum(
        1
        for lv in rubric.LEVERS
        if {"A": results["ab"][0].id, "B": results["ab"][1].id}.get(results["ab"][2]["levers"][lv]["winner"])
        == {"A": results["ba"][0].id, "B": results["ba"][1].id}.get(results["ba"][2]["levers"][lv]["winner"])
    )
    return {"pair": [a.id, b.id], "orders_agree": agree}


# ------------------------------------------------------------------ reads (API, exports, studies)


def video_levers(video_id: int, method: str | None = None) -> dict:
    q = db.select(LeverScore).where(LeverScore.video_id == video_id)
    if method:
        q = q.where(LeverScore.method == method)
    rows = list(db.session.scalars(q.order_by(LeverScore.created_at.desc(), LeverScore.id)))
    if not rows:
        return {"scorings": [], "moments": [], "probe": None}
    subs: dict[int, dict] = {}
    for s in db.session.scalars(db.select(LeverSubscore).where(LeverSubscore.score_id.in_([x.id for x in rows]))):
        subs.setdefault(s.score_id, {})[s.item] = s.score
    # Group into scorings: same job + config key.
    groups: dict[tuple, dict] = {}
    for x in rows:
        key = (x.job_id, x.method, x.analyzer_version, x.rubric_hash, x.model, x.input_mode)
        g = groups.setdefault(
            key,
            {
                "method": x.method,
                "analyzer_version": x.analyzer_version,
                "rubric_version": x.rubric_version,
                "rubric_hash": x.rubric_hash,
                "model": x.model,
                "input_mode": x.input_mode,
                "job_id": x.job_id,
                "created_at": x.created_at.isoformat() + "Z",
                "aggregate": {},
                "runs": {},
            },
        )
        entry = {
            "score": x.score,
            "spread": x.spread,
            "confidence": x.confidence,
            "reason": x.reason,
            "evidence": x.evidence or [],
            "flags": x.flags or [],
            "subscores": subs.get(x.id, {}),
        }
        if x.run_idx is None:
            g["aggregate"][x.lever] = entry
        else:
            g["runs"].setdefault(str(x.run_idx), {})[x.lever] = entry
    latest = next(iter(groups.values()))
    moments = [
        {
            "kind": m.kind,
            "lever": m.lever,
            "t_start": m.t_start,
            "t_end": m.t_end,
            "intensity": m.intensity,
            "label": m.label,
            "run_idx": m.run_idx,
        }
        for m in db.session.scalars(
            db.select(LeverMoment)
            .where(
                LeverMoment.video_id == video_id,
                LeverMoment.job_id == latest["job_id"] if latest["job_id"] else LeverMoment.job_id.is_(None),
                LeverMoment.rubric_hash == latest["rubric_hash"],
                LeverMoment.model == latest["model"],
            )
            .order_by(LeverMoment.t_start)
        )
    ]
    probe = db.session.scalar(
        db.select(LeverProbe).where(LeverProbe.video_id == video_id).order_by(LeverProbe.created_at.desc())
    )
    return {
        "scorings": list(groups.values()),
        "moments": moments,
        "probe": None
        if probe is None
        else {
            "identified": probe.identified,
            "creator_guess": probe.creator_guess,
            "title_guess": probe.title_guess,
            "confidence": probe.confidence,
            "contaminated": probe.contaminated,
            "model": probe.model,
            "input_mode": probe.input_mode,
        },
    }


def export_rows(video_ids: list[int] | None = None) -> list[dict]:
    """One row per lever score (aggregates and runs) with sub-scores flattened."""
    q = db.select(LeverScore, Video).join(Video, Video.id == LeverScore.video_id)
    if video_ids:
        q = q.where(LeverScore.video_id.in_(video_ids))
    pairs = list(db.session.execute(q.order_by(LeverScore.video_id, LeverScore.id)))
    subs: dict[int, dict] = {}
    for s in db.session.scalars(db.select(LeverSubscore).where(LeverSubscore.score_id.in_([p[0].id for p in pairs]))):
        subs.setdefault(s.score_id, {})[s.item] = s.score
    out = []
    for s, v in pairs:
        row = {
            "video_id": v.id,
            "youtube_id": v.external_id,
            "lever": s.lever,
            "method": s.method,
            "analyzer_version": s.analyzer_version,
            "rubric_version": s.rubric_version,
            "rubric_hash": s.rubric_hash,
            "model": s.model,
            "input_mode": s.input_mode,
            "kind": "aggregate" if s.run_idx is None else "run",
            "run_idx": s.run_idx,
            "score": s.score,
            "spread": s.spread,
            "confidence": s.confidence,
            "flags": ",".join(s.flags or []),
            "reason": s.reason,
            "created_at": s.created_at.isoformat() + "Z",
        }
        for item, val in subs.get(s.id, {}).items():
            row[f"sub_{item}"] = val
        out.append(row)
    return out


def ab_comparison(video_ids: list[int], model: str | None = None) -> list[dict]:
    """Per video and lever: latest upload aggregate vs latest youtube_url aggregate."""
    out = []
    for vid in video_ids:
        q = db.select(LeverScore).where(LeverScore.video_id == vid, LeverScore.run_idx.is_(None),
                                        LeverScore.method == METHOD)  # fmt: skip
        if model:
            q = q.where(LeverScore.model == model)
        latest: dict[tuple[str, str], LeverScore] = {}
        for s in db.session.scalars(q.order_by(LeverScore.created_at)):
            latest[(s.input_mode or "", s.lever)] = s
        for lever in rubric.LEVERS:
            up, url = latest.get(("upload", lever)), latest.get(("youtube_url", lever))
            out.append(
                {
                    "video_id": vid,
                    "lever": lever,
                    "upload": up.score if up else None,
                    "youtube_url": url.score if url else None,
                    "difference": None if not (up and url) else round(url.score - up.score, 3),
                }
            )
    return out

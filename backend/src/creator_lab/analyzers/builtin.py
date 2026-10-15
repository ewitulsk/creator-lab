"""Analyzers computed in-app from the stats store (cheap, no runner needed).

outlier_score@1
    A video's views divided by the median views of the channel's 10 nearest uploads
    (by upload order), compared at the same video age. Shorts and long-form get
    separate baselines; live streams and premieres get none.

    "Same age": for a video V of age A, each neighbour's views at age A are
    interpolated from its snapshot history. When both V and the neighbour are older
    than the settle age (30 days), views have mostly stopped growing, so current
    views are compared directly (this is what Dataset/build_dataset.py did, and the
    only option for videos backfilled long after upload). A neighbour whose views at
    age A are unknown is skipped; fewer than 3 usable neighbours gives no score.

views_at_age@1
    Views at 1, 7, 30, 90 and 365 days, interpolated from snapshots (with 0 views at
    publication). Only ages inside the observed history are reported.
"""

from __future__ import annotations

import bisect
import datetime as dt
import statistics
from dataclasses import dataclass

from ..extensions import db
from ..models import AnalysisResult, Video, VideoSnapshot, utcnow
from . import Spec, current, register

OUTLIER = register(
    Spec(
        name="outlier_score",
        version="1",
        title="Outlier score",
        runner_type="builtin",
        description="Views ÷ median of the channel's 10 nearest uploads at equal age. "
        "Separate baselines for Shorts and long-form.",
        applies_to=["long", "short"],
    )
)
VIEWS_AT_AGE = register(
    Spec(
        name="views_at_age",
        version="1",
        title="Views at age",
        runner_type="builtin",
        description="Views at 1, 7, 30, 90 and 365 days after upload, interpolated from snapshots.",
    )
)

NEIGHBOURS = 10
MIN_NEIGHBOURS = 3
SETTLE_DAYS = 30
AGES = (1, 7, 30, 90, 365)


@dataclass
class History:
    """A video's (age in days, views) points, starting at (0, 0).

    Views grow fast early and slowly later, so a straight line between two far-apart
    readings is wrong. Interpolation is only trusted when the bracketing readings are
    close: at most max(2 days, half the requested age) apart. A video first seen
    years after upload therefore has no known views at day 2.
    """

    ages: list[float]
    views: list[float]

    def at(self, age: float) -> float | None:
        if not self.ages or age < 0 or age > self.ages[-1]:
            return None
        i = bisect.bisect_left(self.ages, age)
        if self.ages[i] == age:
            return self.views[i]
        a0, a1 = self.ages[i - 1], self.ages[i]
        if a1 - a0 > max(2.0, age / 2):
            return None
        v0, v1 = self.views[i - 1], self.views[i]
        return v0 + (v1 - v0) * (age - a0) / (a1 - a0)


def age_days(published: dt.datetime | None, at: dt.datetime) -> float | None:
    if published is None:
        return None
    return max((at - published).total_seconds() / 86400, 0.0)


def load_histories(videos: list[Video]) -> dict[int, History]:
    ids = [v.id for v in videos]
    pub = {v.id: v.published_at for v in videos}
    points: dict[int, list[tuple[float, float]]] = {i: [] for i in ids}
    rows = db.session.execute(
        db.select(VideoSnapshot.video_id, VideoSnapshot.captured_at, VideoSnapshot.views)
        .where(VideoSnapshot.video_id.in_(ids), VideoSnapshot.views.is_not(None))
        .order_by(VideoSnapshot.captured_at)
    )
    for vid, at, views in rows:
        a = age_days(pub[vid], at)
        if a is not None:
            points[vid].append((a, float(views)))
    out = {}
    for vid, pts in points.items():
        ages, views = [0.0], [0.0]
        for a, v in pts:
            if a <= ages[-1]:  # same instant or clock skew: keep the later reading
                views[-1] = max(views[-1], v) if a == ages[-1] else views[-1]
                continue
            ages.append(a)
            views.append(max(v, views[-1]))  # views never go down (except fraud removal)
        out[vid] = History(ages, views)
    return out


def outlier_scores(videos: list[Video], now: dt.datetime) -> dict[int, dict]:
    """Scores for one channel's videos of one format group, ordered by upload time."""
    videos = sorted((v for v in videos if v.published_at and v.views is not None), key=lambda v: v.published_at)
    hist = load_histories(videos)
    out = {}
    for i, v in enumerate(videos):
        age_v = age_days(v.published_at, now)
        order = sorted((j for j in range(len(videos)) if j != i), key=lambda j: (abs(j - i), j))
        values, methods = [], set()
        for j in order:
            if len(values) == NEIGHBOURS:
                break
            n = videos[j]
            age_n = age_days(n.published_at, now)
            if age_v >= SETTLE_DAYS and age_n >= SETTLE_DAYS:
                values.append(float(n.views))
                methods.add("settled")
                continue
            at = hist[n.id].at(age_v)
            if at is not None:
                values.append(at)
                methods.add("interpolated")
        if len(values) < MIN_NEIGHBOURS:
            out[v.id] = {
                "score": None,
                "reason": f"only {len(values)} comparable neighbours",
                "age_days": age_v,
            }
            continue
        baseline = statistics.median(values)
        out[v.id] = {
            "score": round(v.views / baseline, 4) if baseline > 0 else None,
            "baseline": baseline,
            "views": v.views,
            "neighbours": len(values),
            "age_days": round(age_v, 2),
            "method": "+".join(sorted(methods)),
        }
    return out


def views_at_ages(v: Video, hist: History) -> dict:
    return {f"d{a}": (round(x) if (x := hist.at(a)) is not None else None) for a in AGES}


def _store(video_id: int, analyzer_id: int, summary: dict, now: dt.datetime, done: bool = True) -> None:
    row = db.session.scalar(
        db.select(AnalysisResult).where(AnalysisResult.video_id == video_id, AnalysisResult.analyzer_id == analyzer_id)
    )
    if row is None:
        row = AnalysisResult(video_id=video_id, analyzer_id=analyzer_id)
        db.session.add(row)
    row.status = "done" if done else "failed"
    row.summary = summary
    row.error = None if done else summary.get("reason")
    row.computed_at = now


def compute_channel(channel_id: int) -> int:
    """Recompute the built-in analyzers for every video of a channel."""
    now = utcnow()
    outlier = current(OUTLIER.name)
    vaa = current(VIEWS_AT_AGE.name)
    videos = list(db.session.scalars(db.select(Video).where(Video.channel_id == channel_id, Video.gone.is_(False))))
    for group in ("long", "short"):
        members = [v for v in videos if v.format == group]
        for vid, summary in outlier_scores(members, now).items():
            _store(vid, outlier.id, summary, now)
    by_id = {v.id: v for v in videos}
    for v in videos:
        if v.format not in ("long", "short"):
            v.outlier_score = None
            v.outlier_baseline = None
    rows = db.session.scalars(
        db.select(AnalysisResult).where(
            AnalysisResult.analyzer_id == outlier.id, AnalysisResult.video_id.in_(list(by_id))
        )
    )
    for r in rows:
        v = by_id[r.video_id]
        if v.format in ("long", "short"):
            v.outlier_score = r.summary.get("score")
            v.outlier_baseline = r.summary.get("baseline")
    hist = load_histories(videos)
    for v in videos:
        _store(v.id, vaa.id, views_at_ages(v, hist[v.id]), now)
    db.session.commit()
    return len(videos)

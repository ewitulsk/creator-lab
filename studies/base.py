"""What a study module provides, the store it reads from, and shared statistics.

A study module defines:
    SLUG, TITLE, SUMMARY      identity and one-paragraph summary
    METHODOLOGY               markdown: hypotheses, criteria, measures, tests
    PARAMS                    criteria parameters (frozen when the study is locked)
    REQUIRES                  ["analyzer@version", ...]: drives completeness and gap filling
    MEASURES                  pre-registered measures (frozen when locked)
    criteria(store, params, channel_id=None) -> Criteria
    analyze(store, members, params, out_dir) -> {"results": {...}, "figures": [file names]}

criteria() must work on one channel (channel_id) so Discover can show "videos needed for
study X". analyze() writes figures into out_dir and returns JSON-able results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np


@dataclass
class Member:
    video_id: int
    role: str
    group: str | None = None
    reason: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class Criteria:
    members: list[Member]
    rules: str  # plain-language rendering of what was selected and why
    stats: dict = field(default_factory=dict)


class Store(Protocol):
    """Read-only access to the platform's data for study modules."""

    def channels(self, *, is_mine: bool | None = None, owned: bool | None = None) -> list[dict]: ...
    def videos(
        self,
        *,
        channel_ids: list[int] | None = None,
        formats: list[str] | None = None,
        min_duration_s: int | None = None,
        video_ids: list[int] | None = None,
    ) -> list[dict]: ...
    def result(self, video_id: int, analyzer: str) -> dict | None: ...
    def load_json(self, path: Path | str) -> Any: ...
    def retention(self, video_id: int, audience: str = "ALL") -> list[dict]: ...
    def heatmap(self, video_id: int) -> list[dict]: ...
    def captions_cues(self, video_id: int) -> list[dict]: ...


# ------------------------------------------------------------------ statistics


def wilcoxon_paired(diffs: list[float]) -> dict:
    from scipy import stats

    d = np.asarray([x for x in diffs if x is not None and np.isfinite(x)], dtype=float)
    if len(d) < 3 or np.allclose(d, 0):
        return {"n": int(len(d)), "statistic": None, "p": None, "median_diff": float(np.median(d)) if len(d) else None}
    res = stats.wilcoxon(d)
    return {
        "n": int(len(d)),
        "statistic": float(res.statistic),
        "p": float(res.pvalue),
        "median_diff": float(np.median(d)),
    }


def sign_flip_test(diffs: list[float], n_perm: int = 10000, seed: int = 0) -> dict:
    """Paired permutation test: randomly flip the sign of each pair difference."""
    d = np.asarray([x for x in diffs if x is not None and np.isfinite(x)], dtype=float)
    if len(d) < 3:
        return {"n": int(len(d)), "mean_diff": None, "p": None}
    rng = np.random.default_rng(seed)
    observed = d.mean()
    flips = rng.choice([-1.0, 1.0], size=(n_perm, len(d)))
    null = (flips * d).mean(axis=1)
    p = (np.sum(np.abs(null) >= abs(observed)) + 1) / (n_perm + 1)
    return {"n": int(len(d)), "mean_diff": float(observed), "p": float(p)}


def holm(pvalues: dict[str, float | None]) -> dict[str, float | None]:
    """Holm-Bonferroni adjusted p-values (None stays None)."""
    items = sorted(((k, p) for k, p in pvalues.items() if p is not None), key=lambda kv: kv[1])
    m = len(items)
    out: dict[str, float | None] = {k: None for k in pvalues}
    running = 0.0
    for i, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[k] = running
    return out


def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    from scipy import stats

    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 5 or np.std(a[ok]) == 0 or np.std(b[ok]) == 0:
        return None
    return float(stats.spearmanr(a[ok], b[ok]).statistic)


def circular_shift_null(
    a: np.ndarray, b: np.ndarray, n_perm: int, rng: np.random.Generator, min_shift: int = 5
) -> np.ndarray:
    """Spearman rho of b against circularly shifted a (keeps each curve's autocorrelation)."""
    n = len(a)
    if n <= 2 * min_shift:
        return np.array([])
    shifts = rng.integers(min_shift, n - min_shift, size=n_perm)
    out = []
    for s in shifts:
        r = spearman(np.roll(a, s), b)
        if r is not None:
            out.append(r)
    return np.asarray(out)


def resample_curve(t: np.ndarray, y: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Average y within each grid bin (bins centred on grid points); empty bins interpolate."""
    t, y = np.asarray(t, float), np.asarray(y, float)
    ok = np.isfinite(t) & np.isfinite(y)
    t, y = t[ok], y[ok]
    if len(t) < 2:
        return np.full(len(grid), np.nan)
    step = grid[1] - grid[0] if len(grid) > 1 else 1.0
    out = np.full(len(grid), np.nan)
    for i, g in enumerate(grid):
        m = (t >= g - step / 2) & (t < g + step / 2)
        if m.any():
            out[i] = y[m].mean()
    missing = ~np.isfinite(out)
    if missing.any() and (~missing).sum() >= 2:
        out[missing] = np.interp(grid[missing], grid[~missing], out[~missing])
    return out


def zscore(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, float)
    sd = np.nanstd(x)
    return (x - np.nanmean(x)) / sd if sd else x * 0

"""Does TribeV2 predict long-form virality?"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np

from .base import Criteria, Member, holm, sign_flip_test, wilcoxon_paired

SLUG = "tribe-virality"
TITLE = "Does TribeV2 predict long-form virality?"
SUMMARY = (
    "Within each of Evan's channels, pair a hit (outlier score at least 2x) with a flop (at most 0.5x) uploaded "
    "within 30 days of it, and test whether TRIBE's predicted brain response separates hits from flops better "
    "than cheap video/audio features and the pre-brain embeddings TRIBE is built on."
)

PARAMS = {
    "channels": "is_mine",  # Evan's channels
    "formats": ["long"],
    "min_duration_s": 180,
    "min_hit_ratio": 2.0,
    "max_flop_ratio": 0.5,
    "pair_gap_days": 30,
    "pairs_per_channel": 2,
    "target_pairs": [30, 50],
    "permutations": 10000,
}

REQUIRES = [
    "media@1",
    "tribe_video@1",
    "tribe_title@1",
    "tribe_engagement@1",
    "visual_features@1",
    "audio_features@1",
    "resmem@1",
]

NETWORKS = ["visual", "auditory", "language", "default_mode", "attention"]

# Draft measures. They stay UNLOCKED until Evan approves them.
MEASURES = [
    *[
        {
            "name": f"net_{n}_mean",
            "source": "tribe_video",
            "role": "primary",
            "definition": f"mean of the {n} network curve over the whole video",
        }
        for n in NETWORKS
    ],
    *[
        {
            "name": f"net_{n}_first30",
            "source": "tribe_video",
            "role": "primary",
            "definition": f"mean of the {n} network curve over the first 30 s (the hook)",
        }
        for n in NETWORKS
    ],
    {
        "name": "engagement_index_mean",
        "source": "tribe_engagement",
        "role": "primary",
        "definition": "mean engagement index (attention minus default mode, global signal removed)",
    },
    {
        "name": "engagement_index_first30",
        "source": "tribe_engagement",
        "role": "primary",
        "definition": "engagement index over the first 30 s",
    },
    {
        "name": "stimulus_drive_mean",
        "source": "tribe_engagement",
        "role": "primary",
        "definition": "mean whole-cortex response magnitude",
    },
    *[
        {
            "name": f"title_net_{n}_mean",
            "source": "tribe_title",
            "role": "side",
            "definition": f"title read aloud: mean {n} network response",
        }
        for n in NETWORKS
    ],
    {
        "name": "thumbnail_memorability",
        "source": "resmem",
        "role": "side",
        "definition": "ResMem score of the thumbnail",
    },
]

CHEAP_FEATURES = [
    ("visual_features", "cuts_per_minute"),
    ("visual_features", "mean_motion"),
    ("visual_features", "mean_colorfulness"),
    ("visual_features", "mean_brightness"),
    ("visual_features", "mean_faces"),
    ("audio_features", "integrated_lufs"),
    ("audio_features", "speech_rate_wpm"),
    ("audio_features", "mean_music_score"),
    ("visual_features", "duration_s"),
]

METHODOLOGY = """
## Question

Does TRIBE v2's predicted cortical response to a video separate a channel's hits from its
flops, beyond what cheap video/audio features and TRIBE's own pre-brain embeddings do?

## Sample (criteria)

- Channels: Evan's channels (marked "my channel").
- Videos: long-form only, no live streams or premieres, at least 3 minutes, public stats.
- Outlier score: views divided by the median of the channel's 10 nearest long-form uploads at
  equal age (Shorts have their own baseline and are excluded here).
- Pairs, within a channel: the highest-scoring hit (score >= 2.0) matched to the worst unused
  flop (score <= 0.5) uploaded within 30 days of it. Up to 2 pairs per channel, aiming for
  30-50 pairs in total.

## Measures (draft, pending Evan's approval)

Primary: per-network means over the whole video and the first 30 s (visual, auditory,
language, default mode, attention), the engagement index (mean and first 30 s) and the
stimulus drive. Side analyses: the title read aloud through TRIBE, and the thumbnail's
ResMem memorability.

## Tests

1. For each primary measure, the hit-minus-flop difference across pairs: Wilcoxon
   signed-rank test, plus a sign-flip permutation test (10,000 permutations). Holm
   correction across primary measures.
2. Leave-one-pair-out classification (logistic regression on pair differences, symmetrized):
   brain measures versus cheap features (cuts per minute, motion, colorfulness, brightness,
   faces, loudness, speech rate, music, length) versus pre-brain embeddings (pooled text,
   audio and video features, PCA to 10 dimensions). The brain model has to beat both, with a
   bootstrap confidence interval over pairs for the AUC difference.
3. Side analyses use the same paired tests.
"""


# ------------------------------------------------------------------ criteria


def _pairs_for_channel(videos: list[dict], params: dict) -> list[tuple[dict, dict, int]]:
    ranked = sorted([v for v in videos if v["outlier_score"] is not None], key=lambda v: -v["outlier_score"])
    used: set[int] = set()
    pairs = []
    for hit in ranked:
        if len(pairs) >= params["pairs_per_channel"]:
            break
        if hit["outlier_score"] < params["min_hit_ratio"]:
            break
        if hit["id"] in used:
            continue
        hit_day = dt.datetime.fromisoformat(hit["published_at"].rstrip("Z"))
        flops = [
            v
            for v in ranked
            if v["id"] not in used
            and v["id"] != hit["id"]
            and v["outlier_score"] <= params["max_flop_ratio"]
            and abs((dt.datetime.fromisoformat(v["published_at"].rstrip("Z")) - hit_day).days)
            <= params["pair_gap_days"]
        ]
        if not flops:
            continue
        flop = flops[-1]  # the worst
        gap = abs((dt.datetime.fromisoformat(flop["published_at"].rstrip("Z")) - hit_day).days)
        used |= {hit["id"], flop["id"]}
        pairs.append((hit, flop, gap))
    return pairs


def criteria(store, params: dict, channel_id: int | None = None) -> Criteria:
    channels = store.channels(is_mine=True) if params["channels"] == "is_mine" else store.channels()
    if channel_id is not None:
        channels = [c for c in channels if c["id"] == channel_id]
    members: list[Member] = []
    per_channel = {}
    for ch in channels:
        videos = [
            v
            for v in store.videos(
                channel_ids=[ch["id"]], formats=params["formats"], min_duration_s=params["min_duration_s"]
            )
            if v["published_at"]
        ]
        pairs = _pairs_for_channel(videos, params)
        per_channel[ch["title"]] = len(pairs)
        for k, (hit, flop, gap) in enumerate(pairs, 1):
            group = f"{ch['id']}-pair{k}"
            members.append(
                Member(hit["id"], "hit", group, f"{hit['outlier_score']:.2f}x of baseline", {"gap_days": gap})
            )
            members.append(
                Member(
                    flop["id"],
                    "flop",
                    group,
                    f"{flop['outlier_score']:.2f}x of baseline, {gap} days apart",
                    {"gap_days": gap},
                )
            )
    n_pairs = len(members) // 2
    lo, hi = params["target_pairs"]
    rules = (
        f"{n_pairs} pairs from {len(channels)} channel(s) "
        f"(target {lo}-{hi}): long-form uploads of at least {params['min_duration_s'] // 60} min, no live or "
        f"premieres; in each channel the best hits at >= {params['min_hit_ratio']}x their outlier baseline, each "
        f"matched to the worst flop at <= {params['max_flop_ratio']}x uploaded within {params['pair_gap_days']} "
        f"days; up to {params['pairs_per_channel']} pairs per channel."
    )
    return Criteria(members, rules, {"pairs": n_pairs, "per_channel": per_channel})


# ------------------------------------------------------------------ measures


def _network_means(path: Path, store, n_first: int | None = None) -> dict[str, float | None]:
    doc = store.load_json(path / "networks.json") if path else None
    out = {}
    for n in NETWORKS:
        vals = np.array([np.nan if x is None else x for x in (doc or {}).get("networks", {}).get(n, [])], float)
        vals = vals[:n_first] if n_first else vals
        out[n] = float(np.nanmean(vals)) if vals.size and np.isfinite(vals).any() else None
    return out


def measure_row(store, video_id: int) -> dict:
    row: dict = {"video_id": video_id}
    tribe = store.result(video_id, "tribe_video")
    if tribe and tribe["status"] == "done":
        for n, v in _network_means(tribe["path"], store).items():
            row[f"net_{n}_mean"] = v
        for n, v in _network_means(tribe["path"], store, 30).items():
            row[f"net_{n}_first30"] = v
        emb = tribe["path"] / "embeddings.npz"
        if emb.exists():
            with np.load(emb) as z:
                row["_prebrain"] = np.concatenate([np.asarray(z[k], float).ravel() for k in sorted(z.files)])
    eng = store.result(video_id, "tribe_engagement")
    if eng and eng["status"] == "done":
        s = eng["summary"]
        row["engagement_index_mean"] = s.get("mean_engagement_index")
        row["engagement_index_first30"] = s.get("mean_engagement_index_first_30s")
        row["stimulus_drive_mean"] = s.get("mean_stimulus_drive")
    title = store.result(video_id, "tribe_title")
    if title and title["status"] == "done":
        for n, v in _network_means(title["path"], store).items():
            row[f"title_net_{n}_mean"] = v
    res = store.result(video_id, "resmem")
    if res and res["status"] == "done":
        row["thumbnail_memorability"] = res["summary"].get("memorability")
    for analyzer, key in CHEAP_FEATURES:
        r = store.result(video_id, analyzer)
        row[f"cheap_{key}"] = r["summary"].get(key) if r and r["status"] == "done" else None
    return row


# ------------------------------------------------------------------ analysis


def _lopo_auc(X: np.ndarray, groups: np.ndarray) -> dict:
    """Leave-one-pair-out logistic regression on symmetrized pair differences."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    n_pairs = X.shape[0]
    if n_pairs < 6 or X.shape[1] == 0:
        return {"auc": None, "accuracy": None, "n_pairs": n_pairs, "scores": None}
    Xs = np.vstack([X, -X])
    y = np.concatenate([np.ones(n_pairs), np.zeros(n_pairs)])
    pair = np.concatenate([np.arange(n_pairs), np.arange(n_pairs)])
    scores = np.zeros(len(y))
    for p in range(n_pairs):
        test = pair == p
        model = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=1000))
        model.fit(Xs[~test], y[~test])
        scores[test] = model.decision_function(Xs[test])
    return {
        "auc": float(roc_auc_score(y, scores)),
        "accuracy": float(np.mean((scores > 0) == (y == 1))),
        "n_pairs": n_pairs,
        "scores": scores.tolist(),
        "labels": y.tolist(),
    }


def _bootstrap_auc_diff(a: dict, b: dict, n_boot: int = 2000, seed: int = 0) -> dict:
    from sklearn.metrics import roc_auc_score

    if a["scores"] is None or b["scores"] is None:
        return {"diff": None, "ci95": None}
    sa, sb, y = np.array(a["scores"]), np.array(b["scores"]), np.array(a["labels"])
    n = len(y) // 2
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(n_boot):
        pairs = rng.integers(0, n, n)
        idx = np.concatenate([pairs, pairs + n])
        if len(set(y[idx])) < 2:
            continue
        diffs.append(roc_auc_score(y[idx], sa[idx]) - roc_auc_score(y[idx], sb[idx]))
    if not diffs:
        return {"diff": None, "ci95": None}
    return {
        "diff": float(a["auc"] - b["auc"]),
        "ci95": [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))],
    }


def analyze(store, members: list[dict], params: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {m["video_id"]: measure_row(store, m["video_id"]) for m in members}
    pairs: dict[str, dict] = {}
    for m in members:
        pairs.setdefault(m["group"], {})[m["role"]] = rows[m["video_id"]]
    complete = [p for p in pairs.values() if "hit" in p and "flop" in p]

    def diffs(name: str) -> list[float]:
        return [
            p["hit"][name] - p["flop"][name]
            for p in complete
            if p["hit"].get(name) is not None and p["flop"].get(name) is not None
        ]

    tests = {}
    for meas in MEASURES:
        d = diffs(meas["name"])
        tests[meas["name"]] = {
            "role": meas["role"],
            "wilcoxon": wilcoxon_paired(d),
            "sign_flip": sign_flip_test(d, params.get("permutations", 10000)),
        }
    primary = {k: v["wilcoxon"]["p"] for k, v in tests.items() if v["role"] == "primary"}
    for k, p in holm(primary).items():
        tests[k]["wilcoxon"]["p_holm"] = p

    # Model comparison on pairs that have every feature in each set.
    brain_names = [m["name"] for m in MEASURES if m["role"] == "primary"]
    cheap_names = [f"cheap_{k}" for _a, k in CHEAP_FEATURES]

    def matrix(names: list[str]) -> np.ndarray:
        X = [
            [p["hit"].get(n) - p["flop"].get(n) for n in names]
            for p in complete
            if all(p["hit"].get(n) is not None and p["flop"].get(n) is not None for n in names)
        ]
        return np.asarray(X, float).reshape(-1, len(names))

    def prebrain() -> np.ndarray:
        ok = [p for p in complete if "_prebrain" in p["hit"] and "_prebrain" in p["flop"]]
        if len(ok) < 6:
            return np.zeros((0, 0))
        from sklearn.decomposition import PCA

        both = np.vstack([np.vstack([p["hit"]["_prebrain"], p["flop"]["_prebrain"]]) for p in ok])
        k = min(10, both.shape[0] - 1, both.shape[1])
        z = PCA(n_components=k, random_state=0).fit_transform(both)
        return z[0::2] - z[1::2]

    models = {
        "brain": _lopo_auc(matrix(brain_names), None),
        "cheap": _lopo_auc(matrix(cheap_names), None),
        "prebrain": _lopo_auc(prebrain(), None),
    }
    comparison = {
        "brain_vs_cheap": _bootstrap_auc_diff(models["brain"], models["cheap"]),
        "brain_vs_prebrain": _bootstrap_auc_diff(models["brain"], models["prebrain"]),
    }
    figures = _figures(tests, complete, out_dir)
    table = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows.values()]
    (out_dir / "measures.json").write_text(json.dumps(table, indent=1, default=float), encoding="utf-8")
    results = {
        "pairs_total": len(pairs),
        "pairs_complete": len(complete),
        "tests": tests,
        "models": {k: {kk: vv for kk, vv in v.items() if kk not in ("scores", "labels")} for k, v in models.items()},
        "comparison": comparison,
        "brain_beats_controls": bool(
            comparison["brain_vs_cheap"]["ci95"]
            and comparison["brain_vs_cheap"]["ci95"][0] > 0
            and comparison["brain_vs_prebrain"]["ci95"]
            and comparison["brain_vs_prebrain"]["ci95"][0] > 0
        ),
    }
    return {"results": results, "figures": figures}


def _figures(tests: dict, complete: list[dict], out_dir: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ranked = sorted(
        (k for k, v in tests.items() if v["role"] == "primary" and v["wilcoxon"]["p"] is not None),
        key=lambda k: tests[k]["wilcoxon"]["p"],
    )[:4]
    if not ranked:
        return []
    fig, axes = plt.subplots(1, len(ranked), figsize=(3.2 * len(ranked), 3.2), squeeze=False)
    for ax, name in zip(axes[0], ranked, strict=False):
        for p in complete:
            h, f = p["hit"].get(name), p["flop"].get(name)
            if h is not None and f is not None:
                ax.plot([0, 1], [f, h], color="0.6", lw=0.8)
                ax.scatter([0, 1], [f, h], c=["#dc2626", "#16a34a"], s=12, zorder=3)
        ax.set_xticks([0, 1], ["flop", "hit"])
        ax.set_title(f"{name}\np={tests[name]['wilcoxon']['p']:.3g}", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_dir / "paired_measures.png", dpi=120)
    plt.close(fig)
    return ["paired_measures.png"]

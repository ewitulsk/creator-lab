"""Does TRIBE predict real audience retention?"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .base import Criteria, Member, circular_shift_null, resample_curve, spearman

SLUG = "tribe-retention"
TITLE = "Does TRIBE predict real audience retention?"
SUMMARY = (
    "Within each of Evan's own videos, does TRIBE's predicted engagement curve track where real viewers drop "
    "off or rewatch, beyond the generic retention decay and beyond cheap features (cuts, loudness, speech rate)? "
    "Other creators' videos use YouTube's public 'Most replayed' heatmap as the target, and owned videos check "
    "how good a proxy that heatmap is."
)

PARAMS = {
    "formats": ["long"],
    "min_duration_s": 180,
    "edge_trim": 0.05,  # drop the first and last 5% (intro cliff, end screen)
    "grid_step": 0.01,
    "lags_s": [0, 2, 5],
    "permutations": 1000,
    "min_shift_bins": 5,
    "include_public_heatmap": True,
}

REQUIRES = ["media@1", "tribe_video@1", "tribe_engagement@1", "visual_features@1", "audio_features@1", "heatmap@1"]

PREDICTORS = ["engagement_index", "stimulus_drive", "anterior_insula", "d_engagement_index"]
YEO = ["visual", "somatomotor", "dorsal_attention", "ventral_attention", "limbic", "frontoparietal", "default"]
CHEAP = ["cuts", "loudness", "speech_rate"]
TARGETS = ["dropoff", "residual", "relative"]

# Draft measure definitions. UNLOCKED until Evan approves them.
MEASURES = [
    {"name": "dropoff", "kind": "target", "definition": "change in audienceWatchRatio per 1% bin, negated (drop-off)"},
    {
        "name": "residual",
        "kind": "target",
        "definition": "audienceWatchRatio minus a smooth decay fit (a*exp(-b*x)+c) per video",
    },
    {"name": "relative", "kind": "target", "definition": "relativeRetentionPerformance per 1% bin"},
    {"name": "heatmap", "kind": "target", "definition": "public 'Most replayed' value per 1% bin (other creators)"},
    {"name": "engagement_index", "kind": "predictor", "definition": "tribe_engagement: attention minus default mode"},
    {"name": "d_engagement_index", "kind": "predictor", "definition": "first difference of the engagement index"},
    {"name": "stimulus_drive", "kind": "predictor", "definition": "tribe_engagement: whole-cortex magnitude"},
    {
        "name": "anterior_insula",
        "kind": "predictor",
        "definition": "tribe_engagement: anterior insula (expected sign: negative for retention, Tong et al. 2020)",
    },
    {"name": "lags_s", "kind": "lag", "definition": "predictor at t - lag for lag in 0, 2, 5 s"},
    {"name": "cuts", "kind": "control", "definition": "shot cuts per second (visual_features)"},
    {"name": "loudness", "kind": "control", "definition": "momentary loudness, LUFS (audio_features)"},
    {"name": "speech_rate", "kind": "control", "definition": "caption words per second"},
]

METHODOLOGY = """
## Question

Inside a video, does TRIBE's predicted engagement curve line up with where real viewers stay,
leave or rewatch, beyond the generic retention decay every video has?

## Samples (criteria)

- **Owned**: Evan's own long-form videos (at least 3 minutes, not live) with owner retention
  data from "Sync data from YT". Targets: real retention.
- **Public**: other creators' long-form videos with YouTube's public "Most replayed" heatmap.
  Target: the heatmap.

## Curves (draft, pending Evan's approval)

- Grid: elapsed-ratio bins of 1%, excluding the first and last 5% (intro cliff, end screen).
  Brain curves in seconds are converted with the video's length (ratio = t / duration).
- Targets (owned): drop-off per bin (negated change in audienceWatchRatio), residual retention
  (audienceWatchRatio minus a per-video smooth decay fit), and relativeRetentionPerformance.
- Target (public): the heatmap value per bin.
- Predictors: engagement index, its first difference, stimulus drive, anterior insula, Yeo-7
  network curves; lags of 0, 2 and 5 s (predictor at t - lag).
- Controls: shot cuts, loudness and speech rate over time.

## Tests

1. Per video, Spearman correlation of each predictor with each target. Null: the same
   correlation after circularly shifting the predictor (1,000 shifts of at least 5 bins),
   which keeps each curve's autocorrelation.
2. Across videos: the mean correlation against the null of mean correlations, and a sign test.
3. Leave-one-video-out ridge regression predicting residual retention from brain curves,
   compared with the same model on the cheap controls. Brain curves must beat the controls.
4. Expected direction: the anterior insula correlates negatively with retention (Tong et al. 2020).
5. Proxy check: on owned videos that have both, how well the public heatmap tracks real
   retention (per-video Spearman, aggregated). This tells us how far the public-heatmap
   results can be trusted.
"""


# ------------------------------------------------------------------ criteria


def criteria(store, params: dict, channel_id: int | None = None) -> Criteria:
    members: list[Member] = []
    owned = store.channels(owned=True)
    owned_ids = {c["id"] for c in owned}
    channels = owned
    if params.get("include_public_heatmap"):
        channels = store.channels()
    if channel_id is not None:
        channels = [c for c in channels if c["id"] == channel_id]
    n_owned = n_public = n_waiting = 0
    for ch in channels:
        is_owned = ch["id"] in owned_ids
        for v in store.videos(
            channel_ids=[ch["id"]], formats=params["formats"], min_duration_s=params["min_duration_s"]
        ):
            if is_owned:
                if store.retention(v["id"]):
                    members.append(Member(v["id"], "owned", ch["title"], "owner retention curve available"))
                    n_owned += 1
                elif channel_id is not None:
                    members.append(Member(v["id"], "owned", ch["title"], "needs 'Sync data from YT'"))
                    n_waiting += 1
            elif store.heatmap(v["id"]):
                members.append(Member(v["id"], "public", ch["title"], "public heatmap available"))
                n_public += 1
            elif channel_id is not None:
                members.append(Member(v["id"], "public", ch["title"], "needs a heatmap capture"))
                n_waiting += 1
    rules = (
        f"{n_owned} owned videos with real retention and {n_public} public videos with a 'Most replayed' heatmap; "
        f"long-form, at least {params['min_duration_s'] // 60} min, no live streams."
        + (
            f" {n_waiting} more video{'s' if n_waiting != 1 else ''} in this channel"
            f" need{'s' if n_waiting == 1 else ''} data first (owner sync or a heatmap capture)."
            if channel_id is not None and n_waiting
            else ""
        )
    )
    return Criteria(members, rules, {"owned": n_owned, "public": n_public})


# ------------------------------------------------------------------ curves


def grid(params: dict) -> np.ndarray:
    step = params["grid_step"]
    lo, hi = params["edge_trim"] + step, 1 - params["edge_trim"]
    return np.round(np.arange(lo, hi + 1e-9, step), 4)


def decay_residual(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """y minus a smooth decay fit (a*exp(-b*x)+c), falling back to a cubic."""
    from scipy.optimize import curve_fit

    ok = np.isfinite(y)
    try:
        popt, _ = curve_fit(lambda x, a, b, c: a * np.exp(-b * x) + c, x[ok], y[ok], p0=(0.5, 2.0, 0.3), maxfev=5000)
        fit = popt[0] * np.exp(-popt[1] * x) + popt[2]
    except Exception:
        fit = np.polyval(np.polyfit(x[ok], y[ok], 3), x)
    return y - fit


def targets_for(store, video: dict, g: np.ndarray) -> dict[str, np.ndarray]:
    out = {}
    pts = store.retention(video["id"])
    if pts:
        r = np.array([p["ratio"] for p in pts], float)
        watch = np.array([np.nan if p["watch_ratio"] is None else p["watch_ratio"] for p in pts], float)
        rel = np.array([np.nan if p["relative"] is None else p["relative"] for p in pts], float)
        w = np.interp(g, r, watch)
        prev = np.interp(g - (g[1] - g[0]), r, watch)
        out["watch"] = w
        out["dropoff"] = prev - w
        out["residual"] = decay_residual(g, w)
        out["relative"] = np.interp(g, r, rel)
    hm = store.heatmap(video["id"])
    if hm and video.get("duration_s"):
        mid = np.array([(p["start_s"] + p["end_s"]) / 2 for p in hm]) / video["duration_s"]
        out["heatmap"] = np.interp(g, mid, np.array([p["value"] for p in hm], float))
    return out


def _file(result: dict | None) -> Path | None:
    if not result or result["status"] != "done" or not result["path"]:
        return None
    return result["path"]


def predictors_for(store, video: dict, g: np.ndarray, lag: float) -> dict[str, np.ndarray]:
    dur = video.get("duration_s") or 0
    out: dict[str, np.ndarray] = {}
    if not dur:
        return out
    path = _file(store.result(video["id"], "tribe_engagement"))
    if path is not None:
        doc = store.load_json(path if path.suffix == ".json" else path / "engagement.json")
        t = np.asarray(doc["t"], float)
        curves = {**doc.get("curves", {}), **{f"yeo_{k}": v for k, v in (doc.get("yeo7") or {}).items()}}
        for name, vals in curves.items():
            if vals is None:
                continue
            y = np.array([np.nan if v is None else v for v in vals], float)
            out[name] = resample_curve((t + lag) / dur, y, g)  # value at t drives the bin at t + lag
        if "engagement_index" in out:
            out["d_engagement_index"] = np.gradient(out["engagement_index"])
    vis = _file(store.result(video["id"], "visual_features"))
    if vis is not None:
        s = store.load_json(vis)["series"]
        out["cuts"] = resample_curve((np.asarray(s["t"], float) + lag) / dur, np.asarray(s["cut"], float), g)
    aud = _file(store.result(video["id"], "audio_features"))
    if aud is not None:
        s = store.load_json(aud)["series"]
        loud = np.array([np.nan if v is None else v for v in s["loudness_lufs"]], float)
        out["loudness"] = resample_curve((np.asarray(s["t"], float) + lag) / dur, loud, g)
    cues = store.captions_cues(video["id"])
    if cues:
        per_sec = np.zeros(int(np.ceil(dur)) + 1)
        for c in cues:
            words = len(str(c.get("text", "")).split())
            s0, s1 = int(c["start"]), max(int(c["start"]) + 1, int(np.ceil(c["end"])))
            per_sec[s0:s1] += words / max(1, s1 - s0)
        out["speech_rate"] = resample_curve((np.arange(len(per_sec)) + lag) / dur, per_sec, g)
    return out


# ------------------------------------------------------------------ analysis


def _aggregate(per_video: list[dict], n_perm: int) -> dict:
    rhos = np.array([v["rho"] for v in per_video], float)
    if len(rhos) == 0:
        return {"n_videos": 0, "mean_rho": None, "p_perm": None, "p_sign": None}
    from scipy import stats

    nulls = [np.asarray(v["null"]) for v in per_video if len(v["null"])]
    p_perm = None
    if nulls and len(nulls) == len(per_video):
        k = min(len(n) for n in nulls)
        null_means = np.mean([n[:k] for n in nulls], axis=0)
        p_perm = float((np.sum(np.abs(null_means) >= abs(rhos.mean())) + 1) / (k + 1))
    pos = int(np.sum(rhos > 0))
    p_sign = float(stats.binomtest(pos, len(rhos), 0.5).pvalue) if len(rhos) else None
    return {"n_videos": len(rhos), "mean_rho": float(rhos.mean()), "positive": pos, "p_perm": p_perm, "p_sign": p_sign}


def _correlations(videos, curves, pred_names, target_names, params, rng) -> dict:
    out: dict = {}
    for lag in params["lags_s"]:
        for p in pred_names:
            for t in target_names:
                per = []
                for v in videos:
                    tg = curves[v["id"]]["targets"].get(t)
                    pr = curves[v["id"]]["predictors"][lag].get(p)
                    if tg is None or pr is None:
                        continue
                    rho = spearman(pr, tg)
                    if rho is None:
                        continue
                    null = circular_shift_null(pr, tg, params["permutations"], rng, params["min_shift_bins"])
                    per.append({"video_id": v["id"], "rho": rho, "null": null})
                agg = _aggregate(per, params["permutations"])
                agg["per_video"] = {str(x["video_id"]): round(x["rho"], 4) for x in per}
                out[f"{p}|{t}|lag{lag}"] = agg
    return out


def _lovo_ridge(videos, curves, feature_names, target, params) -> dict:
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    usable = [v for v in videos if curves[v["id"]]["targets"].get(target) is not None]
    # Use the requested features every usable video has (e.g. Yeo curves may be missing).
    feature_names = [f for f in feature_names if usable and all(f in curves[v["id"]]["predictors"][0] for v in usable)]
    if not feature_names:
        return {"n_videos": 0, "mean_heldout_rho": None, "features": []}
    rows = []
    for v in usable:
        tg = curves[v["id"]]["targets"][target]
        preds = curves[v["id"]]["predictors"][0]
        X = np.column_stack([preds[f] for f in feature_names])
        ok = np.isfinite(X).all(axis=1) & np.isfinite(tg)
        if ok.sum() >= 10:
            rows.append((v["id"], X[ok], tg[ok]))
    if len(rows) < 3:
        return {"n_videos": len(rows), "mean_heldout_rho": None}
    scores = []
    for i, (_vid, Xi, yi) in enumerate(rows):
        Xtr = np.vstack([r[1] for j, r in enumerate(rows) if j != i])
        ytr = np.concatenate([r[2] for j, r in enumerate(rows) if j != i])
        model = make_pipeline(StandardScaler(), Ridge(alpha=1.0)).fit(Xtr, ytr)
        rho = spearman(model.predict(Xi), yi)
        if rho is not None:
            scores.append(rho)
    return {
        "n_videos": len(rows),
        "mean_heldout_rho": float(np.mean(scores)) if scores else None,
        "features": feature_names,
    }


def analyze(store, members: list[dict], params: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    g = grid(params)
    by_id = {v["id"]: v for v in store.videos(video_ids=[m["video_id"] for m in members])}
    curves: dict[int, dict] = {}
    for m in members:
        v = by_id.get(m["video_id"])
        if v is None:
            continue
        curves[v["id"]] = {
            "role": m["role"],
            "targets": targets_for(store, v, g),
            "predictors": {lag: predictors_for(store, v, g, lag) for lag in params["lags_s"]},
        }
    owned = [by_id[i] for i, c in curves.items() if c["role"] == "owned" and "watch" in c["targets"]]
    public = [by_id[i] for i, c in curves.items() if c["role"] == "public" and "heatmap" in c["targets"]]

    brain = PREDICTORS + [f"yeo_{y}" for y in YEO]
    results = {
        "grid": {"from": float(g[0]), "to": float(g[-1]), "step": params["grid_step"]},
        "n_owned": len(owned),
        "n_public": len(public),
        "owned": {
            "brain": _correlations(owned, curves, brain, TARGETS, params, rng),
            "controls": _correlations(owned, curves, CHEAP, TARGETS, params, rng),
            "ridge_brain": _lovo_ridge(
                owned,
                curves,
                ["engagement_index", "stimulus_drive", "anterior_insula"] + [f"yeo_{y}" for y in YEO],
                "residual",
                params,
            ),
            "ridge_controls": _lovo_ridge(owned, curves, CHEAP, "residual", params),
        },
        "public_heatmap": {
            "brain": _correlations(public, curves, brain, ["heatmap"], params, rng),
            "controls": _correlations(public, curves, CHEAP, ["heatmap"], params, rng),
        },
    }
    # Proxy check: the public heatmap against real retention on owned videos that have both.
    both = [v for v in owned if "heatmap" in curves[v["id"]]["targets"]]
    proxy = {}
    for target in ("residual", "relative", "watch"):
        per = []
        for v in both:
            rho = spearman(curves[v["id"]]["targets"]["heatmap"], curves[v["id"]]["targets"][target])
            if rho is not None:
                per.append(
                    {
                        "video_id": v["id"],
                        "rho": rho,
                        "null": circular_shift_null(
                            curves[v["id"]]["targets"]["heatmap"],
                            curves[v["id"]]["targets"][target],
                            params["permutations"],
                            rng,
                            params["min_shift_bins"],
                        ),
                    }
                )
        proxy[target] = _aggregate(per, params["permutations"]) | {
            "per_video": {str(x["video_id"]): round(x["rho"], 4) for x in per}
        }
    results["heatmap_vs_real_retention"] = proxy
    ains = results["owned"]["brain"].get("anterior_insula|residual|lag0", {})
    results["anterior_insula_direction"] = {
        "expected": "negative",
        "observed_mean_rho": ains.get("mean_rho"),
        "as_expected": None if ains.get("mean_rho") is None else ains["mean_rho"] < 0,
    }
    rb, rc = results["owned"]["ridge_brain"], results["owned"]["ridge_controls"]
    results["brain_beats_controls"] = (
        None
        if rb["mean_heldout_rho"] is None or rc["mean_heldout_rho"] is None
        else rb["mean_heldout_rho"] > rc["mean_heldout_rho"]
    )
    figures = _figures(results, owned, curves, g, out_dir)
    (out_dir / "curves_grid.json").write_text(
        json.dumps({"grid": g.tolist(), "videos": [v["id"] for v in owned + public]}), encoding="utf-8"
    )
    return {"results": results, "figures": figures}


def _figures(results, owned, curves, g, out_dir: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figs = []
    key = "engagement_index|residual|lag0"
    per = results["owned"]["brain"].get(key, {}).get("per_video") or {}
    if per:
        fig, ax = plt.subplots(figsize=(4.5, 3))
        ax.hist(list(per.values()), bins=15, color="#4f46e5")
        ax.axvline(0, color="0.3", lw=1)
        ax.set_xlabel("Spearman rho per video")
        ax.set_title("engagement index vs residual retention", fontsize=9)
        fig.tight_layout()
        fig.savefig(out_dir / "rho_per_video.png", dpi=120)
        plt.close(fig)
        figs.append("rho_per_video.png")
    if owned:
        v = owned[0]
        c = curves[v["id"]]
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.plot(g, c["targets"]["residual"], label="residual retention", color="#16a34a")
        eng = c["predictors"][0].get("engagement_index")
        if eng is not None:
            z = (eng - np.nanmean(eng)) / (np.nanstd(eng) or 1) * np.nanstd(c["targets"]["residual"])
            ax.plot(g, z, label="TRIBE engagement (scaled)", color="#ef4444")
        if "heatmap" in c["targets"]:
            h = c["targets"]["heatmap"]
            ax.plot(
                g,
                (h - np.nanmean(h)) / (np.nanstd(h) or 1) * np.nanstd(c["targets"]["residual"]),
                label="public heatmap (scaled)",
                color="#f97316",
                lw=0.8,
            )
        ax.set_xlabel("fraction of video")
        ax.legend(fontsize=7)
        ax.set_title(v["title"][:60], fontsize=8)
        fig.tight_layout()
        fig.savefig(out_dir / "example_overlay.png", dpi=120)
        plt.close(fig)
        figs.append("example_overlay.png")
    return figs

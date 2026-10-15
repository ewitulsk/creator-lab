"""Write everything a TRIBE run produced, with timestamps.

TRIBE predicts in 100-TR windows (TR = 1 s): one row per TR of each window. Its
predict() drops rows without any event (e.g. past the end of the input). Here every row
is kept in raw/ (with a `kept` flag in rows.parquet), and preds.npy is the convenience
view: one row per second of the input, built from the kept rows by start time
(duplicates averaged, rows past the end dropped, seconds with no row NaN).

Run folder:
    preds.npy            float16 (seconds, 20484): the main output
    timing.json          start second of each preds.npy row and alignment stats
    rows.parquet         one row per model output row: times, window, kept, modalities
    raw/preds_all_rows.npy  float16 (all rows, 20484), dropped rows included
    events.parquet       the events dataframe TRIBE built
    words.parquet        WhisperX words with start/end times
    transcript.txt       the transcript
    features/<mod>.npy   float16 (samples, layers, dims) pre-brain features over time
    features/<mod>_times.npy  float32 sample centre times (s)
    features/pooled.npz  time-averaged features within the input (per modality)
    regions.parquet      network and HCP-MMP1 area means per second
    embeddings.npz       same as features/pooled.npz (kept for the analyzers)
    meta.json            config, versions, hashes, hardware, dtypes, stage timings
    manifest.json        every file with size
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

HRF_OFFSET_S = 5.0  # TRIBE's fMRI targets were shifted 5 s (hemodynamic lag) in training


@dataclass
class Predicted:
    rows: list = field(default_factory=list)  # (b*t, 20484) float32 chunks, every row
    table: list = field(default_factory=list)  # one dict per row
    features: dict = field(default_factory=dict)  # mod -> [(times, values (T, L, D))]
    frequency: dict = field(default_factory=dict)
    tr: float = 1.0

    @property
    def all_rows(self) -> np.ndarray:
        return np.concatenate(self.rows) if self.rows else np.zeros((0, 20484), dtype=np.float32)


def to_grid(preds: np.ndarray, starts: np.ndarray, duration: float, tr: float = 1.0) -> tuple[np.ndarray, dict]:
    n = max(1, int(np.floor(duration / tr + 1e-6)))
    out = np.full((n, preds.shape[1]), np.nan, dtype=np.float32)
    counts = np.zeros(n, dtype=np.int32)
    idx = np.rint(np.asarray(starts) / tr).astype(np.int64)
    beyond = 0
    for row, i in enumerate(idx):
        if i < 0 or i >= n:
            beyond += 1
            continue
        if counts[i] == 0:
            out[i] = preds[row]
        else:
            out[i] += preds[row]
        counts[i] += 1
    filled = counts > 0
    out[filled] /= counts[filled, None]
    stats = {
        "rows_in": int(len(idx)),
        "rows_beyond_end": int(beyond),
        "duplicate_rows_merged": int(counts[counts > 1].sum() - (counts > 1).sum()),
        "seconds_without_prediction": int((~filled).sum()),
    }
    return out, stats


def _events_frame(events):
    """Make the events dataframe parquet-safe (object columns with mixed types become text)."""
    df = events.reset_index(drop=True).copy()
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].map(lambda v: v if v is None or isinstance(v, str) else str(v))
    return df


def write_run(
    out_dir: Path,
    pred: Predicted,
    events,
    duration: float,
    atlas: dict | None,
    meta: dict,
) -> dict:
    import pandas as pd

    out_dir = Path(out_dir)
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)
    (out_dir / "features").mkdir(parents=True, exist_ok=True)

    # Every row the model produced, plus the table describing each one.
    rows = pred.all_rows
    np.save(out_dir / "raw" / "preds_all_rows.npy", rows.astype(np.float16))
    table = pd.DataFrame(pred.table)
    table.to_parquet(out_dir / "rows.parquet", index=False)

    # The per-second view from kept rows.
    kept = table["kept"].to_numpy() if len(table) else np.zeros(0, bool)
    grid, stats = to_grid(rows[kept], table["start_s"].to_numpy()[kept], duration, pred.tr)
    np.save(out_dir / "preds.npy", grid.astype(np.float16))
    times = np.arange(len(grid), dtype=np.float32) * pred.tr
    stats |= {"rows_total": int(len(table)), "rows_kept_by_tribe": int(kept.sum())}
    timing = {
        "tr_s": pred.tr,
        "start_s": times.tolist(),
        "n_timesteps": len(grid),
        "n_vertices": int(grid.shape[1]),
        "mesh": "fsaverage5",
        "vertex_order": "left hemisphere (10242) then right (10242)",
        "input_duration_s": round(float(duration), 3),
        "hrf_offset_s": HRF_OFFSET_S,
        "alignment": "row t = predicted response to the stimulus at t..t+1 s (TRIBE applies the 5 s lag itself)",
        **stats,
    }
    (out_dir / "timing.json").write_text(json.dumps(timing), encoding="utf-8")

    # What TRIBE was fed.
    ev = _events_frame(events)
    ev.to_parquet(out_dir / "events.parquet", index=False)
    words = ev[ev["type"] == "Word"]
    word_cols = [c for c in ("start", "duration", "text", "sentence", "sequence_id") if c in words.columns]
    words = words[word_cols].sort_values("start").reset_index(drop=True)
    if "start" in words and "duration" in words:
        words["end"] = words["start"] + words["duration"]
    words.to_parquet(out_dir / "words.parquet", index=False)
    (out_dir / "transcript.txt").write_text(
        " ".join(str(w).strip() for w in words.get("text", [])) + "\n", encoding="utf-8"
    )

    # Pre-brain features over time, and pooled within the input.
    pooled = {}
    feature_info = {}
    for mod, parts in pred.features.items():
        order_times = np.concatenate([t for t, _ in parts])
        values = np.concatenate([v for _, v in parts])
        order = np.argsort(order_times, kind="stable")
        order_times, values = order_times[order], values[order]
        np.save(out_dir / "features" / f"{mod}.npy", values.astype(np.float16))
        np.save(out_dir / "features" / f"{mod}_times.npy", order_times.astype(np.float32))
        inside = order_times < duration
        if inside.any():
            pooled[mod] = values[inside].mean(axis=0).astype(np.float32)
        feature_info[mod] = {
            "shape": list(values.shape),
            "frequency_hz": pred.frequency.get(mod),
            "samples_inside_input": int(inside.sum()),
        }
    if pooled:
        np.savez_compressed(out_dir / "features" / "pooled.npz", **pooled)
        np.savez_compressed(out_dir / "embeddings.npz", **pooled)

    summary = {"n_timesteps": len(grid), **stats, "features": feature_info}
    if atlas is not None:
        from .atlas import region_table

        regions = region_table(grid, atlas, times)
        regions.to_parquet(out_dir / "regions.parquet", index=False)
        nets = [c for c in regions.columns if c.startswith("net_")]
        summary["network_means"] = {c[4:]: _f(regions[c].mean()) for c in nets}
        summary["network_means_first_30s"] = {c[4:]: _f(regions[c].iloc[:30].mean()) for c in nets}
    summary["global_mean"] = _f(np.nanmean(grid))
    summary["words"] = int(len(words))

    meta = {**meta, "timing": {k: v for k, v in timing.items() if k != "start_s"}, "features": feature_info}
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
    files = sorted(p for p in out_dir.rglob("*") if p.is_file() and p.name != "manifest.json")
    manifest = {
        "files": [{"path": p.relative_to(out_dir).as_posix(), "bytes": p.stat().st_size} for p in files],
        "main": "preds.npy",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    summary["bytes"] = sum(f["bytes"] for f in manifest["files"])
    return summary


def _f(x) -> float | None:
    x = float(x)
    return None if np.isnan(x) else round(x, 6)

"""Brain-player data: TRIBE predictions quantized to uint8 and served in chunks.

Quantization (per result, cached next to preds.npy as preds_u8.npy + preds_u8.json):
    0        = no prediction (NaN)
    1..255   = value mapped linearly from [lo, hi], symmetric around 0 so 128 is 0:
               lo = -m, hi = +m with m = 99.5th percentile of |value|
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

QUANT_VERSION = 1


def quantize(preds: np.ndarray) -> tuple[np.ndarray, dict]:
    x = preds.astype(np.float32)
    finite = np.isfinite(x)
    m = float(np.percentile(np.abs(x[finite]), 99.5)) if finite.any() else 1.0
    m = m if m > 0 else 1.0
    lo, hi = -m, m
    q = np.zeros(x.shape, dtype=np.uint8)
    scaled = np.clip((x - lo) / (hi - lo), 0, 1)
    q[finite] = (1 + np.rint(scaled[finite] * 254)).astype(np.uint8)
    return q, {"version": QUANT_VERSION, "lo": lo, "hi": hi, "zero_code": 128, "nan_code": 0, "levels": 254}


def dequantize(code: int, scale: dict) -> float | None:
    if code == 0:
        return None
    return scale["lo"] + (code - 1) / scale["levels"] * (scale["hi"] - scale["lo"])


def load_quantized(result_dir: Path) -> tuple[np.ndarray, dict]:
    """The uint8 frames for a TRIBE result folder, built once and cached."""
    q_path, meta_path = result_dir / "preds_u8.npy", result_dir / "preds_u8.json"
    src = result_dir / "preds.npy"
    if q_path.exists() and meta_path.exists() and q_path.stat().st_mtime >= src.stat().st_mtime:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("version") == QUANT_VERSION:
            return np.load(q_path, mmap_mode="r"), meta
    q, meta = quantize(np.load(src))
    np.save(q_path, q)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return np.load(q_path, mmap_mode="r"), meta


def frames_bytes(q: np.ndarray, start: int, count: int) -> bytes:
    start = max(0, min(start, q.shape[0]))
    end = max(start, min(start + count, q.shape[0]))
    return np.ascontiguousarray(q[start:end]).tobytes()


def network_peaks(t: list[float], values: list[float | None], k: int = 3, min_gap_s: float = 10) -> list[dict]:
    """The k highest local maxima at least min_gap_s apart."""
    v = np.array([np.nan if x is None else x for x in values], dtype=np.float64)
    if v.size < 3 or not np.isfinite(v).any():
        return []
    candidates = [i for i in range(1, len(v) - 1) if np.isfinite(v[i]) and v[i] >= np.nanmax(v[i - 1 : i + 2])]
    candidates.sort(key=lambda i: -v[i])
    chosen: list[int] = []
    for i in candidates:
        if all(abs(t[i] - t[j]) >= min_gap_s for j in chosen):
            chosen.append(i)
        if len(chosen) == k:
            break
    return [{"t": float(t[i]), "value": round(float(v[i]), 5)} for i in sorted(chosen)]

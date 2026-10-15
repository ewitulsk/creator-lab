"""Cheap visual features over time (after Bakhshi et al., arXiv 1510.06223 style descriptors).

Frames are sampled at SAMPLE_FPS. Per second of video:
    cut          1 if a shot boundary falls in that second (HSV-histogram distance spike)
    motion       mean absolute frame difference (0..1)
    edges        Canny edge density (0..1)
    brightness   mean V (0..1)
    saturation   mean S (0..1)
    colorfulness Hasler-Suesstrunk colorfulness
    faces        number of faces (YuNet, if its model is available; otherwise null)
Summary: cuts per minute, mean shot length, means of the above, resolution, fps, length.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable
from pathlib import Path

import numpy as np

log = logging.getLogger("runner.visual")

SAMPLE_FPS = 4.0
CUT_THRESHOLD = 0.45  # Bhattacharyya distance between consecutive HSV histograms
YUNET_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
)


def colorfulness(frame_bgr: np.ndarray) -> float:
    b, g, r = (frame_bgr[..., i].astype(np.float32) for i in range(3))
    rg = np.abs(r - g)
    yb = np.abs(0.5 * (r + g) - b)
    return float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


def face_detector(cache_dir: Path | None):
    """YuNet face detector, or None when the model can't be had."""
    import cv2

    if cache_dir is None or not hasattr(cv2, "FaceDetectorYN"):
        return None
    path = Path(cache_dir) / "face_detection_yunet_2023mar.onnx"
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(YUNET_URL, path)
        return cv2.FaceDetectorYN.create(str(path), "", (320, 320), 0.8)
    except Exception as err:
        log.warning("face detection unavailable: %s", err)
        return None


def frame_features(prev_hist, frame_bgr: np.ndarray, prev_gray, detector):
    import cv2

    small = cv2.resize(frame_bgr, (320, int(320 * frame_bgr.shape[0] / frame_bgr.shape[1])))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [16, 8, 8], [0, 180, 0, 256, 0, 256])
    cv2.normalize(hist, hist)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 100, 200)
    out = {
        "cut_distance": float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA))
        if prev_hist is not None
        else 0.0,
        "motion": float(np.abs(gray.astype(np.float32) - prev_gray).mean() / 255) if prev_gray is not None else 0.0,
        "edges": float((edges > 0).mean()),
        "brightness": float(hsv[..., 2].mean() / 255),
        "saturation": float(hsv[..., 1].mean() / 255),
        "colorfulness": colorfulness(small),
        "faces": None,
    }
    if detector is not None:
        detector.setInputSize((small.shape[1], small.shape[0]))
        _ok, faces = detector.detect(small)
        out["faces"] = 0 if faces is None else int(len(faces))
    return out, hist, gray.astype(np.float32)


def per_second(samples: list[dict], times: list[float], duration: float) -> dict[str, list]:
    n = max(1, int(np.ceil(duration)))
    keys = ["motion", "edges", "brightness", "saturation", "colorfulness", "faces"]
    series: dict[str, list] = {k: [None] * n for k in keys}
    series["cut"] = [0] * n
    buckets: dict[int, list[dict]] = {}
    for s, t in zip(samples, times, strict=True):
        buckets.setdefault(min(int(t), n - 1), []).append(s)
    for sec, rows in buckets.items():
        for k in keys:
            vals = [r[k] for r in rows if r[k] is not None]
            series[k][sec] = round(float(np.mean(vals)), 5) if vals else None
        series["cut"][sec] = int(any(r["cut_distance"] >= CUT_THRESHOLD for r in rows))
    series["t"] = list(range(n))
    return series


def summarize(series: dict[str, list], duration: float, width: int, height: int, fps: float) -> dict:
    cuts = int(sum(series["cut"]))
    summary = {
        "duration_s": round(duration, 2),
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "cuts": cuts,
        "cuts_per_minute": round(cuts / max(duration / 60, 1e-6), 3),
        "mean_shot_length_s": round(duration / (cuts + 1), 3),
    }
    for k in ("motion", "edges", "brightness", "saturation", "colorfulness", "faces"):
        vals = [v for v in series[k] if v is not None]
        summary[f"mean_{k}"] = round(float(np.mean(vals)), 5) if vals else None
    first = {k: [v for v in series[k][:30] if v is not None] for k in ("motion", "faces")}
    summary["cuts_first_30s"] = int(sum(series["cut"][:30]))
    summary["mean_motion_first_30s"] = round(float(np.mean(first["motion"])), 5) if first["motion"] else None
    return summary


def analyze_video(path: Path, cache_dir: Path | None = None, progress: Callable[[float], None] | None = None) -> dict:
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"can't open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = n_frames / fps if n_frames else 0.0
    step = max(1, int(round(fps / SAMPLE_FPS)))
    detector = face_detector(cache_dir)
    samples, times = [], []
    prev_hist = prev_gray = None
    idx = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if ok:
                feats, prev_hist, prev_gray = frame_features(prev_hist, frame, prev_gray, detector)
                samples.append(feats)
                times.append(idx / fps)
                if progress and n_frames and len(samples) % 20 == 0:
                    progress(100 * idx / n_frames)
        idx += 1
    cap.release()
    duration = max(duration, idx / fps)
    series = per_second(samples, times, duration)
    return {
        "summary": summarize(series, duration, width, height, fps),
        "series": series,
        "faces_available": detector is not None,
    }


def write_outputs(out_dir: Path, result: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "visual_features.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    return path

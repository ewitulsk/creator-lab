"""Cheap audio features over time: loudness, speech/music estimates and speech rate.

Per second:
    loudness_lufs   momentary loudness (ffmpeg ebur128, 400 ms window)
    rms_db          RMS level in dBFS (16 kHz mono)
    flatness        spectral flatness (0 = tonal, 1 = noise-like)
    speech          1 when captions place speech in that second (null without captions)
    music_score     heuristic 0..1: sustained tonal energy with steady loudness
Summary: integrated LUFS, loudness range, speech ratio (captions), speech rate (words per
minute from captions), mean music score, silence ratio.

speech/music are heuristics, documented as such; captions are the speech source when present.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np

from ..common.media import ffmpeg_exe

SR = 16000
MOMENTARY_RE = re.compile(r"t:\s*([\d.]+)\s+TARGET:.*?M:\s*(-?[\d.]+|-inf)")
SUMMARY_I_RE = re.compile(r"I:\s+(-?[\d.]+) LUFS")
SUMMARY_LRA_RE = re.compile(r"LRA:\s+([\d.]+) LU")


def decode_pcm(path: Path) -> np.ndarray:
    out = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        capture_output=True,
        check=True,
    )
    return np.frombuffer(out.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def ebur128(path: Path) -> tuple[list[tuple[float, float]], float | None, float | None]:
    proc = subprocess.run(
        [ffmpeg_exe(), "-nostats", "-i", str(path), "-filter_complex", "ebur128", "-f", "null", "-"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    return parse_ebur128(proc.stderr)


def parse_ebur128(text: str) -> tuple[list[tuple[float, float]], float | None, float | None]:
    points = []
    for m in MOMENTARY_RE.finditer(text):
        val = m.group(2)
        points.append((float(m.group(1)), float("-inf") if val == "-inf" else float(val)))
    i = SUMMARY_I_RE.search(text)
    lra = SUMMARY_LRA_RE.search(text)
    return points, (float(i.group(1)) if i else None), (float(lra.group(1)) if lra else None)


def frame_stats(pcm: np.ndarray, seconds: int) -> tuple[list, list, list]:
    rms_db, flatness, music = [], [], []
    for s in range(seconds):
        chunk = pcm[s * SR : (s + 1) * SR]
        if chunk.size < SR // 4:
            rms_db.append(None)
            flatness.append(None)
            music.append(None)
            continue
        rms = float(np.sqrt(np.mean(chunk**2)) + 1e-12)
        rms_db.append(round(20 * np.log10(rms), 2))
        spec = np.abs(np.fft.rfft(chunk * np.hanning(chunk.size))) + 1e-10
        flat = float(np.exp(np.mean(np.log(spec))) / np.mean(spec))
        flatness.append(round(flat, 5))
        # Steady loudness across 100 ms sub-frames plus tonal spectrum suggests music.
        sub = chunk[: (chunk.size // 1600) * 1600].reshape(-1, 1600)
        sub_db = 20 * np.log10(np.sqrt((sub**2).mean(axis=1)) + 1e-9)
        steadiness = float(np.clip(1 - np.std(sub_db) / 12, 0, 1))
        tonal = float(np.clip(1 - flat * 4, 0, 1))
        music.append(round(0.5 * steadiness + 0.5 * tonal, 4) if rms_db[-1] > -50 else 0.0)
    return rms_db, flatness, music


def caption_speech(cues: list[dict], seconds: int) -> tuple[list[int], int]:
    speech = [0] * seconds
    words = 0
    for c in cues:
        words += len(str(c.get("text", "")).split())
        for s in range(int(c["start"]), min(seconds, int(np.ceil(c["end"])))):
            speech[s] = 1
    return speech, words


def analyze_audio(path: Path, captions_json: Path | None = None) -> dict:
    pcm = decode_pcm(path)
    duration = pcm.size / SR
    seconds = max(1, int(np.ceil(duration)))
    rms_db, flatness, music = frame_stats(pcm, seconds)
    points, integrated, lra = ebur128(path)
    lufs: list[float | None] = [None] * seconds
    for t, m in points:
        s = min(int(t), seconds - 1)
        if np.isfinite(m):
            lufs[s] = round(m, 2)
    speech: list | None = None
    words = None
    if captions_json and Path(captions_json).exists():
        cues = json.loads(Path(captions_json).read_text(encoding="utf-8")).get("cues", [])
        speech, words = caption_speech(cues, seconds)
    silence = sum(1 for r in rms_db if r is not None and r < -50) / seconds
    summary = {
        "duration_s": round(duration, 2),
        "integrated_lufs": integrated,
        "loudness_range_lu": lra,
        "mean_rms_db": round(float(np.mean([r for r in rms_db if r is not None])), 2) if any(rms_db) else None,
        "silence_ratio": round(silence, 4),
        "mean_music_score": round(float(np.mean([m for m in music if m is not None])), 4) if any(music) else None,
        "speech_ratio": round(sum(speech) / seconds, 4) if speech is not None else None,
        "speech_rate_wpm": round(words / max(duration / 60, 1e-6), 2) if words is not None else None,
        "speech_source": "captions" if speech is not None else None,
    }
    series = {
        "t": list(range(seconds)),
        "loudness_lufs": lufs,
        "rms_db": rms_db,
        "flatness": flatness,
        "music_score": music,
        "speech": speech,
    }
    return {"summary": summary, "series": series}

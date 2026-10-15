"""Cheap feature extractors and engagement curves on synthetic inputs only."""

import json

import numpy as np
import pytest

from runners.cpu import audio, engagement, visual


def make_video(path, seconds=6, fps=10, cut_at=3):
    """Synthetic clip: a flat red scene, then a hard cut to a striped blue scene."""
    cv2 = pytest.importorskip("cv2")
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (160, 120))
    if not writer.isOpened():
        pytest.skip("no mp4v encoder")
    for i in range(seconds * fps):
        frame = np.zeros((120, 160, 3), np.uint8)
        if i < cut_at * fps:
            frame[:] = (0, 0, 200)
        else:
            frame[:, (np.arange(160) // 8) % 2 == 0] = (255, 255, 255)
        writer.write(frame)
    writer.release()


def test_visual_features_find_the_cut(tmp_path):
    path = tmp_path / "clip.mp4"
    make_video(path)
    result = visual.analyze_video(path, cache_dir=None)
    s = result["summary"]
    assert s["width"] == 160 and s["height"] == 120
    assert s["cuts"] == 1 and result["series"]["cut"][3] == 1
    assert result["series"]["edges"][0] < result["series"]["edges"][5]  # stripes have edges
    assert result["faces_available"] is False and s["mean_faces"] is None
    assert len(result["series"]["t"]) == 6


def test_colorfulness_orders_scenes():
    grey = np.full((10, 10, 3), 128, np.uint8)
    vivid = np.zeros((10, 10, 3), np.uint8)
    vivid[:, :5] = (0, 0, 255)
    vivid[:, 5:] = (0, 255, 0)
    assert visual.colorfulness(vivid) > visual.colorfulness(grey) == 0


def test_audio_frame_stats_tone_vs_noise():
    sr = audio.SR
    t = np.arange(sr * 2) / sr
    tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    noise = (np.random.default_rng(0).normal(scale=0.3, size=sr * 2)).astype(np.float32)
    silence = np.zeros(sr, np.float32)
    rms, flat, music = audio.frame_stats(np.concatenate([tone, noise, silence]), 5)
    assert flat[0] < 0.1 < flat[2]  # a pure tone is tonal, noise is flat
    assert music[0] > music[2] and music[4] == 0.0
    assert rms[4] < -50


def test_parse_ebur128_and_captions():
    text = (
        "[Parsed_ebur128_0 @ x] t: 0.4  TARGET:-23 LUFS    M: -30.2 S:-120.7     I: -30.2 LUFS       LRA:   0.0 LU\n"
        "[Parsed_ebur128_0 @ x] t: 1.4  TARGET:-23 LUFS    M:-inf S:-120.7     I: -30.2 LUFS       LRA:   0.0 LU\n"
        "  Integrated loudness:\n    I:         -18.5 LUFS\n  Loudness range:\n    LRA:         6.2 LU\n"
    )
    points, integrated, lra = audio.parse_ebur128(text)
    assert points[0] == (0.4, -30.2) and points[1][1] == float("-inf")
    assert integrated == -30.2 or integrated == -18.5
    assert lra in (0.0, 6.2)
    speech, words = audio.caption_speech([{"start": 0.5, "end": 2.2, "text": "hello there world"}], 5)
    assert speech == [1, 1, 1, 0, 0] and words == 3


def _synthetic_atlas():
    areas = ["V1", "FEF", "AVI", "31pv", "A1"]
    va = np.array([i % len(areas) for i in range(2 * engagement.N_HEMI)], dtype=np.int16)
    return {"areas": areas, "vertex_area": va.tolist()}


def test_engagement_index_tracks_attention_minus_default():
    T, V = 120, 2 * engagement.N_HEMI
    rng = np.random.default_rng(0)
    yeo = np.array([1 + (i % 7) for i in range(V)], dtype=np.int8)
    signal = np.sin(np.arange(T) / 6.0)
    preds = rng.normal(scale=0.5, size=(T, V)).astype(np.float32)
    preds[:, yeo == 3] += signal[:, None]  # dorsal attention follows the signal
    preds[:, yeo == 7] -= signal[:, None]  # default mode does the opposite
    out = engagement.compute(preds, _synthetic_atlas(), yeo)
    idx = out["curves"]["engagement_index"]
    assert np.corrcoef(idx, signal)[0, 1] > 0.9
    assert out["curves"]["anterior_insula"] is not None and len(out["hcp"]) == 5
    # Global signal regression: a cortex-wide shift doesn't move region curves.
    shifted = engagement.compute(preds + np.linspace(0, 5, T)[:, None], _synthetic_atlas(), yeo)
    assert np.corrcoef(shifted["curves"]["engagement_index"], signal)[0, 1] > 0.9


def test_engagement_run_writes_outputs_with_hcp_fallback(tmp_path):
    tribe = tmp_path / "tribe"
    tribe.mkdir()
    rng = np.random.default_rng(1)
    np.save(tribe / "preds.npy", rng.normal(size=(40, 2 * engagement.N_HEMI)).astype(np.float16))
    (tribe / "timing.json").write_text(json.dumps({"tr_s": 1.0, "start_s": list(range(40))}))
    atlas_path = tmp_path / "atlas.json"
    atlas = _synthetic_atlas()
    atlas["areas"] = engagement.YEO_FROM_HCP["dorsal_attention"][:2] + ["AVI", "31pv", "7m"]
    atlas_path.write_text(json.dumps(atlas))
    out = tmp_path / "out"
    summary = engagement.run(tribe, atlas_path, cache_dir=None, out_dir=out)  # no cache: HCP approximation
    doc = json.loads((out / "engagement.json").read_text())
    assert "approximated from HCP-MMP1" in doc["atlases"]["yeo7"]
    assert doc["nacc"] is None and "subcortical" in doc["nacc_note"]
    assert len(doc["t"]) == 40 and len(doc["curves"]["stimulus_drive"]) == 40
    assert summary["n_timesteps"] == 40 and summary["mean_stimulus_drive"] > 0
    assert (out / "engagement_regions.npz").exists()

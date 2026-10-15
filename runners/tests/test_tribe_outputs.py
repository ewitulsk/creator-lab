"""TRIBE runner output handling, on small synthetic arrays (no models, no media)."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from runners.tribe.cache_seed import copy_uid_folder, rewrite_key
from runners.tribe.outputs import Predicted, to_grid, write_run

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import tribe_periodicity  # noqa: E402


def test_to_grid_one_row_per_second():
    preds = np.arange(6 * 3, dtype=np.float32).reshape(6, 3)
    starts = np.array([0.0, 1.0, 1.0, 2.0, 4.0, 9.0])  # a duplicate at 1 s, a gap at 3 s, one past the end
    grid, stats = to_grid(preds, starts, duration=5.4)
    assert grid.shape == (5, 3)
    np.testing.assert_allclose(grid[1], (preds[1] + preds[2]) / 2)
    assert np.isnan(grid[3]).all()
    assert stats == {"rows_in": 6, "rows_beyond_end": 1, "duplicate_rows_merged": 1, "seconds_without_prediction": 1}


def _synthetic_run(n_seconds=7, vertices=12):
    pred = Predicted(tr=1.0)
    rows = []
    for seg in range(2):  # two 5-row windows
        for t in range(5):
            start = seg * 5 + t
            rows.append(
                {
                    "row": len(rows),
                    "batch": 0,
                    "segment": seg,
                    "segment_start_s": float(seg * 5),
                    "segment_duration_s": 5.0,
                    "start_s": float(start),
                    "duration_s": 1.0,
                    "kept": start < n_seconds,
                    "n_events": int(start < n_seconds),
                    "has_video": start < n_seconds,
                    "has_audio": start < n_seconds,
                    "has_word": False,
                    "has_text": False,
                    "event_types": "Video" if start < n_seconds else "",
                }
            )
    pred.table = rows
    rng = np.random.default_rng(0)
    pred.rows = [rng.normal(size=(10, vertices)).astype(np.float32)]
    t = np.arange(20) * 0.5 + 0.25
    pred.features = {"video": [(t, rng.normal(size=(20, 2, 4)).astype(np.float32))]}
    pred.frequency = {"video": 2.0}
    return pred


def test_write_run_saves_everything(tmp_path):
    pred = _synthetic_run()
    events = pd.DataFrame(
        [
            {"type": "Video", "start": 0.0, "duration": 7.0, "filepath": "v.mp4", "context": None},
            {
                "type": "Word",
                "start": 1.2,
                "duration": 0.3,
                "text": "hello",
                "sentence": "hello there",
                "context": ["x"],
            },
            {"type": "Word", "start": 1.6, "duration": 0.3, "text": "there", "sentence": "hello there", "context": 3},
        ]
    )
    atlas = {
        "areas": ["V1", "A1"],
        "vertex_area": np.array([0] * 6 + [1] * 6, dtype=np.int16),
        "networks": {"visual": [0], "auditory": [1]},
    }
    summary = write_run(tmp_path, pred, events, 7.2, atlas, {"model": {"id": "test"}})

    grid = np.load(tmp_path / "preds.npy")
    assert grid.shape == (7, 12) and grid.dtype == np.float16
    raw = np.load(tmp_path / "raw" / "preds_all_rows.npy")
    assert raw.shape == (10, 12)  # dropped rows are kept in raw/
    rows = pd.read_parquet(tmp_path / "rows.parquet")
    assert rows["kept"].sum() == 7 and len(rows) == 10
    timing = json.loads((tmp_path / "timing.json").read_text())
    assert timing["start_s"] == [0, 1, 2, 3, 4, 5, 6] and timing["rows_kept_by_tribe"] == 7
    words = pd.read_parquet(tmp_path / "words.parquet")
    assert list(words["text"]) == ["hello", "there"] and words["end"].iloc[0] == pytest.approx(1.5)
    assert (tmp_path / "transcript.txt").read_text().strip() == "hello there"
    assert len(pd.read_parquet(tmp_path / "events.parquet")) == 3
    feats = np.load(tmp_path / "features" / "video.npy")
    assert feats.shape == (20, 2, 4)
    assert np.all(np.diff(np.load(tmp_path / "features" / "video_times.npy")) > 0)
    pooled = np.load(tmp_path / "features" / "pooled.npz")
    assert pooled["video"].shape == (2, 4)
    regions = pd.read_parquet(tmp_path / "regions.parquet")
    assert {"t", "net_visual", "net_auditory", "area_V1", "area_A1"} <= set(regions.columns)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    paths = {f["path"] for f in manifest["files"]}
    assert {"preds.npy", "rows.parquet", "raw/preds_all_rows.npy", "meta.json", "features/video.npy"} <= paths
    meta = json.loads((tmp_path / "meta.json").read_text())
    assert meta["model"]["id"] == "test" and meta["timing"]["n_timesteps"] == 7
    assert summary["n_timesteps"] == 7 and set(summary["network_means"]) == {"visual", "auditory"}


def test_cache_seed_rewrites_keys(tmp_path):
    src = tmp_path / "src" / "uid"
    (src / "data").mkdir(parents=True)
    (src / "data" / "abc.data").write_bytes(b"\x00" * 16)
    (src / "config.yaml").write_text("x: 1")
    rec = {
        "data": {"filename": "data/abc.data", "dtype": "float32", "shape": [2, 2]},
        "#key": "D:\\BrainThing\\samples\\roomba\\roomba_h264.mp4_0.00_60.00",
    }
    (src / "host-1-info.jsonl").write_text(json.dumps(rec) + "\n")
    dst = tmp_path / "dst" / "uid"
    info = copy_uid_folder(src, dst, {"D:\\BrainThing": "D:\\VideoAnalysis\\BrainThing"})
    assert info == {"items": 1, "dtypes": ["float32"]}
    out = json.loads((dst / "seeded-host-1-info.jsonl").read_text())
    assert out["#key"] == "D:\\VideoAnalysis\\BrainThing\\samples\\roomba\\roomba_h264.mp4_0.00_60.00"
    assert (dst / "data" / "abc.data").stat().st_size == 16
    assert (src / "host-1-info.jsonl").read_text().count("D:\\\\BrainThing") == 1  # source untouched
    assert rewrite_key("E:\\other.mp4_1", {"D:\\x": "y"}) == "E:\\other.mp4_1"


def test_duplicated_words_make_a_120_row_cycle():
    """The demo bug in miniature: copies of the same word features every 120 s
    produce a 120-row cycle; aligned features don't."""
    rng = np.random.default_rng(1)
    seconds, vertices = 658, 50
    words = rng.normal(size=(seconds, vertices))
    timeline = 1858
    buggy = np.zeros((timeline, vertices))
    for k in range(11):  # 11 chunks, each shifted by start + offset = 120k
        buggy[120 * k : 120 * k + seconds] += words
    buggy += rng.normal(scale=0.3, size=buggy.shape)
    fixed = words + rng.normal(scale=0.3, size=words.shape)
    sim_bug = tribe_periodicity.lag_similarity(buggy, 200)
    sim_fix = tribe_periodicity.lag_similarity(fixed, 200)
    assert sim_bug[120] > 0.5 > abs(sim_fix[120])
    assert tribe_periodicity.periodic_variance_fraction(buggy, 120) > 0.3

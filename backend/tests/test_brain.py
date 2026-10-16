import json

import numpy as np
import pytest

from creator_lab import brain
from creator_lab.analyzers import current
from creator_lab.extensions import db
from creator_lab.models import AnalysisResult, Channel, Video


def test_quantize_roundtrip():
    rng = np.random.default_rng(0)
    preds = rng.normal(size=(30, 50)).astype(np.float16)
    preds[3, 7] = np.nan
    q, scale = brain.quantize(preds)
    assert q.dtype == np.uint8 and q[3, 7] == 0
    assert scale["lo"] == -scale["hi"] and scale["zero_code"] == 128
    assert brain.dequantize(128, scale) == pytest.approx(0, abs=scale["hi"] / 127)
    recon = brain.dequantize(int(q[0, 0]), scale)
    assert recon == pytest.approx(float(preds[0, 0]), abs=2 * scale["hi"] / 254 + 1e-3)
    assert set(np.unique(q)) <= set(range(256)) and q[np.isfinite(preds)].min() >= 1


def test_network_peaks():
    t = list(range(100))
    v = [0.0] * 100
    v[10], v[50], v[52], v[90] = 1.0, 3.0, 2.5, 2.0
    peaks = brain.network_peaks(t, v, k=3, min_gap_s=10)
    assert [p["t"] for p in peaks] == [10, 50, 90]


@pytest.fixture
def tribe_video(app):
    ch = Channel(platform_id=1, external_id="UC" + "z" * 22, title="c")
    db.session.add(ch)
    db.session.flush()
    v = Video(channel_id=ch.id, platform_id=1, external_id="vid1", title="t", duration_s=40, format="long")
    db.session.add(v)
    db.session.flush()
    folder = app.config["APP"].data_root / "analyzers" / "tribe_video" / "1" / "vid1"
    folder.mkdir(parents=True)
    rng = np.random.default_rng(1)
    np.save(folder / "preds.npy", rng.normal(size=(40, 20484)).astype(np.float16))
    (folder / "timing.json").write_text(json.dumps({"tr_s": 1.0, "start_s": list(range(40)), "hrf_offset_s": 5.0}))
    nets = {"t": list(range(40)), "networks": {"visual": [float(np.sin(i / 3)) for i in range(40)]}}
    (folder / "networks.json").write_text(json.dumps(nets))
    db.session.add(
        AnalysisResult(
            video_id=v.id,
            analyzer_id=current("tribe_video").id,
            status="done",
            output_path="analyzers/tribe_video/1/vid1",
            summary={},
        )
    )
    db.session.commit()
    return v


def test_brain_meta_and_frames(client, tribe_video):
    meta = client.get(f"/api/videos/{tribe_video.id}/brain").json
    assert meta["n_timesteps"] == 40 and meta["n_vertices"] == 20484 and meta["tr_s"] == 1.0
    assert meta["start_s"][:3] == [0, 1, 2] and meta["hrf_offset_s"] == 5.0
    assert meta["networks"]["networks"]["visual"][0] == 0.0
    assert meta["peaks"]["visual"]
    r = client.get(meta["frames_url"] + "&start=38&count=10")
    assert r.status_code == 200 and r.headers["X-Frame-Count"] == "2"
    assert len(r.data) == 2 * 20484
    # The quantized cache is built once.
    folder = tribe_video.channel  # noqa: F841
    again = client.get(meta["frames_url"] + "&start=0&count=1")
    assert len(again.data) == 20484


def test_brain_404s(client, app, tribe_video):
    assert client.get(f"/api/videos/{tribe_video.id}/brain?analyzer=tribe_title").status_code == 404
    assert client.get(f"/api/videos/{tribe_video.id}/brain?analyzer=outlier_score").status_code == 400
    assert client.get("/api/brain/assets/mesh.json").status_code == 404
    assert client.get("/api/brain/assets/../../secret").status_code == 404
    brain_dir = app.config["APP"].data_root / "brain"
    brain_dir.mkdir()
    (brain_dir / "mesh.json").write_text("{}")
    assert client.get("/api/brain/assets/mesh.json").status_code == 200

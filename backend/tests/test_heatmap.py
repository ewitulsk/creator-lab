from creator_lab.extensions import db
from creator_lab.models import AnalysisResult, Asset, VideoChapter, VideoHeatmapPoint, VideoInfoCapture

from .fake_youtube import seed_channel
from .test_jobs import register, rh


def capture(n=100, views=1000):
    return {
        "captured_at": "2026-10-15T05:00:00Z",
        "heatmap": [{"start_s": i * 6.0, "end_s": (i + 1) * 6.0, "value": i / n} for i in range(n)],
        "chapters": [{"start_s": 0.0, "end_s": 30.0, "title": "Intro"}],
        "snapshot": {"view_count": views, "like_count": 9},
    }


def test_heatmap_job_works_on_discovered_videos(client, yt, anon, runner_headers, app):
    seed_channel(yt, n=2)
    cid = client.post("/api/channels", {"channel": "@alpha"}).json["channel"]["id"]
    vid = client.get(f"/api/channels/{cid}/videos").json["videos"][0]["id"]
    r = client.post("/api/jobs", {"analyzer": "heatmap", "video_ids": [vid]})
    assert [j["type"] for j in r.json["jobs"]] == ["heatmap"]  # no download chained

    rid = register(anon, runner_headers, caps=["heatmap"])
    h = rh(runner_headers, rid)
    job = anon.post("/api/jobs/claim", {"runner_id": rid, "types": ["heatmap"]}, headers=h).json["job"]
    item = job["items"][0]
    out = app.config["APP"].data_root / "media" / item["channel_external_id"] / item["external_id"]
    assert item["output_dir"] == str(out)
    out.mkdir(parents=True)
    (out / "info.json").write_text("{}")
    r = anon.post(
        f"/api/jobs/{job['id']}/items/{vid}/result",
        {
            "status": "done",
            "summary": {"points": 100},
            "assets": [{"kind": "info", "path": str(out / "info.json")}],
            "capture": capture(),
        },
        headers=h,
    )
    assert r.status_code == 200, r.json
    anon.post(f"/api/jobs/{job['id']}/complete", {}, headers=h)

    cap = db.session.scalar(db.select(VideoInfoCapture))
    assert cap.source == "heatmap" and cap.heatmap_points == 100 and cap.snapshot["view_count"] == 1000
    assert db.session.scalar(db.select(db.func.count()).select_from(VideoHeatmapPoint)) == 100
    assert db.session.scalar(db.select(VideoChapter)).title == "Intro"
    assert db.session.scalar(db.select(Asset).where(Asset.kind == "info")) is not None

    hm = client.get(f"/api/videos/{vid}/heatmap").json
    assert hm["capture_id"] == cap.id and len(hm["points"]) == 100 and hm["chapters"][0]["title"] == "Intro"
    cov = client.get("/api/coverage?selection=all").json
    assert "heatmap" in [a["name"] for a in cov["analyzers"]]


def test_download_captures_heatmap_too_and_versions(client, yt, anon, runner_headers, app):
    seed_channel(yt, n=1)
    cid = client.post("/api/channels", {"channel": "@alpha"}).json["channel"]["id"]
    vid = client.get(f"/api/channels/{cid}/videos").json["videos"][0]["id"]
    client.post("/api/discover/pull", {"video_ids": [vid]})
    rid = register(anon, runner_headers)
    h = rh(runner_headers, rid)
    job = anon.post("/api/jobs/claim", {"runner_id": rid, "types": ["media"]}, headers=h).json["job"]
    anon.post(f"/api/jobs/{job['id']}/items/{vid}/result", {"status": "done", "capture": capture(views=10)}, headers=h)
    hm_result = db.session.scalar(
        db.select(AnalysisResult)
        .join(AnalysisResult.analyzer)
        .where(AnalysisResult.video_id == vid)
        .where(AnalysisResult.analyzer.has(name="heatmap"))
    )
    assert hm_result.status == "done" and hm_result.summary["source"] == "download"

    # A later capture is a new version; asking for a specific one still works.
    client.post("/api/jobs", {"analyzer": "heatmap", "video_ids": [vid], "force": True})
    rid2 = register(anon, runner_headers, name="cpu-hm", caps=["heatmap"])
    h2 = rh(runner_headers, rid2)
    j2 = anon.post("/api/jobs/claim", {"runner_id": rid2, "types": ["heatmap"]}, headers=h2).json["job"]
    anon.post(
        f"/api/jobs/{j2['id']}/items/{vid}/result", {"status": "done", "capture": capture(n=50, views=99)}, headers=h2
    )
    caps = client.get(f"/api/videos/{vid}/heatmap").json["captures"]
    assert [c["heatmap_points"] for c in caps] == [100, 50]
    first = client.get(f"/api/videos/{vid}/heatmap?capture_id={caps[0]['id']}").json
    assert len(first["points"]) == 100


def test_bad_capture_points_are_skipped(client, yt, anon, runner_headers):
    seed_channel(yt, n=1)
    cid = client.post("/api/channels", {"channel": "@alpha"}).json["channel"]["id"]
    vid = client.get(f"/api/channels/{cid}/videos").json["videos"][0]["id"]
    client.post("/api/jobs", {"analyzer": "heatmap", "video_ids": [vid]})
    rid = register(anon, runner_headers, caps=["heatmap"])
    h = rh(runner_headers, rid)
    job = anon.post("/api/jobs/claim", {"runner_id": rid, "types": ["heatmap"]}, headers=h).json["job"]
    bad = {
        "heatmap": [
            {"start_s": 5, "end_s": 1, "value": 1},
            {"start_s": 0},
            "x",
            {"start_s": 0, "end_s": 1, "value": 0.5},
        ]
    }
    r = anon.post(f"/api/jobs/{job['id']}/items/{vid}/result", {"status": "done", "capture": bad}, headers=h)
    assert r.status_code == 200
    assert db.session.scalar(db.select(VideoInfoCapture)).heatmap_points == 1

import datetime as dt

import pytest

from creator_lab import jobs as svc
from creator_lab.extensions import db
from creator_lab.models import AnalysisResult, Asset, Job, Runner, Video, utcnow

from .fake_youtube import seed_channel


@pytest.fixture
def pulled(client, yt):
    """A channel with 3 pulled videos; returns their ids."""
    seed_channel(yt, n=3)
    cid = client.post("/api/channels", {"channel": "@alpha"}).json["channel"]["id"]
    ids = [v["id"] for v in client.get(f"/api/channels/{cid}/videos").json["videos"]]
    r = client.post("/api/discover/pull", {"video_ids": ids})
    assert r.status_code == 200, r.json
    return ids


def register(anon, runner_headers, name="cpu-1", rtype="cpu", caps=("media", "captions")):
    r = anon.post(
        "/api/runners/register",
        {"name": name, "type": rtype, "capabilities": list(caps), "gpu": None, "host": "test"},
        headers=runner_headers,
    )
    assert r.status_code == 200, r.json
    return r.json["runner"]["id"]


def rh(runner_headers, rid):
    return {**runner_headers, "X-Runner-Id": str(rid)}


def test_runner_endpoints_need_the_token(anon, client):
    assert anon.post("/api/runners/register", {"name": "x", "type": "cpu", "capabilities": []}).status_code == 401
    # A logged-in user is not a runner either.
    assert client.post("/api/jobs/claim", {"runner_id": 1}).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert anon.post("/api/jobs/claim", {"runner_id": 1}, headers=bad).status_code == 401


def test_pull_queues_download_and_captions(pulled, client):
    jobs = client.get("/api/jobs").json["jobs"]
    assert sorted(j["type"] for j in jobs) == ["captions", "media"]
    media = next(j for j in jobs if j["type"] == "media")
    assert media["items_total"] == 3 and media["status"] == "queued"
    statuses = {
        r.status for r in db.session.scalars(db.select(AnalysisResult).where(AnalysisResult.job_id == media["id"]))
    }
    assert statuses == {"queued"}
    # Pulling again does not queue duplicates.
    client.post("/api/discover/pull", {"video_ids": pulled})
    assert client.get("/api/jobs").json["total"] == 2


def test_full_job_lifecycle(pulled, anon, client, runner_headers, app):
    rid = register(anon, runner_headers)
    h = rh(runner_headers, rid)
    job = anon.post("/api/jobs/claim", {"runner_id": rid, "types": ["media"]}, headers=h).json["job"]
    assert job["type"] == "media" and len(job["items"]) == 3
    assert job["max_video_height"] == 1080
    item = job["items"][0]
    assert item["output_dir"].endswith(item["external_id"])
    assert anon.post("/api/jobs/claim", {"runner_id": rid, "types": ["media"]}, headers=h).json["job"] is None

    r = anon.post(
        f"/api/jobs/{job['id']}/progress",
        {
            "stage": "download",
            "stage_index": 1,
            "stage_count": 1,
            "video_index": 1,
            "video_count": 3,
            "video_id": item["video_id"],
            "stage_percent": 12.5,
            "overall_percent": 4.2,
            "message": "12.5% of 80 MB",
            "log": "starting download",
        },
        headers=h,
    )
    assert r.json == {"cancel": False}
    summary = client.get(f"/api/jobs/{job['id']}").json["job"]
    assert summary["status"] == "running"
    assert summary["progress_text"] == "download on video 1 of 3, 12%"
    assert summary["stage_timings"][0]["stage"] == "download"
    assert summary["items"][0]["status"] == "running"
    assert "starting download" in client.get(f"/api/jobs/{job['id']}/log").json["log"]

    # Report the first video's file.
    out = app.config["APP"].data_root / "media" / item["channel_external_id"] / item["external_id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "video.mp4").write_bytes(b"\x00" * 1000)
    r = anon.post(
        f"/api/jobs/{job['id']}/items/{item['video_id']}/result",
        {
            "status": "done",
            "summary": {"height": 1080},
            "assets": [
                {"kind": "video", "path": str(out / "video.mp4"), "height": 1080, "width": 1920, "codec": "h264"}
            ],
        },
        headers=h,
    )
    assert r.status_code == 200, r.json
    asset = db.session.scalar(db.select(Asset).where(Asset.kind == "video"))
    assert asset.bytes == 1000 and asset.height == 1080 and not asset.path.startswith(("C:", "/"))
    anon.post(
        f"/api/jobs/{job['id']}/items/{job['items'][1]['video_id']}/result",
        {"status": "failed", "error": "HTTP 403"},
        headers=h,
    )
    r = anon.post(f"/api/jobs/{job['id']}/complete", {}, headers=h)
    assert r.json["job"]["status"] == "done"
    assert r.json["job"]["items_by_status"] == {"done": 1, "failed": 2}
    detail = client.get(f"/api/videos/{item['video_id']}").json
    media = next(a for a in detail["analyzers"] if a["analyzer"] == "media")
    assert media["status"] == "done"


def test_asset_paths_must_stay_in_the_data_folder(pulled, anon, runner_headers, tmp_path):
    rid = register(anon, runner_headers)
    h = rh(runner_headers, rid)
    job = anon.post("/api/jobs/claim", {"runner_id": rid, "types": ["media"]}, headers=h).json["job"]
    outside = tmp_path / "evil.mp4"
    outside.write_bytes(b"x")
    vid = job["items"][0]["video_id"]
    r = anon.post(
        f"/api/jobs/{job['id']}/items/{vid}/result",
        {"status": "done", "assets": [{"kind": "video", "path": str(outside)}]},
        headers=h,
    )
    assert r.status_code == 400


def test_tribe_job_waits_for_download(pulled, anon, client, runner_headers, app):
    # Cancel the auto-queued download so the TRIBE request must chain its own.
    for j in client.get("/api/jobs").json["jobs"]:
        client.post(f"/api/jobs/{j['id']}/cancel")
    r = client.post("/api/jobs", {"analyzer": "tribe_video", "video_ids": pulled[:2]})
    jobs = r.json["jobs"]
    assert sorted(j["type"] for j in jobs) == ["media", "tribe_video"]
    tribe = next(j for j in jobs if j["type"] == "tribe_video")
    download = next(j for j in jobs if j["type"] == "media")
    assert tribe["depends_on"] == [download["id"]]

    gpu = register(anon, runner_headers, name="gpu-1", rtype="gpu", caps=["tribe_video", "tribe_title"])
    hg = rh(runner_headers, gpu)
    assert anon.post("/api/jobs/claim", {"runner_id": gpu}, headers=hg).json["job"] is None  # still waiting
    cpu = register(anon, runner_headers)
    hc = rh(runner_headers, cpu)
    dl = anon.post("/api/jobs/claim", {"runner_id": cpu, "types": ["media"]}, headers=hc).json["job"]
    anon.post(f"/api/jobs/{dl['id']}/complete", {}, headers=hc)
    claimed = anon.post("/api/jobs/claim", {"runner_id": gpu}, headers=hg).json["job"]
    assert claimed["id"] == tribe["id"]
    assert set(claimed["items"][0]["inputs"]) >= {"media", "title_text", "thumbnail"}
    assert claimed["items"][0]["inputs"]["title_text"].endswith("title.txt")


def test_cancel_reaches_the_runner(pulled, anon, client, runner_headers):
    rid = register(anon, runner_headers)
    h = rh(runner_headers, rid)
    job = anon.post("/api/jobs/claim", {"runner_id": rid, "types": ["media"]}, headers=h).json["job"]
    client.post(f"/api/jobs/{job['id']}/cancel")
    r = anon.post(f"/api/jobs/{job['id']}/progress", {"stage": "download"}, headers=h)
    assert r.json == {"cancel": True}
    hb = anon.post(f"/api/runners/{rid}/heartbeat", {}, headers=runner_headers).json
    assert job["id"] in hb["cancelled_jobs"]
    statuses = {
        r.status for r in db.session.scalars(db.select(AnalysisResult).where(AnalysisResult.job_id == job["id"]))
    }
    assert statuses == {"missing"}


def test_silent_runner_job_is_requeued(pulled, anon, runner_headers):
    rid = register(anon, runner_headers)
    h = rh(runner_headers, rid)
    job = anon.post("/api/jobs/claim", {"runner_id": rid, "types": ["media"]}, headers=h).json["job"]
    r = db.session.get(Runner, rid)
    r.last_heartbeat = utcnow() - dt.timedelta(minutes=11)
    db.session.commit()
    assert svc.requeue_stale_jobs() == 1
    assert db.session.get(Job, job["id"]).status == "queued"
    # The old runner's late calls are rejected; another runner can take the job.
    other = register(anon, runner_headers, name="cpu-2")
    again = anon.post("/api/jobs/claim", {"runner_id": other, "types": ["media"]}, headers=rh(runner_headers, other))
    assert again.json["job"]["id"] == job["id"]
    late = anon.post(f"/api/jobs/{job['id']}/progress", {"stage": "x"}, headers=h)
    assert late.status_code == 409


def test_fail_with_retry_requeues_until_attempts_run_out(pulled, anon, runner_headers):
    rid = register(anon, runner_headers)
    h = rh(runner_headers, rid)
    for attempt in range(3):
        job = anon.post("/api/jobs/claim", {"runner_id": rid, "types": ["media"]}, headers=h).json["job"]
        assert job["attempt"] == attempt + 1
        anon.post(f"/api/jobs/{job['id']}/fail", {"error": "throttled", "log_tail": "429", "retry": True}, headers=h)
    final = db.session.get(Job, job["id"])
    assert final.status == "failed" and final.attempts == 3


def test_retry_creates_new_job_for_unfinished_items(pulled, anon, client, runner_headers):
    rid = register(anon, runner_headers)
    h = rh(runner_headers, rid)
    job = anon.post("/api/jobs/claim", {"runner_id": rid, "types": ["media"]}, headers=h).json["job"]
    anon.post(f"/api/jobs/{job['id']}/fail", {"error": "boom"}, headers=h)
    r = client.post(f"/api/jobs/{job['id']}/retry")
    assert r.status_code == 200, r.json
    assert r.json["job"]["items_total"] == 3 and r.json["job"]["id"] != job["id"]


def test_coverage_grid(pulled, client):
    r = client.get("/api/coverage").json
    names = [a["name"] for a in r["analyzers"]]
    assert {"outlier_score", "media", "captions", "tribe_video", "tribe_title"} <= set(names)
    assert r["total"] == 3
    cells = r["videos"][0]["cells"]
    assert cells["media"]["status"] == "queued"
    assert cells["tribe_video"]["status"] == "missing"
    assert cells["outlier_score"]["status"] in ("done", "failed")


def test_issue_job_validation(pulled, client):
    assert client.post("/api/jobs", {"analyzer": "outlier_score", "video_ids": pulled}).status_code == 400
    assert client.post("/api/jobs", {"analyzer": "nope", "video_ids": pulled}).status_code == 400
    assert client.post("/api/jobs", {"analyzer": "captions", "video_ids": "x"}).status_code == 400
    # Captions are already queued for these videos: nothing new to issue.
    assert client.post("/api/jobs", {"analyzer": "captions", "video_ids": pulled}).json["skipped"] == ["captions"]


def test_job_stream_once(pulled, client):
    r = client.get("/api/jobs/stream?once=1")
    text = r.get_data(as_text=True)
    assert "event: jobs" in text and '"progress_text"' in text


def test_runners_list(pulled, anon, client, runner_headers):
    register(anon, runner_headers, name="gpu-1", rtype="gpu", caps=["tribe_video"])
    runners = client.get("/api/runners").json["runners"]
    assert runners[0]["name"] == "gpu-1" and runners[0]["online"] is True
    assert client.get("/api/analyzers").json["analyzers"][0]["name"] == "outlier_score"


def test_media_counts_as_done_when_file_exists(pulled, client, app):
    v = db.session.get(Video, pulled[0])
    for j in client.get("/api/jobs").json["jobs"]:
        client.post(f"/api/jobs/{j['id']}/cancel")
    folder = app.config["APP"].data_root / "media" / v.channel.external_id / v.external_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "video.mp4").write_bytes(b"x")
    svc.register_asset(v, {"kind": "video", "path": str(folder / "video.mp4")})
    db.session.commit()
    r = client.post("/api/jobs", {"analyzer": "media", "video_ids": [v.id]}).json
    assert r["skipped"] == ["media"]

"""The runners' own client (runners/common) against a live server: the full job protocol,
with a fake handler instead of real downloads or models."""

import sys
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from creator_lab.extensions import db
from creator_lab.models import AnalysisResult, Job

from .conftest import RUNNER_TOKEN
from .fake_youtube import seed_channel

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for `runners`

from runners.common.client import PlatformClient  # noqa: E402
from runners.common.config import RunnerConfig  # noqa: E402
from runners.common.runner import BaseRunner  # noqa: E402


@pytest.fixture
def live(app):
    import logging

    # The alembic test's fileConfig() disables existing loggers; undo that for the runner's.
    for name in ("runner", "runner.download", "runner.captions"):
        logging.getLogger(name).disabled = False
    logging.getLogger("runner").setLevel(logging.INFO)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


class FakeCaptionsRunner(BaseRunner):
    type = "cpu"
    capabilities = ["captions"]

    def __init__(self, url):
        cfg = RunnerConfig(url=url, token=RUNNER_TOKEN, name="fake-cpu", brainthing_dir=None, heartbeat_seconds=3600)
        super().__init__(cfg)
        self.seen = []

    def stages_for(self, job):
        return ["captions"]

    def handle(self, job, reporter):
        reporter.stage("captions")
        for i, item in enumerate(job["items"], 1):
            reporter.video(i, item["video_id"], f"captions for {item['title']}")
            reporter.update(stage_percent=50, message="half way", force=True)
            self.seen.append(item["video_id"])
            reporter.client.item_result(
                job["id"], item["video_id"], {"status": "done", "summary": {"available": False}}
            )


def test_runner_protocol_end_to_end(client, yt, live):
    seed_channel(yt, n=2)
    cid = client.post("/api/channels", {"channel": "@alpha"}).json["channel"]["id"]
    ids = [v["id"] for v in client.get(f"/api/channels/{cid}/videos").json["videos"]]
    client.post("/api/discover/pull", {"video_ids": ids})

    runner = FakeCaptionsRunner(live)
    runner.serve(once=True)  # register, claim the captions job, run it, complete it
    assert sorted(runner.seen) == sorted(ids)

    job = db.session.scalar(db.select(Job).where(Job.job_type == "captions"))
    db.session.refresh(job)
    assert job.status == "done" and job.runner.name == "fake-cpu"
    assert [t["stage"] for t in job.stage_timings] == ["captions"]
    results = db.session.scalars(db.select(AnalysisResult).where(AnalysisResult.job_id == job.id)).all()
    assert {r.status for r in results} == {"done"}
    detail = client.get(f"/api/jobs/{job.id}").json["job"]
    assert detail["items_by_status"] == {"done": 2}
    assert "captions" in client.get(f"/api/jobs/{job.id}/log").json["log"]  # runner log lines arrive


def test_client_rejects_bad_token(live):
    from runners.common.client import ApiError

    c = PlatformClient(live, "wrong-token")
    with pytest.raises(ApiError) as err:
        c.register("x", "cpu", ["captions"])
    assert err.value.status == 401


def test_failure_is_reported_with_log_tail(client, yt, live):
    seed_channel(yt, n=1)
    cid = client.post("/api/channels", {"channel": "@alpha"}).json["channel"]["id"]
    ids = [v["id"] for v in client.get(f"/api/channels/{cid}/videos").json["videos"]]
    client.post("/api/discover/pull", {"video_ids": ids})

    class Boom(FakeCaptionsRunner):
        def handle(self, job, reporter):
            reporter.stage("captions")
            import logging

            logging.getLogger("runner").info("about to fail")
            raise RuntimeError("yt-dlp exploded")

    Boom(live).serve(once=True)
    job = db.session.scalar(db.select(Job).where(Job.job_type == "captions"))
    db.session.refresh(job)
    assert job.status == "failed" and "yt-dlp exploded" in job.error
    assert "about to fail" in client.get(f"/api/jobs/{job.id}/log").json["log"]

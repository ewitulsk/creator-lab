import pytest

from runners.common.config import read_env_file
from runners.common.runner import BaseRunner, JobCancelled, Reporter, RetryableError


class FakeClient:
    def __init__(self, cancel_after: int | None = None):
        self.calls: list[tuple] = []
        self.cancel_after = cancel_after
        self.runner_id = 1

    def progress(self, job_id, fields):
        self.calls.append(("progress", job_id, fields))
        n = sum(1 for c in self.calls if c[0] == "progress")
        return {"cancel": self.cancel_after is not None and n > self.cancel_after}

    def item_result(self, job_id, video_id, result):
        self.calls.append(("result", video_id, result))

    def complete(self, job_id):
        self.calls.append(("complete", job_id))

    def fail(self, job_id, error, log_tail="", retry=False):
        self.calls.append(("fail", job_id, error, retry))


JOB = {"id": 7, "type": "tribe_video", "items": [{"video_id": i} for i in range(1, 11)]}


def test_overall_percent_spans_stages_and_videos():
    r = Reporter(FakeClient(), JOB, ["Whisper", "Llama", "w2v-BERT", "V-JEPA2", "TRIBE"], min_interval=0)
    r.stage("Whisper")
    r.video(5, 105)
    r.update(stage_percent=1)
    last = r.client.calls[-1][2]
    assert last["stage"] == "Whisper" and last["video_index"] == 5 and last["video_count"] == 10
    assert last["stage_percent"] == 1.0
    assert last["overall_percent"] == pytest.approx(100 * (4 + 0.01) / 10 / 5, abs=0.01)
    r.stage("TRIBE")
    r.video(10, 110)
    r.update(stage_percent=100)
    assert r.client.calls[-1][2]["overall_percent"] == 100


def test_progress_is_rate_limited_but_forced_on_stage_change():
    client = FakeClient()
    r = Reporter(client, JOB, ["a", "b"], min_interval=60)
    r.stage("a")
    for p in range(10):
        r.update(stage_percent=p)
    assert len(client.calls) == 1
    r.stage("b")
    assert len(client.calls) == 2


def test_logs_ride_along_with_progress():
    client = FakeClient()
    r = Reporter(client, JOB, ["a"], min_interval=0)
    r.log("hello")
    r.update(stage_percent=1)
    assert client.calls[-1][2]["log"] == "hello"
    r.update(stage_percent=2)
    assert "log" not in client.calls[-1][2]


def test_cancel_from_platform_raises():
    r = Reporter(FakeClient(cancel_after=1), JOB, ["a"], min_interval=0)
    r.stage("a")
    with pytest.raises(JobCancelled):
        r.update(stage_percent=5)
    with pytest.raises(JobCancelled):
        r.update(stage_percent=6)


class _Runner(BaseRunner):
    def __init__(self, behaviour):
        self.client = FakeClient()
        self.current = None
        self.behaviour = behaviour

    def handle(self, job, reporter):
        reporter.stage("work")
        self.behaviour()


def test_run_one_completes_or_fails():
    ok = _Runner(lambda: None)
    ok.run_one(JOB)
    assert ok.client.calls[-1] == ("complete", 7)

    def boom():
        raise ValueError("bad input")

    bad = _Runner(boom)
    bad.run_one(JOB)
    assert bad.client.calls[-1][:2] == ("fail", 7) and "bad input" in bad.client.calls[-1][2]
    assert bad.client.calls[-1][3] is False

    def throttled():
        raise RetryableError("429")

    retry = _Runner(throttled)
    retry.run_one(JOB)
    assert retry.client.calls[-1][3] is True


def test_read_env_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# c\nA=1\nB = 'two'\nC=\"x=y\"\n\nbad line\n")
    assert read_env_file(p) == {"A": "1", "B": "two", "C": "x=y"}

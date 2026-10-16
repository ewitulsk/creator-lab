"""'Most replayed' capture from synthetic yt-dlp info dicts (yt-dlp never runs)."""

import json

from runners.common.runner import Reporter
from runners.cpu.heatmap import run_heatmaps
from runners.cpu.info import capture_from_info, save_info, scrub

from .test_runner_common import FakeClient

INFO = {
    "id": "abc",
    "title": "A video",
    "duration": 300,
    "view_count": 12345,
    "like_count": 600,
    "comment_count": 40,
    "http_headers": {"Cookie": "SECRET=1", "User-Agent": "x"},
    "cookies": "SID=abc",
    "formats": [{"format_id": "18", "http_headers": {"Cookie": "SECRET=2"}, "url": "https://example/1"}],
    "heatmap": [{"start_time": i * 3.0, "end_time": (i + 1) * 3.0, "value": round(1 - i / 100, 3)} for i in range(100)],
    "chapters": [
        {"start_time": 0.0, "end_time": 60.0, "title": "Intro"},
        {"start_time": 60.0, "end_time": 300.0, "title": "Main"},
    ],
}


def test_capture_extracts_heatmap_chapters_and_snapshot():
    cap = capture_from_info(INFO)
    assert len(cap["heatmap"]) == 100
    assert cap["heatmap"][0] == {"start_s": 0.0, "end_s": 3.0, "value": 1.0}
    assert [c["title"] for c in cap["chapters"]] == ["Intro", "Main"]
    assert cap["snapshot"]["view_count"] == 12345 and cap["snapshot"]["like_count"] == 600
    assert "http_headers" not in cap["snapshot"]


def test_capture_without_heatmap():
    cap = capture_from_info({"id": "x", "view_count": 3, "heatmap": None})
    assert cap["heatmap"] == [] and cap["chapters"] == []


def test_saved_info_never_contains_cookies_or_headers(tmp_path):
    path = save_info(INFO, tmp_path / "info.json")
    text = path.read_text()
    assert "SECRET" not in text and "SID=abc" not in text and "http_headers" not in text
    assert json.loads(text)["formats"][0]["format_id"] == "18"
    compact = json.loads(save_info(INFO, tmp_path / "compact.json", compact=True).read_text())
    assert "formats" not in compact and len(compact["heatmap"]) == 100
    assert scrub({"a": [{"cookies": 1, "b": 2}]}) == {"a": [{"b": 2}]}


def test_heatmap_job_reports_capture_without_downloading(tmp_path):
    client = FakeClient()
    job = {
        "id": 3,
        "type": "heatmap",
        "items": [
            {
                "video_id": 1,
                "external_id": "abc",
                "title": "A",
                "url": "u1",
                "output_dir": str(tmp_path / "a"),
                "status": "queued",
            },
            {
                "video_id": 2,
                "external_id": "def",
                "title": "B",
                "url": "u2",
                "output_dir": str(tmp_path / "b"),
                "status": "queued",
            },
        ],
    }
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        if url == "u2":
            raise RuntimeError("Video unavailable")
        return INFO

    reporter = Reporter(client, job, ["heatmap"], min_interval=0)
    reporter.stage("heatmap")
    run_heatmaps(job, reporter, fetch=fake_fetch)
    results = {c[1]: c[2] for c in client.calls if c[0] == "result"}
    assert fetched == ["u1", "u2"]
    assert results[1]["status"] == "done" and results[1]["summary"]["points"] == 100
    assert results[1]["capture"]["chapters"][1]["title"] == "Main"
    assert results[1]["assets"][0]["kind"] == "info"
    assert results[2]["status"] == "failed" and "unavailable" in results[2]["error"]
    assert not list(tmp_path.rglob("*.mp4"))

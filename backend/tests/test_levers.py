"""Gemini lever scores, all against a fake client: no key needed, no network, no ffmpeg."""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

import pytest

from creator_lab.extensions import db
from creator_lab.levers import config as lcfg
from creator_lab.levers import gemini, media, rubric, service, worker
from creator_lab.models import (
    Asset,
    Channel,
    Job,
    LeverCall,
    LeverMoment,
    LeverPairwise,
    LeverProbe,
    LeverScore,
    LeverSubscore,
    Video,
)

from .fake_gemini import FakeLeverClient, score_response

KEY = "test-gemini-key-NOT-REAL-7f3a9c"
TITLE = "My Secret Title About Fireworks"
CHANNEL = "Unmistakable Channel Name"


@pytest.fixture
def fake(app, monkeypatch, tmp_path):
    r = rubric.load()
    client = FakeLeverClient(r)
    gemini.set_client_factory(client)

    def fake_copy(src, dest_dir, height, fps, run=None):
        cmd, out = media.blind_copy_command(src, dest_dir, height, fps)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"low-res")
        client.copy_cmds = getattr(client, "copy_cmds", []) + [cmd]
        return out

    monkeypatch.setattr(service.media, "make_blind_copy", fake_copy)
    yield client
    gemini.set_client_factory(None)


@pytest.fixture
def keyed(app):
    app.config["APP"].gemini_api_key = KEY
    lcfg.update_config({"min_seconds_between_calls": 0})
    yield
    app.config["APP"].gemini_api_key = None


def _video(app, n=1, duration=120, downloaded=True) -> list[int]:
    data_root = app.config["APP"].data_root
    ch = db.session.scalar(db.select(Channel).where(Channel.title == CHANNEL))
    if ch is None:
        ch = Channel(platform_id=1, external_id="UC" + "q" * 22, title=CHANNEL, handle="@unmistakable")
        db.session.add(ch)
        db.session.flush()
    ids = []
    for i in range(n):
        v = Video(channel_id=ch.id, platform_id=1, external_id=f"vid{i:08d}", title=f"{TITLE} {i}",
                  duration_s=duration, format="long", selection="tracked")  # fmt: skip
        db.session.add(v)
        db.session.flush()
        if downloaded:
            p = Path(data_root) / "media" / ch.external_id / v.external_id / "video.mp4"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"not really a video")
            db.session.add(Asset(video_id=v.id, kind="video", path=p.relative_to(data_root).as_posix()))
        ids.append(v.id)
    db.session.commit()
    return ids


def _run_all():
    while worker.process_next_job(sleep=lambda s: None):
        pass


# ------------------------------------------------------------------ config and key handling


def test_without_a_key_the_analyzer_is_unavailable(client, app, fake):
    ids = _video(app)
    cfg = client.get("/api/levers/config").json
    assert cfg["key_set"] is False and cfg["config"]["input_mode"] == "upload"
    assert cfg["config"]["model"] == "gemini-4-argon"
    r = client.post("/api/levers/score", {"video_ids": ids})
    assert r.status_code == 400 and "GEMINI_API_KEY" in r.json["error"]
    assert client.post("/api/levers/test").status_code == 400
    assert worker.process_next_job() is None and fake.calls == []


def test_config_validation_and_model_switch(client, keyed):
    r = client.put("/api/levers/config", {"model": "gemini-3.8-flash", "upload_height": 240, "temperature": 0.4})
    assert r.status_code == 200 and r.json["config"]["model"] == "gemini-3.8-flash"
    assert r.json["config"]["upload_height"] == 240
    assert client.put("/api/levers/config", {"model": "gpt-9"}).status_code == 400
    assert client.put("/api/levers/config", {"upload_height": 4000}).status_code == 400
    assert client.put("/api/levers/config", {"input_mode": "magic"}).status_code == 400


def test_test_connection_checks_the_model(client, keyed, fake):
    r = client.post("/api/levers/test", {"model": "gemini-3.8-flash"})
    assert r.json["ok"] and fake.checked == ["gemini-3.8-flash"]
    assert fake.api_key == KEY


def test_the_key_is_never_returned_or_logged(client, app, keyed, fake, caplog):
    caplog.set_level(logging.DEBUG)
    ids = _video(app)
    fake.fail_with = gemini.GeminiError(f"401 bad key {KEY}")
    responses = [client.get("/api/levers/config"), client.post("/api/levers/score", {"video_ids": ids})]
    _run_all()
    responses += [client.get(f"/api/videos/{ids[0]}/levers"), client.get("/api/jobs"), client.post("/api/levers/test")]
    for r in responses:
        assert KEY not in r.get_data(as_text=True)
    assert KEY not in caplog.text
    errors = [c.error for c in db.session.scalars(db.select(LeverCall))] + [
        j.error for j in db.session.scalars(db.select(Job))
    ]
    assert errors and all(KEY not in (e or "") for e in errors)
    assert any("***" in (e or "") for e in errors)


# ------------------------------------------------------------------ scoring


def test_scoring_stores_runs_aggregate_moments_probe_and_cost(client, app, keyed, fake):
    ids = _video(app, n=2)
    est = client.post("/api/levers/estimate", {"video_ids": ids}).json["estimate"]
    assert est["videos"] == 2 and est["calls"] == 8 and est["cost_usd"] > 0
    r = client.post("/api/levers/score", {"video_ids": ids})
    job_id = r.json["job"]["id"]
    _run_all()
    job = db.session.get(Job, job_id)
    assert job.status == "done", job.error
    assert job.params["config"]["model"] == "gemini-4-argon"  # frozen at issue time
    v = ids[0]
    runs = db.session.scalars(
        db.select(LeverScore).where(LeverScore.video_id == v, LeverScore.run_idx.isnot(None))
    ).all()
    assert len(runs) == 3 * 6
    agg = {
        s.lever: s
        for s in db.session.scalars(db.select(LeverScore).where(LeverScore.video_id == v, LeverScore.run_idx.is_(None)))
    }
    assert set(agg) == set(rubric.LEVERS)
    att = agg["attention"]
    assert att.score == 3 and att.spread == 3 and "high_spread" in att.flags  # runs 2, 3, 5
    assert agg["emotion"].spread == 0 and "high_spread" not in agg["emotion"].flags
    assert {s.input_mode for s in runs} == {"upload"} and att.model == "gemini-4-argon"
    assert att.rubric_hash == rubric.load().hash and att.method == "gemini_agentic"
    subs = {s.item for s in db.session.scalars(db.select(LeverSubscore).where(LeverSubscore.score_id == att.id))}
    assert {"hook_strength", "curiosity_gap", "pacing", "hook_score"} <= subs
    kinds = {m.kind for m in db.session.scalars(db.select(LeverMoment).where(LeverMoment.video_id == v))}
    assert kinds == {"elicited", "evidence"}
    assert db.session.scalar(db.select(LeverProbe).where(LeverProbe.video_id == v)).contaminated is False
    # 3 runs + 1 probe per video, all costed, uploads deleted afterwards
    calls = db.session.scalars(db.select(LeverCall).where(LeverCall.job_id == job_id)).all()
    assert len(calls) == 8 and all(c.cost_usd > 0 and c.raw_path for c in calls)
    assert len(fake.uploads) == 2 and sorted(fake.deleted) == ["files/1", "files/2"]
    cost = client.get(f"/api/levers/jobs/{job_id}/cost").json
    assert cost["actual"]["calls"] == 8 and cost["estimate"]["videos"] == 2
    # 100k in + 3k out(+thinking) per call at Argon prices
    assert cost["actual"]["cost_usd"] == pytest.approx(8 * lcfg.cost_usd("gemini-4-argon", 100_000, 2_000, 1_000))
    body = client.get(f"/api/videos/{v}/levers").json
    assert body["scorings"][0]["aggregate"]["attention"]["spread"] == 3
    assert set(body["scorings"][0]["runs"]) == {"1", "2", "3"}
    assert body["probe"]["identified"] is False


def test_prompts_are_blinded_and_the_default_sends_no_url(client, app, keyed, fake):
    ids = _video(app)
    client.post("/api/levers/score", {"video_ids": ids})
    _run_all()
    assert fake.calls
    for call in fake.calls:
        for p in call["parts"]:
            if p["kind"] == "text":
                assert TITLE.lower() not in p["text"].lower() and CHANNEL.lower() not in p["text"].lower()
                assert "unmistakable" not in p["text"].lower()
            else:
                assert "youtube.com" not in p["uri"]
        assert call["agentic"] is True
    cmd = fake.copy_cmds[0]
    assert cmd[cmd.index("-map_metadata") + 1] == "-1" and "-map_chapters" in cmd
    assert "scale=-2:'min(360,ih)',fps=2" in cmd
    out = Path(cmd[-1]).name
    assert "vid" not in out and "Fireworks" not in out and len(out) == len("0" * 24 + ".mp4")


def test_resume_skips_scored_videos_until_model_or_rubric_changes(client, app, keyed, fake):
    ids = _video(app)
    client.post("/api/levers/score", {"video_ids": ids})
    _run_all()
    n = len(fake.calls)
    r = client.post("/api/levers/score", {"video_ids": ids})
    assert r.json["job"] is None and r.json["estimate"]["already_scored"] == 1
    client.put("/api/levers/config", {"model": "gemini-3.8-flash"})
    r = client.post("/api/levers/score", {"video_ids": ids})
    assert r.json["job"] is not None
    _run_all()
    assert len(fake.calls) == n + 4 and fake.calls[-1]["model"] == "gemini-3.8-flash"


def test_bad_json_is_retried_once_then_fails(client, app, keyed, fake):
    ids = _video(app, n=2)
    good = score_response(fake.r)
    bad_scale = score_response(fake.r, base=7)
    fake.score_queue = ["not json at all", good, good, good, bad_scale, "{}"]
    r = client.post("/api/levers/score", {"video_ids": ids})
    _run_all()
    job = db.session.get(Job, r.json["job"]["id"])
    status = {i.video_id: i.status for i in job.items}
    assert status[ids[0]] == "done"  # the bad first answer was retried
    assert status[ids[1]] == "failed" and "invalid score response twice" in job.items[1].error


def test_rate_limits_back_off_and_retry(client, app, keyed, fake):
    ids = _video(app)
    fake.score_queue = [gemini.RetryableGeminiError("429 RESOURCE_EXHAUSTED")]
    client.post("/api/levers/score", {"video_ids": ids})
    _run_all()
    assert (
        db.session.scalar(db.select(db.func.count()).select_from(LeverScore).where(LeverScore.run_idx.is_(None))) == 6
    )


def test_youtube_url_mode_and_fallback(client, app, keyed, fake):
    ids = _video(app, n=2)
    client.put("/api/levers/config", {"input_mode": "youtube_url"})
    client.post("/api/levers/score", {"video_ids": [ids[0]]})
    _run_all()
    urls = [p["uri"] for c in fake.calls for p in c["parts"] if p["kind"] == "video"]
    assert urls and all("youtube.com/watch?v=vid00000000" in u for u in urls) and not fake.uploads
    row = db.session.scalar(db.select(LeverScore).where(LeverScore.video_id == ids[0], LeverScore.run_idx.is_(None)))
    assert row.input_mode == "youtube_url" and "not_blinded_url_input" in row.flags
    # Gemini rejects the URL: fall back to the blinded upload and record that
    fake.reject_urls = True
    client.post("/api/levers/score", {"video_ids": [ids[1]]})
    _run_all()
    row = db.session.scalar(db.select(LeverScore).where(LeverScore.video_id == ids[1], LeverScore.run_idx.is_(None)))
    assert row.input_mode == "upload" and "url_rejected_fell_back_to_upload" in row.flags
    assert len(fake.uploads) == 1 and fake.deleted == ["files/1"]


def test_contamination_probe_flags_scores(client, app, keyed, fake):
    ids = _video(app)
    fake.probe = {"identified": True, "creator": "unmistakable channel name", "title": "?", "confidence": "high"}
    client.post("/api/levers/score", {"video_ids": ids})
    _run_all()
    probe = db.session.scalar(db.select(LeverProbe))
    assert probe.identified and probe.contaminated
    rows = db.session.scalars(db.select(LeverScore)).all()
    assert all("possibly_contaminated" in r.flags for r in rows)


def test_contamination_needs_a_correct_guess(app):
    ids = _video(app)
    v = db.session.get(Video, ids[0])
    wrong = {"identified": True, "creator": "MrBeast", "title": "Squid game", "confidence": "low"}
    title = {"identified": True, "creator": "unknown", "title": f"{TITLE} 0", "confidence": "med"}
    assert not service.is_contaminated(wrong, v)
    assert service.is_contaminated(title, v)
    assert not service.is_contaminated({**title, "identified": False}, v)


def test_validation_rejects_out_of_range_and_missing():
    r = rubric.load()
    good = score_response(r)
    assert rubric.validate_scores(good, r)["hook_score"] == 4
    bad = score_response(r, base=6)
    with pytest.raises(rubric.InvalidResponse):
        rubric.validate_scores(bad, r)
    missing = score_response(r)
    del missing["levers"]["shareable"]["subscores"]["triggers"]
    with pytest.raises(rubric.InvalidResponse):
        rubric.validate_scores(missing, r)
    with pytest.raises(rubric.InvalidResponse):
        rubric.validate_probe({"identified": "yes"})


def test_rubric_hash_and_prompt():
    r = rubric.load()
    assert len(r.hash) == 64 and r.version == 1
    prompt = rubric.score_prompt(r)
    for lv in rubric.LEVERS:
        assert f"## {lv}" in prompt
    schema = rubric.score_schema(r)
    assert schema["properties"]["levers"]["required"] == list(rubric.LEVERS)


# ------------------------------------------------------------------ pairwise, A/B, coverage, export


def test_pairwise_both_orders(client, app, keyed, fake):
    a, b = _video(app, n=2)
    r = client.post("/api/levers/pairwise", {"pairs": [[a, b]]})
    assert r.status_code == 200, r.json
    _run_all()
    rows = db.session.scalars(db.select(LeverPairwise)).all()
    assert len(rows) == 12 and {x.order for x in rows} == {"ab", "ba"}
    ab = [x for x in rows if x.order == "ab"]
    ba = [x for x in rows if x.order == "ba"]
    assert all(x.video_a == a and x.winner_video_id == a for x in ab)  # "A" = shown first
    assert all(x.video_a == b and x.winner_video_id == b for x in ba)
    assert len(client.get(f"/api/levers/pairwise?video_id={a}").json["rows"]) == 12
    assert client.post("/api/levers/pairwise", {"pairs": [[a, a]]}).status_code == 400


def test_ab_scores_both_ways(client, app, keyed, fake):
    ids = _video(app)
    r = client.post("/api/levers/ab", {"video_ids": ids})
    assert r.status_code == 200
    _run_all()
    modes = {s.input_mode for s in db.session.scalars(db.select(LeverScore).where(LeverScore.run_idx.is_(None)))}
    assert modes == {"upload", "youtube_url"}
    rows = client.get(f"/api/levers/ab?video_ids={ids[0]}").json["rows"]
    assert len(rows) == 6 and all(x["difference"] is not None for x in rows)
    assert client.post("/api/levers/ab", {"video_ids": list(range(1, 8))}).status_code == 400


def test_coverage_export_and_study_store(client, app, keyed, fake):
    ids = _video(app)
    client.post("/api/levers/score", {"video_ids": ids})
    _run_all()
    cov = client.get("/api/coverage?selection=all").json
    keys = [c["key"] for c in cov["lever_columns"]]
    assert keys[0] == "attention:gemini_agentic" and len(keys) == 6
    row = next(v for v in cov["videos"] if v["video_id"] == ids[0])
    assert row["levers"]["attention:gemini_agentic"]["spread"] == 3
    text = client.get("/api/exports/levers.csv").get_data(as_text=True)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 6 * 4 and {"aggregate", "run"} == {r["kind"] for r in rows}
    assert "sub_hook_strength" in rows[0]
    from creator_lab.study_service import DBStore

    agg = DBStore().lever_scores([ids[0]])
    assert len(agg) == 6 and all(r["kind"] == "aggregate" for r in agg)

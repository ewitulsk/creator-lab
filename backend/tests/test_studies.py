"""Studies framework and both studies, on synthetic data (planted effects the tests must find)."""

import datetime as dt
import json

import numpy as np
import pytest

from creator_lab import study_service as svc
from creator_lab.analyzers import current
from creator_lab.captures import store_capture
from creator_lab.extensions import db
from creator_lab.models import (
    AnalysisResult,
    Channel,
    Job,
    OwnerSync,
    RetentionPoint,
    Study,
    StudyVideo,
    Video,
    utcnow,
)

NETS = ["visual", "auditory", "language", "default_mode", "attention"]


@pytest.fixture(autouse=True)
def results_root(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "RESULTS_ROOT", tmp_path / "results")


def add_channel(title, is_mine=False, owned=False):
    ch = Channel(platform_id=1, external_id=f"UC{title:x<22}"[:24], title=title, is_mine=is_mine, owned=owned)
    db.session.add(ch)
    db.session.flush()
    return ch


def add_video(ch, key, days_ago, score, duration=600, fmt="long"):
    v = Video(
        channel_id=ch.id,
        platform_id=1,
        external_id=key,
        title=f"video {key}",
        published_at=utcnow() - dt.timedelta(days=days_ago),
        duration_s=duration,
        format=fmt,
        views=int(1000 * score),
        outlier_score=score,
        selection="discovered",
    )
    db.session.add(v)
    db.session.flush()
    return v


def put_result(app, v, analyzer, summary=None, files=None, output=None):
    a = current(analyzer)
    folder = app.config["APP"].data_root / "analyzers" / analyzer / "1" / v.external_id
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in (files or {}).items():
        if name.endswith(".npz"):
            np.savez(folder / name, **content)
        else:
            (folder / name).write_text(json.dumps(content))
    rel = f"analyzers/{analyzer}/1/{v.external_id}" + (f"/{output}" if output else "")
    db.session.add(
        AnalysisResult(video_id=v.id, analyzer_id=a.id, status="done", output_path=rel, summary=summary or {})
    )


def virality_world(app, n_channels=4, pairs_per=2, effect=1.0):
    rng = np.random.default_rng(0)
    for c in range(n_channels):
        ch = add_channel(f"mine{c}", is_mine=True)
        for p in range(pairs_per):
            base = 100 + 60 * p
            hit = add_video(ch, f"c{c}h{p}", base, 3.0 + p)
            flop = add_video(ch, f"c{c}f{p}", base + 10, 0.3)
            add_video(ch, f"c{c}m{p}", base + 5, 1.0)  # ordinary video, not selected
            for v, is_hit in ((hit, True), (flop, False)):
                curves = {
                    n: (rng.normal(size=60) + (effect if (is_hit and n == "attention") else 0)).tolist() for n in NETS
                }
                emb = {
                    "text": rng.normal(size=(2, 8)),
                    "audio": rng.normal(size=(2, 8)),
                    "video": rng.normal(size=(2, 8)),
                }
                put_result(
                    app,
                    v,
                    "tribe_video",
                    files={"networks.json": {"t": list(range(60)), "networks": curves}, "embeddings.npz": emb},
                )
                put_result(
                    app,
                    v,
                    "tribe_engagement",
                    {
                        "mean_engagement_index": float(rng.normal() + (effect if is_hit else 0)),
                        "mean_engagement_index_first_30s": float(rng.normal()),
                        "mean_stimulus_drive": float(rng.normal()),
                    },
                )
                put_result(
                    app,
                    v,
                    "visual_features",
                    {
                        "cuts_per_minute": float(rng.normal()),
                        "mean_motion": float(rng.normal()),
                        "mean_colorfulness": 1.0,
                        "mean_brightness": float(rng.normal()),
                        "mean_faces": 0.5,
                        "duration_s": 600.0,
                    },
                )
                put_result(
                    app,
                    v,
                    "audio_features",
                    {
                        "integrated_lufs": float(rng.normal()),
                        "speech_rate_wpm": 150.0,
                        "mean_music_score": float(rng.normal()),
                    },
                )
    # One channel that isn't Evan's: never sampled.
    other = add_channel("other")
    add_video(other, "oh", 100, 9.0)
    add_video(other, "of", 105, 0.1)
    db.session.commit()


def study(slug):
    return db.session.scalar(db.select(Study).where(Study.slug == slug))


def test_studies_are_synced_as_drafts(client):
    rows = client.get("/api/studies").json["studies"]
    assert {s["slug"] for s in rows} == {"tribe-virality", "tribe-retention"}
    assert all(s["status"] == "draft" for s in rows)
    s = client.get(f"/api/studies/{rows[0]['id']}").json["study"]
    assert s["methodology_md"].startswith("## ") and s["measures"] and s["requires"]


def test_virality_selects_pairs_within_channels(client, app):
    virality_world(app)
    sid = study("tribe-virality").id
    r = client.post(f"/api/studies/{sid}/select")
    assert r.status_code == 200, r.json
    assert r.json["stats"]["pairs"] == 8 and "8 pairs from 4 channel(s)" in r.json["rules"]
    members = db.session.scalars(db.select(StudyVideo).where(StudyVideo.study_id == sid)).all()
    roles = {m.video.external_id: m.role for m in members}
    assert roles["c0h0"] == "hit" and roles["c0f0"] == "flop" and "c0m0" not in roles and "oh" not in roles
    groups = {}
    for m in members:
        groups.setdefault(m.group, set()).add(m.video.channel_id)
    assert all(len(chs) == 1 for chs in groups.values())  # pairs never cross channels
    detail = client.get(f"/api/studies/{sid}").json
    assert detail["completeness"]["requirements"][0] == "media@1"
    assert detail["completeness"]["members"][0]["requirements"]["tribe_video@1"] == "done"


def test_lock_drift_and_new_version(client, app, monkeypatch):
    sid = study("tribe-virality").id
    r = client.post(f"/api/studies/{sid}/lock")
    assert r.json["study"]["status"] == "locked"
    s = db.session.get(Study, sid)
    assert len(s.locked_hash) == 64 and s.locked_by == "admin"
    assert client.post(f"/api/studies/{sid}/lock").status_code == 409
    assert client.get(f"/api/studies/{sid}").json["study"]["drift"]["changed"] is False
    monkeypatch.setattr(svc, "module_hash", lambda name: "0" * 64)  # the module's source changed
    assert client.get(f"/api/studies/{sid}").json["study"]["drift"]["changed"] is True
    assert client.post(f"/api/studies/{sid}/select").status_code == 409
    assert client.post(f"/api/studies/{sid}/analyze").status_code == 409
    v2 = client.post(f"/api/studies/{sid}/new-version").json["study"]
    assert v2["version"] == 2 and v2["status"] == "draft"


def test_fill_gaps_pulls_and_issues_jobs(client, app, yt):
    ch = add_channel("mine", is_mine=True)
    hit = add_video(ch, "fh", 100, 4.0)
    flop = add_video(ch, "ff", 105, 0.2)
    db.session.commit()
    sid = study("tribe-virality").id
    client.post(f"/api/studies/{sid}/select")
    r = client.post(f"/api/studies/{sid}/fill-gaps")
    assert r.status_code == 200, r.json
    assert r.json["pulled"] == 2
    assert (
        db.session.get(Video, hit.id).selection == "tracked" and db.session.get(Video, flop.id).selection == "tracked"
    )
    types = {j.job_type for j in db.session.scalars(db.select(Job))}
    assert {"media", "captions", "tribe_video", "tribe_title", "tribe_engagement", "visual_features"} <= types
    eng = db.session.scalar(db.select(Job).where(Job.job_type == "tribe_engagement"))
    tribe = db.session.scalar(db.select(Job).where(Job.job_type == "tribe_video"))
    assert tribe.id in eng.depends_on  # engagement waits for TRIBE, which waits for the download


def test_virality_analysis_finds_the_planted_effect(client, app):
    virality_world(app, n_channels=5, pairs_per=2, effect=2.5)
    sid = study("tribe-virality").id
    client.post(f"/api/studies/{sid}/select")
    s = db.session.get(Study, sid)
    row = svc.analyze(s)
    res = row.results
    assert res["pairs_complete"] == 10
    att = res["tests"]["net_attention_mean"]
    assert att["wilcoxon"]["median_diff"] > 1 and att["wilcoxon"]["p"] < 0.05 and att["sign_flip"]["p"] < 0.05
    assert att["wilcoxon"]["p_holm"] >= att["wilcoxon"]["p"]
    vis = res["tests"]["net_visual_mean"]["wilcoxon"]["p"]
    assert vis > 0.01  # no planted effect there
    assert res["models"]["brain"]["auc"] > 0.8
    assert set(res["comparison"]) == {"brain_vs_cheap", "brain_vs_prebrain"}
    figs = client.get(f"/api/studies/{sid}").json["results"][0]["figures"]
    assert figs and client.get(figs[0]).status_code == 200
    assert (svc.RESULTS_ROOT / "tribe-virality" / "v1" / "results.json").exists()
    assert s.status == "draft"  # analysing a draft doesn't move it to analyzed
    meta = json.loads((svc.RESULTS_ROOT / "tribe-virality" / "v1" / "results.json").read_text())["meta"]
    assert meta["pre_registered"] is False and meta["code_version"]


def retention_world(app, n_owned=6, effect=1.0):
    rng = np.random.default_rng(3)
    owned = add_channel("evan", is_mine=True, owned=True)
    public = add_channel("ludwig")
    sync = OwnerSync(channel_id=owned.id, mode="full", status="done", started_by="t", counts={}, log=[])
    db.session.add(sync)
    db.session.flush()
    for i in range(n_owned):
        v = add_video(owned, f"own{i}", 50 + i, 1.0, duration=400)
        t = np.arange(400)
        signal = np.sin(t / 15 + i)  # what TRIBE "sees"
        ratios = np.arange(1, 101) / 100
        sig_at = np.interp(ratios * 400, t, signal)
        watch = 0.8 * np.exp(-1.5 * ratios) + 0.15 + effect * 0.03 * sig_at + rng.normal(scale=0.002, size=100)
        for r, w in zip(ratios, watch, strict=True):
            db.session.add(
                RetentionPoint(
                    video_id=v.id,
                    sync_id=sync.id,
                    audience_type="ALL",
                    elapsed_ratio=float(r),
                    audience_watch_ratio=float(w),
                    relative_retention_performance=0.5,
                    video_duration_s=400,
                )
            )
        put_result(
            app,
            v,
            "tribe_engagement",
            {"mean_engagement_index": 0.0},
            files={
                "engagement.json": {
                    "t": t.tolist(),
                    "curves": {
                        "engagement_index": signal.tolist(),
                        "stimulus_drive": rng.normal(size=400).tolist(),
                        "anterior_insula": (-signal).tolist(),
                    },
                    "yeo7": {},
                }
            },
            output="engagement.json",
        )
        put_result(
            app,
            v,
            "visual_features",
            files={"visual_features.json": {"series": {"t": t.tolist(), "cut": rng.integers(0, 2, 400).tolist()}}},
            output="visual_features.json",
        )
        put_result(
            app,
            v,
            "audio_features",
            files={
                "audio_features.json": {"series": {"t": t.tolist(), "loudness_lufs": rng.normal(-20, 2, 400).tolist()}}
            },
            output="audio_features.json",
        )
        # The public heatmap tracks real retention on owned videos (a decent proxy here).
        store_capture(
            v,
            {
                "heatmap": [
                    {
                        "start_s": k * 4.0,
                        "end_s": (k + 1) * 4.0,
                        "value": float(np.interp((k + 0.5) / 100, ratios, watch)),
                    }
                    for k in range(100)
                ]
            },
            None,
            "heatmap",
        )
    for i in range(3):
        v = add_video(public, f"pub{i}", 40 + i, 1.0, duration=300)
        store_capture(
            v,
            {
                "heatmap": [
                    {"start_s": k * 3.0, "end_s": (k + 1) * 3.0, "value": float(rng.random())} for k in range(100)
                ]
            },
            None,
            "heatmap",
        )
    add_video(public, "pub_nohm", 30, 1.0, duration=300)
    db.session.commit()
    return owned, public


def test_retention_criteria_and_discover_filter(client, app):
    owned, public = retention_world(app)
    sid = study("tribe-retention").id
    r = client.post(f"/api/studies/{sid}/select").json
    assert r["stats"] == {"owned": 6, "public": 3}
    d = client.get(f"/api/discover/channels/{public.id}/videos?study_id={sid}").json
    roles = {v["external_id"]: (v["role"], v["group"]) for v in d["videos"]}
    assert roles["pub0"] == ("public", "ludwig") and "pub_nohm" in roles  # needs a heatmap capture first
    assert "1 more video in this channel needs data first" in d["study_rules"]


def test_retention_analysis_and_heatmap_proxy(client, app):
    retention_world(app, effect=1.0)
    sid = study("tribe-retention").id
    client.post(f"/api/studies/{sid}/select")
    res = svc.analyze(db.session.get(Study, sid)).results
    assert res["n_owned"] == 6 and res["n_public"] == 3
    eng = res["owned"]["brain"]["engagement_index|residual|lag0"]
    assert eng["mean_rho"] > 0.5 and eng["positive"] == 6 and eng["p_perm"] < 0.05
    assert res["owned"]["controls"]["cuts|residual|lag0"]["mean_rho"] < 0.3
    assert res["anterior_insula_direction"]["as_expected"] is True
    assert res["heatmap_vs_real_retention"]["watch"]["mean_rho"] > 0.9
    assert res["owned"]["ridge_brain"]["mean_heldout_rho"] is not None
    assert "engagement_index|heatmap|lag0" in res["public_heatmap"]["brain"]


def test_pull_into_study_and_exports(client, app, yt):
    from .fake_youtube import seed_channel

    seed_channel(yt, n=3)
    cid = client.post("/api/channels", {"channel": "@alpha", "is_mine": True}).json["channel"]["id"]
    ids = [v["id"] for v in client.get(f"/api/channels/{cid}/videos").json["videos"]]
    sid = study("tribe-virality").id
    r = client.post("/api/discover/pull", {"video_ids": ids[:2], "study_id": sid}).json
    assert r["study_added"] == 2
    members = db.session.scalars(db.select(StudyVideo).where(StudyVideo.study_id == sid)).all()
    assert {m.role for m in members} == {"manual"}
    csv_text = client.get(f"/api/studies/{sid}/members.csv").get_data(as_text=True)
    assert csv_text.splitlines()[0].startswith("study,study_version,video_id") and len(csv_text.splitlines()) == 3
    pq = client.get(f"/api/studies/{sid}/members.parquet")
    assert pq.status_code == 200 and pq.data[:4] == b"PAR1"
    # Re-selecting keeps manual members.
    client.post(f"/api/studies/{sid}/select")
    assert db.session.scalar(db.select(db.func.count()).select_from(StudyVideo).where(StudyVideo.role == "manual")) == 2

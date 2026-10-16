import datetime as dt

import pytest

from creator_lab import ingest
from creator_lab.analyzers.builtin import History, outlier_scores
from creator_lab.extensions import db
from creator_lab.models import AnalysisResult, ApiUsage, Asset, Channel, Video, VideoSnapshot, utcnow
from creator_lab.settings import update_settings
from creator_lab.youtube.quota import spend

from .fake_youtube import CID, CID2, seed_channel


def count(model) -> int:
    return db.session.scalar(db.select(db.func.count()).select_from(model))


def test_add_channel_lists_uploads_without_tracking(client, yt, app):
    seed_channel(yt)
    yt.add_video(CID, "short1", utcnow() - dt.timedelta(days=3), 50_000, "PT40S", vertical=True, short=True)
    yt.add_video(CID, "vert1", utcnow() - dt.timedelta(days=2), 500, "PT2M", vertical=True, short=False)
    yt.add_video(CID, "live1", utcnow() - dt.timedelta(days=1), 900, "PT2H", live=True)

    r = client.post("/api/channels", {"channel": "https://www.youtube.com/@alpha", "is_mine": True})
    assert r.status_code == 201, r.json
    ch = r.json["channel"]
    assert ch["external_id"] == CID and ch["is_mine"] is True
    assert ch["followed"] is False and ch["auto_track_new"] is False

    videos = {v.external_id: v for v in db.session.scalars(db.select(Video))}
    assert len(videos) == 15
    assert {v.selection for v in videos.values()} == {"discovered"}
    assert videos["short1"].format == "short"
    assert videos["vert1"].format == "long"
    assert videos["live1"].format == "live"
    assert videos["aaaav00"].duration_s == 600
    assert videos["aaaav00"].aspect == pytest.approx(1.7778, rel=1e-3)
    assert count(VideoSnapshot) == 15
    # Browsing never writes media files.
    assert count(Asset) == 0
    assert not (app.config["APP"].data_root / "media" / CID).exists()

    assert db.session.scalar(db.select(ApiUsage)).units >= 4
    assert not any(c.endswith("/search") for c in yt.calls)

    # Old long-form videos are settled: the hits (5000) sit at 5x the 1000-view baseline.
    hit = videos["aaaav04"]
    assert hit.outlier_score == pytest.approx(5.0)
    assert videos["aaaav01"].outlier_score == pytest.approx(1.0)
    assert videos["live1"].outlier_score is None

    listing = client.get(f"/api/channels/{ch['id']}/videos?sort=outlier_score").json
    assert listing["total"] == 15
    assert listing["videos"][0]["outlier_score"] == pytest.approx(5.0)

    detail = client.get(f"/api/videos/{hit.id}").json
    assert detail["video"]["channel"]["id"] == ch["id"]
    statuses = {a["analyzer"]: a["status"] for a in detail["analyzers"]}
    assert statuses["outlier_score"] == "done" and statuses["views_at_age"] == "done"

    channels = client.get("/api/channels").json["channels"]
    assert channels[0]["video_count"] == 15 and channels[0]["tracked_count"] == 0
    assert channels[0]["videos_by_format"] == {"long": 13, "short": 1, "live": 1}
    assert channels[0]["subscribers"] == 1000


def test_adding_twice_is_idempotent(client, yt):
    seed_channel(yt, n=2)
    assert client.post("/api/channels", {"channel": "@alpha"}).status_code == 201
    r = client.post("/api/channels", {"channel": CID})
    assert r.status_code == 200 and r.json["created"] is False
    assert count(Channel) == 1


def test_unknown_channel_is_404(client, yt):
    assert client.post("/api/channels", {"channel": "@nobody"}).status_code == 404


def test_search_costs_100_units_only_for_names(client, yt):
    seed_channel(yt, n=1)
    seed_channel(yt, cid=CID2, handle="@beta", n=1)
    r = client.get("/api/discover/search?q=alpha")
    assert [c["external_id"] for c in r.json["channels"]] == [CID]
    assert r.json["channels"][0]["subscribers"] == 1000
    assert db.session.scalar(db.select(ApiUsage)).units == 101  # search + channels.list

    r = client.get("/api/discover/search?q=@beta")  # a handle resolves without search.list
    assert [c["external_id"] for c in r.json["channels"]] == [CID2]
    assert sum(1 for c in yt.calls if c.endswith("/search")) == 1


def test_discover_filters(client, yt):
    seed_channel(yt, n=12, start_days_ago=400)
    for i in range(6):
        yt.add_video(CID, f"s{i}", utcnow() - dt.timedelta(days=300 - i), 9000, "PT30S", vertical=True, short=True)
    yt.add_video(CID, "live1", utcnow() - dt.timedelta(days=5), 900, "PT2H", live=True)
    cid = client.post("/api/channels", {"channel": "@alpha"}).json["channel"]["id"]
    url = f"/api/discover/channels/{cid}/videos"

    r = client.get(url).json
    assert r["matching"] == 18  # live excluded by default
    assert r["listing"]["fresh"] is True
    assert client.get(url + "?include_live=1").json["matching"] == 19

    top = client.get(url + "?formats=long&pick=top&n=2&rank=outlier").json["videos"]
    assert [v["outlier_score"] for v in top] == [pytest.approx(5.0)] * 2
    bottom = client.get(url + "?formats=long&pick=bottom&n=3&rank=views").json["videos"]
    assert all(v["views"] == 1000 for v in bottom)

    assert client.get(url + "?formats=short").json["matching"] == 6
    assert client.get(url + "?timeframe=30d").json["matching"] == 0
    window = client.get(url + "?timeframe=custom&since=2000-01-01&until=2100-01-01&min_duration=60").json
    assert window["matching"] == 12
    assert client.get(url + "?timeframe=nope").status_code == 400


def test_pull_tracks_and_follows(client, yt, app):
    seed_channel(yt, n=4)
    cid = client.post("/api/channels", {"channel": "@alpha"}).json["channel"]["id"]
    vids = [v["id"] for v in client.get(f"/api/channels/{cid}/videos").json["videos"][:2]]
    before = count(VideoSnapshot)

    r = client.post("/api/discover/pull", {"video_ids": vids})
    assert r.status_code == 200, r.json
    assert r.json["pulled"] == 2 and r.json["already_tracked"] == 0

    tracked = db.session.scalars(db.select(Video).where(Video.selection == "tracked")).all()
    assert {v.id for v in tracked} == set(vids)
    assert all(v.tracked_at for v in tracked)
    assert db.session.get(Channel, cid).followed is True
    assert count(VideoSnapshot) == before + 2
    title = db.session.scalar(db.select(Asset).where(Asset.kind == "title_text", Asset.video_id == vids[0]))
    v0 = db.session.get(Video, vids[0])
    assert (app.config["APP"].data_root / title.path).read_text(encoding="utf-8") == f"{v0.title}\n"

    again = client.post("/api/discover/pull", {"video_ids": vids}).json
    assert again["pulled"] == 0 and again["already_tracked"] == 2
    assert client.post("/api/discover/pull", {"video_ids": [999999]}).status_code == 404


def test_patch_and_delete_channel(client, yt):
    seed_channel(yt, n=2)
    cid = client.post("/api/channels", {"channel": "@alpha"}).json["channel"]["id"]
    r = client.patch(
        f"/api/channels/{cid}",
        {"followed": True, "auto_track_new": True, "tags": ["tech", " tech ", "x"], "notes": "hi"},
    )
    assert r.json["channel"]["followed"] is True and r.json["channel"]["auto_track_new"] is True
    assert r.json["channel"]["tags"] == ["tech", "x"]
    assert client.delete(f"/api/channels/{cid}").status_code == 200
    assert count(Video) == 0


def test_only_tracked_videos_get_scheduled_snapshots(client, yt):
    seed_channel(yt, n=4, start_days_ago=100)
    yt.add_video(CID, "young", utcnow() - dt.timedelta(days=2), 100)
    cid = client.post("/api/channels", {"channel": "@alpha"}).json["channel"]["id"]
    assert ingest.due_videos(utcnow() + dt.timedelta(days=8), 100) == []  # nothing tracked yet

    ids = [v.id for v in db.session.scalars(db.select(Video).where(Video.channel_id == cid))]
    client.post("/api/discover/pull", {"video_ids": ids})
    assert ingest.snapshot_videos() == 0  # all just snapshotted

    due = {v.external_id for v in ingest.due_videos(utcnow() + dt.timedelta(hours=25), 100)}
    assert due == {"young"}  # older videos wait a week
    assert len(ingest.due_videos(utcnow() + dt.timedelta(days=7, hours=1), 100)) == 5

    v = db.session.scalar(db.select(Video).where(Video.external_id == "young"))
    v.last_snapshot_at = utcnow() - dt.timedelta(days=2)
    db.session.commit()
    yt.set_views("young", 250)
    assert ingest.snapshot_videos() == 1
    assert v.views == 250


def test_new_uploads_are_discovered_unless_auto_track(client, yt):
    seed_channel(yt, n=3)
    cid = client.post("/api/channels", {"channel": "@alpha", "follow": True}).json["channel"]["id"]
    yt.add_video(CID, "new1", utcnow(), 10)
    assert len(ingest.sync_new_uploads(cid)) == 1
    assert db.session.scalar(db.select(Video).where(Video.external_id == "new1")).selection == "discovered"

    client.patch(f"/api/channels/{cid}", {"auto_track_new": True})
    yt.add_video(CID, "new2", utcnow(), 10)
    ingest.sync_new_uploads(cid)
    assert db.session.scalar(db.select(Video).where(Video.external_id == "new2")).selection == "tracked"


def test_deleted_video_is_marked_gone(client, yt):
    seed_channel(yt, n=3)
    client.post("/api/channels", {"channel": "@alpha"})
    v = db.session.scalar(db.select(Video).where(Video.external_id == "aaaav00"))
    client.post("/api/discover/pull", {"video_ids": [v.id]})
    yt.videos.pop("aaaav00")
    v.last_snapshot_at = utcnow() - dt.timedelta(days=30)
    db.session.commit()
    ingest.snapshot_videos()
    assert v.gone is True


def test_history_interpolation():
    h = History([0, 1, 3], [0, 100, 300])
    assert h.at(0.5) == 50
    assert h.at(2) == 200
    assert h.at(3) == 300
    assert h.at(4) is None
    # A video first seen 400 days after upload has no known early views.
    assert History([0, 400], [0, 1000]).at(2) is None


def test_outlier_young_videos_compare_at_equal_age(app):
    """A 2-day-old video is compared with what its neighbours had at 2 days old."""
    now = utcnow()
    ch = Channel(platform_id=1, external_id=CID, title="c")
    db.session.add(ch)
    db.session.flush()
    vids = []
    for i in range(6):
        pub = now - dt.timedelta(days=60 - i * 10)
        v = Video(
            channel_id=ch.id,
            platform_id=1,
            external_id=f"v{i}",
            published_at=pub,
            format="long",
            views=10_000,
        )
        db.session.add(v)
        db.session.flush()
        # Each neighbour had 1,000 views at day 2 and 10,000 now.
        db.session.add(VideoSnapshot(video_id=v.id, views=1000, captured_at=pub + dt.timedelta(days=2)))
        db.session.add(VideoSnapshot(video_id=v.id, views=10_000, captured_at=now))
        vids.append(v)
    young = Video(
        channel_id=ch.id,
        platform_id=1,
        external_id="young",
        published_at=now - dt.timedelta(days=2),
        format="long",
        views=3000,
    )
    db.session.add(young)
    db.session.flush()
    db.session.add(VideoSnapshot(video_id=young.id, views=3000, captured_at=now))
    db.session.commit()
    scores = outlier_scores([*vids, young], now)
    assert scores[young.id]["score"] == pytest.approx(3.0, rel=0.01)
    assert scores[young.id]["method"] == "interpolated"


def test_shorts_have_their_own_baseline(client, yt):
    seed_channel(yt, n=8)
    for i in range(6):
        yt.add_video(CID, f"s{i}", utcnow() - dt.timedelta(days=200 - i), 100_000, "PT30S", vertical=True, short=True)
    client.post("/api/channels", {"channel": "@alpha"})
    s = db.session.scalar(db.select(Video).where(Video.external_id == "s0"))
    assert s.outlier_score == pytest.approx(1.0)  # judged against Shorts, not the 1k long-form videos
    assert db.session.scalar(db.select(AnalysisResult).where(AnalysisResult.video_id == s.id)) is not None


def test_thumbnail_endpoint_redirects_until_downloaded(client, yt):
    seed_channel(yt, n=1)
    client.post("/api/channels", {"channel": "@alpha"})
    v = db.session.scalar(db.select(Video))
    r = client.get(f"/api/videos/{v.id}/thumbnail")
    assert r.status_code == 302 and "ytimg" in r.headers["Location"]


def test_asset_file_supports_ranges(client, yt):
    seed_channel(yt, n=1)
    client.post("/api/channels", {"channel": "@alpha"})
    v = db.session.scalar(db.select(Video))
    client.post("/api/discover/pull", {"video_ids": [v.id]})
    a = db.session.scalar(db.select(Asset).where(Asset.kind == "title_text"))
    r = client.get(f"/api/assets/{a.id}/file", headers={"Range": "bytes=0-4"})
    assert r.status_code == 206 and r.data == b"Video"


def test_jpeg_size():
    # Minimal JPEG header: SOI, then SOF0 with height 360 and width 480.
    data = bytes([0xFF, 0xD8, 0xFF, 0xC0, 0x00, 0x11, 0x08, 0x01, 0x68, 0x01, 0xE0]) + b"\x00" * 20
    assert ingest.jpeg_size(data) == (480, 360)


def test_quota_budget_blocks_calls(client, yt):
    seed_channel(yt, n=1)
    update_settings({"daily_quota_budget": 100})
    spend(100)
    assert client.post("/api/channels", {"channel": "@alpha"}).status_code == 429

import datetime as dt
import urllib.parse

import pytest

from creator_lab import ingest
from creator_lab.extensions import db
from creator_lab.models import (
    AnalyticsReport,
    Channel,
    Job,
    OwnerCredential,
    OwnerSync,
    RetentionPoint,
    Video,
    VideoBreakdown,
    VideoDailyStat,
    VideoLifetimeStat,
    utcnow,
)
from creator_lab.youtube import oauth

from .fake_google import REFRESH, FakeGoogle
from .fake_youtube import CID, seed_channel


@pytest.fixture
def google(app, yt):
    cfg = app.config["APP"]
    cfg.google_client_id = "client-id.apps.googleusercontent.com"
    cfg.google_client_secret = "client-secret"
    cfg.google_redirect_uri = "http://localhost:5050/api/youtube/oauth/callback"
    seed_channel(yt, n=4)
    g = FakeGoogle(yt, CID)
    oauth.set_transport(g.transport())
    yield g
    oauth.set_transport(None)


def sign_in(client, anon, google, code="good-code"):
    r = client.post("/api/youtube/oauth/start", {"return_to": "/settings"})
    assert r.status_code == 200, r.json
    url = urllib.parse.urlparse(r.json["auth_url"])
    q = dict(urllib.parse.parse_qsl(url.query))
    assert url.netloc == "accounts.google.com"
    assert q["access_type"] == "offline" and q["code_challenge_method"] == "S256"
    assert "yt-analytics.readonly" in q["scope"] and "youtube.readonly" in q["scope"]
    # Google redirects the browser back; the callback is authenticated by state alone.
    return anon.get(f"/api/youtube/oauth/callback?state={q['state']}&code={code}")


def test_not_configured(client, app):
    app.config["APP"].google_client_id = None
    r = client.post("/api/youtube/oauth/start", {})
    assert r.status_code == 400 and "GOOGLE_OAUTH_CLIENT_ID" in r.json["error"]
    assert client.get("/api/youtube/owner").json["configured"] is False


def test_sign_in_stores_encrypted_token(client, anon, google):
    r = sign_in(client, anon, google)
    assert r.status_code == 302
    assert "youtube=connected" in r.headers["Location"] and r.headers["Location"].startswith(
        "http://localhost/settings"
    )
    cred = db.session.scalar(db.select(OwnerCredential))
    assert cred.status == "active"
    assert REFRESH not in cred.refresh_token_enc  # encrypted at rest
    assert oauth.decrypt(cred.refresh_token_enc) == REFRESH
    ch = db.session.get(Channel, cred.channel_id)
    assert ch.owned and ch.is_mine and ch.followed and ch.external_id == CID
    status = client.get("/api/youtube/owner").json
    assert status["configured"] and status["credentials"][0]["channel"]["id"] == ch.id
    assert REFRESH not in str(status)


def test_state_is_single_use_and_checked(client, anon, google):
    r = client.post("/api/youtube/oauth/start", {})
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r.json["auth_url"]).query))["state"]
    assert anon.get("/api/youtube/oauth/callback?state=forged&code=good-code").status_code == 400
    assert anon.get(f"/api/youtube/oauth/callback?state={state}&code=good-code").status_code == 302
    assert anon.get(f"/api/youtube/oauth/callback?state={state}&code=good-code").status_code == 400


def test_bad_code_and_user_denial_redirect_with_error(client, anon, google):
    r = sign_in(client, anon, google, code="bad-code")
    assert "youtube=error" in r.headers["Location"]
    r = client.post("/api/youtube/oauth/start", {})
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r.json["auth_url"]).query))["state"]
    r = anon.get(f"/api/youtube/oauth/callback?state={state}&error=access_denied")
    assert "message=access_denied" in r.headers["Location"]
    assert db.session.scalar(db.select(OwnerCredential)) is None


def test_return_to_cannot_leave_the_site(client, google):
    assert client.post("/api/youtube/oauth/start", {"return_to": "https://evil.example"}).status_code == 400
    assert client.post("/api/youtube/oauth/start", {"return_to": "//evil.example"}).status_code == 400


def test_full_sync_pulls_everything(client, anon, google):
    google.add_private_video("priv1", utcnow() - dt.timedelta(days=40), 77)
    sign_in(client, anon, google)
    ch = db.session.scalar(db.select(Channel).where(Channel.owned))
    r = client.post(f"/api/youtube/owner/{ch.id}/sync", {"mode": "full"})
    assert r.status_code == 200, r.json
    sync = db.session.get(OwnerSync, r.json["sync"]["id"])
    db.session.refresh(sync)
    assert sync.status == "done", sync.error
    assert sync.counts["videos_listed"] == 5 and sync.counts["videos_synced"] == 5
    assert sync.counts["retention_curves"] == 10  # ALL + ORGANIC for each video

    priv = db.session.scalar(db.select(Video).where(Video.external_id == "priv1"))
    assert priv.privacy == "private" and priv.selection == "tracked"
    assert {j.job_type for j in db.session.scalars(db.select(Job))} == {"media", "captions"}

    curve = db.session.scalars(
        db.select(RetentionPoint).where(RetentionPoint.video_id == priv.id, RetentionPoint.audience_type == "ALL")
    ).all()
    assert len(curve) == 100 and curve[0].video_duration_s == priv.duration_s
    life = db.session.scalar(db.select(VideoLifetimeStat).where(VideoLifetimeStat.video_id == priv.id))
    assert life.metrics["views"] == 42 and "cardTeaserClicks" not in life.metrics
    assert "videoThumbnailImpressions" not in life.metrics
    assert set(sync.unavailable["impressions"]) == {"videoThumbnailImpressions", "videoThumbnailImpressionsClickRate"}
    assert "cardTeaserClicks" in sync.unavailable["lifetime"]
    assert db.session.scalar(db.select(db.func.count()).select_from(VideoDailyStat)) == 15
    dims = set(db.session.scalars(db.select(VideoBreakdown.dimension).distinct()))
    assert dims == {"traffic_source", "playback_location", "device", "country", "age_gender", "subscribed_status"}
    raw = db.session.scalar(db.select(db.func.count()).select_from(AnalyticsReport))
    assert raw > 5 * 10  # every call kept raw

    eng = client.get(f"/api/videos/{priv.id}/engagement").json
    assert eng["owned"] is True and set(eng["retention"]["curves"]) == {"ALL", "ORGANIC"}
    pt = eng["retention"]["curves"]["ALL"][49]
    assert pt["ratio"] == 0.5 and pt["seconds"] == pytest.approx(0.5 * priv.duration_s)
    assert eng["lifetime"]["views"] == 42 and len(eng["daily"]) == 3
    assert eng["tribe"] is None


def test_new_only_sync_and_versions(client, anon, google, yt):
    sign_in(client, anon, google)
    ch = db.session.scalar(db.select(Channel).where(Channel.owned))
    first = client.post(f"/api/youtube/owner/{ch.id}/sync", {"mode": "full"}).json["sync"]["id"]
    yt.add_video(CID, "brandnew", utcnow(), 5)
    second = client.post(f"/api/youtube/owner/{ch.id}/sync", {"mode": "new"}).json["sync"]
    s2 = db.session.get(OwnerSync, second["id"])
    db.session.refresh(s2)
    assert s2.status == "done" and s2.counts["videos_synced"] == 1
    v = db.session.scalar(db.select(Video).where(Video.external_id == "aaaav00"))
    third = client.post(f"/api/youtube/owner/{ch.id}/sync", {"mode": "full"}).json["sync"]["id"]
    versions = client.get(f"/api/videos/{v.id}/engagement").json["retention"]["versions"]
    assert versions == [first, third]  # the re-pull added a version and kept the old one
    old = client.get(f"/api/videos/{v.id}/engagement?sync_id={first}").json["retention"]
    assert old["sync_id"] == first and len(old["curves"]["ALL"]) == 100


def test_expired_refresh_token_asks_for_sign_in(client, anon, google):
    sign_in(client, anon, google)
    ch = db.session.scalar(db.select(Channel).where(Channel.owned))
    google.refresh_valid = False
    oauth.set_transport(google.transport())  # clears cached access tokens
    sync_id = client.post(f"/api/youtube/owner/{ch.id}/sync", {"mode": "full"}).json["sync"]["id"]
    sync = db.session.get(OwnerSync, sync_id)
    db.session.refresh(sync)
    assert sync.status == "failed" and "Sign in again" in sync.error
    cred = db.session.scalar(db.select(OwnerCredential))
    assert cred.status == "expired"
    assert client.post(f"/api/youtube/owner/{ch.id}/sync", {"mode": "new"}).status_code == 409
    # Signing in again reactivates it.
    google.refresh_valid = True
    sign_in(client, anon, google)
    db.session.refresh(cred)
    assert cred.status == "active"


def test_disconnect(client, anon, google):
    sign_in(client, anon, google)
    ch = db.session.scalar(db.select(Channel).where(Channel.owned))
    assert client.delete(f"/api/youtube/owner/{ch.id}").status_code == 200
    assert db.session.scalar(db.select(OwnerCredential)) is None
    db.session.refresh(ch)
    assert ch.owned is False
    assert any("revoke" in c for c in google.calls)


def test_wrong_secret_key_requires_sign_in(client, anon, google, app):
    sign_in(client, anon, google)
    cred = db.session.scalar(db.select(OwnerCredential))
    app.config["SECRET_KEY"] = "a-different-secret"
    with pytest.raises(oauth.ReauthRequired):
        oauth.decrypt(cred.refresh_token_enc)
    _ = ingest  # keep import used

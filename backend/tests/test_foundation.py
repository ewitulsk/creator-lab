import re

from creator_lab import create_app
from creator_lab.api.system import openapi_spec
from creator_lab.config import Config
from creator_lab.extensions import db
from creator_lab.models import User

from .conftest import PASSWORD, make_config


def test_health_is_public(anon):
    assert anon.get("/api/health").json["ok"] is True


def test_api_requires_login(anon):
    assert anon.get("/api/dashboard").status_code == 401
    assert anon.get("/api/auth/me").status_code == 401


def test_login_logout(anon):
    r = anon.post("/api/auth/login", {"username": "admin", "password": "wrong"})
    assert r.status_code == 401
    r = anon.post("/api/auth/login", {"username": "admin", "password": PASSWORD})
    assert r.status_code == 200
    assert r.json["user"]["username"] == "admin"
    assert anon.get("/api/auth/me").status_code == 200
    anon.post("/api/auth/logout")
    assert anon.get("/api/auth/me").status_code == 401


def test_admin_password_is_hashed(app):
    user = db.session.scalar(db.select(User))
    assert user.password_hash != PASSWORD
    assert user.check_password(PASSWORD)


def test_writes_need_csrf_header(app, client):
    raw = app.test_client()
    raw.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    r = raw.put("/api/settings/schedules", json={"daily_quota_budget": 5000})
    assert r.status_code == 400
    r = client.put("/api/settings/schedules", {"daily_quota_budget": 5000})
    assert r.status_code == 200
    assert r.json["schedules"]["daily_quota_budget"] == 5000


def test_schedule_validation(client):
    assert client.put("/api/settings/schedules", {"nope": 1}).status_code == 400
    assert client.put("/api/settings/schedules", {"daily_quota_budget": 50_000}).status_code == 400
    assert client.put("/api/settings/schedules", {"daily_quota_budget": "x"}).status_code == 400


def test_change_password(client, anon):
    r = client.post("/api/auth/password", {"current_password": "bad", "new_password": "x" * 12})
    assert r.status_code == 403
    r = client.post("/api/auth/password", {"current_password": PASSWORD, "new_password": "short"})
    assert r.status_code == 400
    r = client.post("/api/auth/password", {"current_password": PASSWORD, "new_password": "a-new-password"})
    assert r.status_code == 200
    assert anon.post("/api/auth/login", {"username": "admin", "password": PASSWORD}).status_code == 401
    assert anon.post("/api/auth/login", {"username": "admin", "password": "a-new-password"}).status_code == 200


def test_settings_never_return_secrets(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert "test-key" not in text and "test-runner-token" not in text and "test-secret" not in text
    assert r.json["keys"] == {"youtube_api_key": True, "runner_token": True, "secret_key": True}
    assert r.json["max_video_height"] == 1080


def test_max_height_never_4k(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_VIDEO_HEIGHT", "2160")
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    assert Config.from_env(env_file=None).max_video_height == 1080
    monkeypatch.setenv("MAX_VIDEO_HEIGHT", "720")
    assert Config.from_env(env_file=None).max_video_height == 720


def test_dashboard_shape(client):
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    d = r.json
    assert d["counts"]["channels"] == 0
    assert "drive_free" in d["disk"]
    assert d["quota"]["units"] == 0


def test_login_is_rate_limited(tmp_path):
    app = create_app(make_config(tmp_path, ratelimit=True))
    with app.app_context():
        db.create_all()
        c = app.test_client()
        codes = [c.post("/api/auth/login", json={"username": "a", "password": "b"}).status_code for _ in range(12)]
        assert 429 in codes
        db.engine.dispose()


def test_spa_fallback(app, anon, tmp_path):
    assert b"not built" in anon.get("/").data
    dist = app.config["APP"].frontend_dist
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>")
    assert anon.get("/channels/5").data == b"<html>spa</html>"
    assert anon.get("/api/nope").status_code == 404


def test_every_api_route_is_in_openapi(app):
    spec_paths = openapi_spec()["paths"]
    missing = []
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api"):
            continue
        path = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", rule.rule)
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            if path not in spec_paths or method.lower() not in spec_paths[path]:
                missing.append(f"{method} {path}")
    assert not missing, f"undocumented routes: {missing}"

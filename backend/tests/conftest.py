from __future__ import annotations

from pathlib import Path

import pytest

from creator_lab import create_app
from creator_lab.bootstrap import bootstrap
from creator_lab.config import Config
from creator_lab.extensions import db

PASSWORD = "test-password-123"
RUNNER_TOKEN = "test-runner-token"


def make_config(tmp_path: Path, **extra) -> Config:
    return Config(
        data_root=tmp_path / "data",
        secret_key="test-secret",
        admin_username="admin",
        admin_password=PASSWORD,
        youtube_api_key="test-key",
        runner_token=RUNNER_TOKEN,
        brainthing_dir=None,
        max_video_height=1080,
        frontend_dist=tmp_path / "dist",
        testing=True,
        start_scheduler=False,
        extra=extra,
    )


@pytest.fixture
def app(tmp_path):
    app = create_app(make_config(tmp_path))
    with app.app_context():
        db.create_all()
        bootstrap(app.config["APP"])
        yield app
        db.session.remove()
        db.engine.dispose()


class ApiClient:
    """Test client that sends the CSRF header, like the frontend does."""

    def __init__(self, client):
        self.c = client

    def _h(self, headers=None):
        return {"X-Requested-With": "creator-lab", **(headers or {})}

    def get(self, url, **kw):
        return self.c.get(url, headers=self._h(kw.pop("headers", None)), **kw)

    def post(self, url, json=None, **kw):
        return self.c.post(url, json=json if json is not None else {}, headers=self._h(kw.pop("headers", None)), **kw)

    def put(self, url, json=None, **kw):
        return self.c.put(url, json=json if json is not None else {}, headers=self._h(kw.pop("headers", None)), **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self._h(kw.pop("headers", None)), **kw)

    def patch(self, url, json=None, **kw):
        return self.c.patch(url, json=json if json is not None else {}, headers=self._h(kw.pop("headers", None)), **kw)


@pytest.fixture
def anon(app):
    return ApiClient(app.test_client())


@pytest.fixture
def client(app):
    c = ApiClient(app.test_client())
    r = c.post("/api/auth/login", {"username": "admin", "password": PASSWORD})
    assert r.status_code == 200, r.json
    return c


@pytest.fixture
def runner_headers():
    return {"Authorization": f"Bearer {RUNNER_TOKEN}"}


@pytest.fixture
def yt(app):
    from creator_lab import ingest

    from .fake_youtube import FakeYouTube

    fake = FakeYouTube()
    ingest.set_client_factory(fake.client)
    yield fake
    ingest.set_client_factory(None)

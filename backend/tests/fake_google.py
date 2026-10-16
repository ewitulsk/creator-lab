"""Fake Google: OAuth token endpoint, owner Data API calls and the Analytics API."""

from __future__ import annotations

import json
import urllib.parse

import httpx

from .fake_youtube import FakeYouTube

REFRESH = "fake-refresh-token-xyz"
ACCESS = "fake-access-token"


class FakeGoogle:
    def __init__(self, yt: FakeYouTube, owner_channel: str):
        self.yt = yt
        self.owner_channel = owner_channel
        self.private: dict[str, dict] = {}  # extra uploads only the owner sees
        self.refresh_valid = True
        self.calls: list[str] = []
        self.analytics_calls: list[dict] = []
        self.rejected_metrics = {"videoThumbnailImpressions", "videoThumbnailImpressionsClickRate", "cardTeaserClicks"}
        self.audience_types_with_data = {"ORGANIC"}

    def add_private_video(self, vid: str, published, views: int) -> None:
        self.yt.add_video(self.owner_channel, vid, published, views)
        self.yt.videos[vid]["status"]["privacyStatus"] = "private"

    @staticmethod
    def _json(data, status=200) -> httpx.Response:
        return httpx.Response(status, content=json.dumps(data), headers={"Content-Type": "application/json"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.calls.append(f"{request.method} {url.host}{url.path}")
        if url.host == "oauth2.googleapis.com" and url.path == "/token":
            form = dict(urllib.parse.parse_qsl(request.content.decode()))
            if form["grant_type"] == "authorization_code":
                if form.get("code") != "good-code" or not form.get("code_verifier"):
                    return self._json({"error": "invalid_grant", "error_description": "bad code"}, 400)
                scope = "https://www.googleapis.com/auth/yt-analytics.readonly https://www.googleapis.com/auth/youtube.readonly"
                return self._json(
                    {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600, "scope": scope}
                )
            if form["grant_type"] == "refresh_token":
                if not self.refresh_valid or form["refresh_token"] != REFRESH:
                    return self._json({"error": "invalid_grant", "error_description": "Token has been expired"}, 400)
                return self._json({"access_token": ACCESS, "expires_in": 3600})
        if url.host == "oauth2.googleapis.com" and url.path == "/revoke":
            return self._json({})
        if url.host == "www.googleapis.com":
            if request.headers.get("Authorization") != f"Bearer {ACCESS}":
                return self._json({"error": {"message": "no auth"}}, 401)
            if url.path.endswith("/channels") and url.params.get("mine") == "true":
                return self._json({"items": [self.yt.channels[self.owner_channel]]})
            return self.yt.handler(request)
        if url.host == "www.youtube.com":
            return self.yt.handler(request)
        if url.host == "youtubeanalytics.googleapis.com":
            return self.analytics(request)
        return httpx.Response(404)

    def analytics(self, request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        self.analytics_calls.append(p)
        if request.headers.get("Authorization") != f"Bearer {ACCESS}":
            return self._json({"error": {"message": "unauthenticated"}}, 401)
        metrics = p["metrics"].split(",")
        bad = [m for m in metrics if m in self.rejected_metrics]
        if bad:
            return self._json(
                {"error": {"message": f"Unknown identifier ({bad[0]}) given in field parameters.metrics."}}, 400
            )
        filters = dict(f.split("==") for f in p.get("filters", "").split(";") if f)
        vid = filters.get("video")
        dims = p.get("dimensions")
        headers = []
        rows = []
        if dims == "elapsedVideoTimeRatio":
            audience = filters.get("audienceType")
            if audience and audience not in self.audience_types_with_data:
                return self._json({"columnHeaders": [], "rows": []})
            headers = [{"name": "elapsedVideoTimeRatio"}] + [{"name": m} for m in metrics]
            for i in range(1, 101):
                ratio = i / 100
                rows.append([ratio, round(1.0 - 0.5 * ratio, 4), round(0.5 + 0.1 * (i % 7), 4)])
        elif dims == "day":
            headers = [{"name": "day"}] + [{"name": m} for m in metrics]
            for d in ("2026-09-01", "2026-09-02", "2026-09-03"):
                rows.append([d] + [10 for _ in metrics])
        elif dims:
            keys = dims.split(",")
            headers = [{"name": k} for k in keys] + [{"name": m} for m in metrics]
            rows.append([f"{k.upper()}_A" for k in keys] + [5 for _ in metrics])
            rows.append([f"{k.upper()}_B" for k in keys] + [3 for _ in metrics])
        else:
            headers = [{"name": m} for m in metrics]
            rows.append([42 if vid else 0 for _ in metrics])
        return self._json({"kind": "youtubeAnalytics#resultTable", "columnHeaders": headers, "rows": rows})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

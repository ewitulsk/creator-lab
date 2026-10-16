"""An in-memory YouTube Data API for tests (served through httpx.MockTransport)."""

from __future__ import annotations

import datetime as dt
import json

import httpx

from creator_lab.settings import get_setting
from creator_lab.youtube.client import YouTubeClient


class FakeYouTube:
    def __init__(self):
        self.channels: dict[str, dict] = {}
        self.handles: dict[str, str] = {}
        self.uploads: dict[str, list[str]] = {}  # playlist id -> video ids, newest first
        self.videos: dict[str, dict] = {}
        self.shorts: set[str] = set()
        self.calls: list[str] = []

    # -------------------------------------------------------------- fixtures

    def add_channel(self, cid: str, handle: str, title: str, subs: int = 1000) -> None:
        self.channels[cid] = {
            "id": cid,
            "snippet": {
                "title": title,
                "customUrl": handle,
                "publishedAt": "2015-01-01T00:00:00Z",
                "thumbnails": {"high": {"url": f"https://yt3/{cid}.jpg"}},
            },
            "contentDetails": {"relatedPlaylists": {"uploads": "UU" + cid[2:]}},
            "statistics": {"subscriberCount": str(subs), "viewCount": "100000", "videoCount": "0"},
        }
        self.handles[handle.lower()] = cid
        self.uploads["UU" + cid[2:]] = []

    def add_video(
        self,
        cid: str,
        vid: str,
        published: dt.datetime,
        views: int,
        duration: str = "PT10M",
        vertical: bool = False,
        live: bool = False,
        short: bool = False,
    ) -> None:
        item = {
            "id": vid,
            "snippet": {
                "title": f"Video {vid}",
                "description": "desc",
                "publishedAt": published.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "channelId": cid,
                "tags": ["a"],
                "categoryId": "22",
                "liveBroadcastContent": "none",
                "thumbnails": {"high": {"url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}},
            },
            "contentDetails": {"duration": duration},
            "statistics": {"viewCount": str(views), "likeCount": str(views // 20), "commentCount": "5"},
            "status": {"privacyStatus": "public"},
            "player": {"embedWidth": "405" if vertical else "1280", "embedHeight": "720"},
        }
        if live:
            item["liveStreamingDetails"] = {"actualStartTime": item["snippet"]["publishedAt"]}
        self.videos[vid] = item
        self.uploads["UU" + cid[2:]].insert(0, vid)
        if short:
            self.shorts.add(vid)

    def set_views(self, vid: str, views: int) -> None:
        self.videos[vid]["statistics"]["viewCount"] = str(views)

    # -------------------------------------------------------------- transport

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.calls.append(f"{request.method} {url.path}")
        if url.host == "www.youtube.com" and url.path.startswith("/shorts/"):
            vid = url.path.rsplit("/", 1)[1]
            if vid in self.shorts:
                return httpx.Response(200)
            return httpx.Response(303, headers={"Location": f"https://www.youtube.com/watch?v={vid}"})
        p = dict(url.params)
        endpoint = url.path.rsplit("/", 1)[1]
        if endpoint == "channels":
            if "forHandle" in p:
                cid = self.handles.get(p["forHandle"].lower())
                items = [{"id": cid}] if cid else []
            else:
                items = [self.channels[i] for i in p["id"].split(",") if i in self.channels]
            return self._json({"items": items})
        if endpoint == "playlistItems":
            ids = self.uploads.get(p["playlistId"])
            if ids is None:
                return httpx.Response(
                    404, json={"error": {"message": "nf", "errors": [{"reason": "playlistNotFound"}]}}
                )
            start = int(p.get("pageToken", 0))
            page = ids[start : start + 50]
            body = {"items": [{"contentDetails": {"videoId": v}} for v in page]}
            if start + 50 < len(ids):
                body["nextPageToken"] = str(start + 50)
            return self._json(body)
        if endpoint == "search":
            q = p["q"].lower()
            hits = [cid for cid, c in self.channels.items() if q in c["snippet"]["title"].lower()]
            return self._json({"items": [{"id": {"kind": "youtube#channel", "channelId": c}} for c in hits]})
        if endpoint == "videos":
            items = [self.videos[i] for i in p["id"].split(",") if i in self.videos]
            return self._json({"items": items})
        return httpx.Response(404)

    @staticmethod
    def _json(data) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(data), headers={"Content-Type": "application/json"})

    def client(self) -> YouTubeClient:
        return YouTubeClient(
            "fake-key",
            budget=lambda: get_setting("daily_quota_budget"),
            transport=httpx.MockTransport(self.handler),
        )


CID = "UC" + "a" * 22
CID2 = "UC" + "b" * 22


def seed_channel(yt: FakeYouTube, cid=CID, handle="@alpha", n=12, start_days_ago=400):
    """n long-form uploads a week apart; every 4th has 5x the views."""
    from creator_lab.models import utcnow

    yt.add_channel(cid, handle, handle[1:].title())
    now = utcnow()
    for i in range(n):
        views = 1000 if i % 4 else 5000
        published = now - dt.timedelta(days=start_days_ago - i * 7)
        yt.add_video(cid, f"{cid[2:6]}v{i:02d}", published, views)

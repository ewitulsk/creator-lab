"""YouTube Data API v3 client.

Only cheap endpoints are used (1 unit per call): channels.list, playlistItems.list
and videos.list. search.list (100 units) is never used. Every call is counted
against the daily budget before it is made.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator

import httpx

from .quota import check_budget, spend

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/youtube/v3"
UC_RE = re.compile(r"UC[\w-]{22}")
VIDEO_PARTS = "snippet,contentDetails,statistics,liveStreamingDetails,player,status,topicDetails"
CHANNEL_PARTS = "snippet,contentDetails,statistics,brandingSettings"


class YouTubeError(RuntimeError):
    pass


class ChannelNotFound(YouTubeError):
    pass


def parse_channel_input(text: str) -> tuple[str, str]:
    """Classify user input as ("id", UC...), ("handle", @x), ("username", x) or ("url", url).

    Accepts @handle, UC... ids, and channel URLs (/@x, /channel/UC..., /user/x, /c/x).
    """
    s = text.strip()
    if not s:
        raise ValueError("empty channel")
    if UC_RE.fullmatch(s):
        return "id", s
    if s.startswith("@"):
        return "handle", s
    if s.startswith(("http://", "https://", "www.", "youtube.com", "m.youtube.com")):
        url = s if s.startswith("http") else "https://" + s
        path = httpx.URL(url).path
        if m := re.match(r"^/channel/(UC[\w-]{22})", path):
            return "id", m.group(1)
        if m := re.match(r"^/(@[^/?#]+)", path):
            return "handle", httpx.URL("https://x/" + m.group(1)).path[1:]  # percent-decoded
        if m := re.match(r"^/user/([^/?#]+)", path):
            return "username", m.group(1)
        return "url", url
    if re.fullmatch(r"[\w.\-]{3,100}", s):
        return "handle", "@" + s
    raise ValueError(f"not a channel handle, id or URL: {text!r}")


class YouTubeClient:
    def __init__(
        self,
        api_key: str,
        budget: Callable[[], int],
        transport: httpx.BaseTransport | None = None,
        count_quota: bool = True,
        access_token: str | None = None,
    ):
        if not api_key and not access_token:
            raise YouTubeError("YOUTUBE_API_KEY is not set in backend/.env")
        self.api_key = api_key
        self.access_token = access_token  # OAuth (owner) calls see private/unlisted uploads
        self.budget = budget
        self.count_quota = count_quota
        self.http = httpx.Client(timeout=30, transport=transport, follow_redirects=False)

    # ------------------------------------------------------------ low level

    def _get(self, endpoint: str, params: dict, units: int = 1) -> dict:
        if self.count_quota:
            check_budget(units, self.budget())
        headers = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        else:
            params = {**params, "key": self.api_key}
        for attempt in range(3):
            try:
                resp = self.http.get(f"{API}/{endpoint}", params=params, headers=headers)
            except httpx.TransportError as err:
                if attempt == 2:
                    raise YouTubeError(f"network error calling {endpoint}: {err}") from err
                continue
            if resp.status_code >= 500 and attempt < 2:
                continue
            break
        if self.count_quota:
            spend(units)
        if resp.status_code != 200:
            try:
                err = resp.json()["error"]
                reason = (err.get("errors") or [{}])[0].get("reason", "")
                msg = f"{err.get('message', resp.text)} ({reason})"
            except Exception:
                msg = resp.text[:300]
            raise YouTubeError(f"YouTube API {endpoint} failed: HTTP {resp.status_code}: {msg}")
        return resp.json()

    # ------------------------------------------------------------ channels

    def resolve_channel_id(self, text: str) -> str:
        kind, value = parse_channel_input(text)
        if kind == "id":
            return value
        if kind in ("handle", "username"):
            key = "forHandle" if kind == "handle" else "forUsername"
            items = self._get("channels", {"part": "id", key: value}).get("items") or []
            if not items:
                raise ChannelNotFound(f"no channel found for {value}")
            return items[0]["id"]
        # /c/custom or other URLs: the API cannot resolve these, so read the page's canonical link.
        resp = self.http.get(value, follow_redirects=True, headers={"Accept-Language": "en"})
        m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', resp.text)
        m = m or re.search(r'"(?:externalId|channelId)":"(UC[\w-]{22})"', resp.text)
        if not m:
            raise ChannelNotFound(f"could not find a channel id at {value}")
        return m.group(1)

    def search_channel_ids(self, query: str, limit: int = 10) -> list[str]:
        """Channel search by name. search.list costs 100 units: only call it on an
        explicit user search."""
        data = self._get(
            "search",
            {"part": "snippet", "type": "channel", "q": query, "maxResults": min(limit, 50)},
            units=100,
        )
        return [it["id"]["channelId"] for it in data.get("items") or [] if it.get("id", {}).get("channelId")]

    def channels(self, ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(ids), 50):
            chunk = ids[i : i + 50]
            data = self._get("channels", {"part": CHANNEL_PARTS, "id": ",".join(chunk), "maxResults": 50})
            out += data.get("items") or []
        return out

    # ------------------------------------------------------------ uploads and videos

    def playlist_video_ids(self, playlist_id: str, max_pages: int | None = None) -> Iterator[list[str]]:
        """Yield pages of video ids from a playlist (the uploads playlist is newest first)."""
        token = None
        pages = 0
        while True:
            params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 50}
            if token:
                params["pageToken"] = token
            try:
                data = self._get("playlistItems", params)
            except YouTubeError as err:
                if "playlistNotFound" in str(err):  # channel with no uploads
                    return
                raise
            ids = [it["contentDetails"]["videoId"] for it in data.get("items") or []]
            if ids:
                yield ids
            pages += 1
            token = data.get("nextPageToken")
            if not token or (max_pages and pages >= max_pages):
                return

    def videos(self, ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(ids), 50):
            chunk = ids[i : i + 50]
            # maxHeight makes the player part include embedWidth/embedHeight, i.e. the aspect ratio.
            data = self._get("videos", {"part": VIDEO_PARTS, "id": ",".join(chunk), "maxResults": 50, "maxHeight": 720})
            out += data.get("items") or []
        return out

    def video_stats(self, ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(ids), 50):
            chunk = ids[i : i + 50]
            data = self._get("videos", {"part": "statistics,status", "id": ",".join(chunk), "maxResults": 50})
            out += data.get("items") or []
        return out

    def is_short(self, video_id: str) -> bool | None:
        """youtube.com/shorts/<id> answers 200 for a Short and redirects to /watch otherwise.
        Costs no API quota. Returns None if YouTube gives no clear answer."""
        try:
            resp = self.http.head(f"https://www.youtube.com/shorts/{video_id}", follow_redirects=False)
        except httpx.HTTPError:
            return None
        if resp.status_code == 200:
            return True
        if resp.status_code in (301, 302, 303, 307, 308):
            return False
        return None

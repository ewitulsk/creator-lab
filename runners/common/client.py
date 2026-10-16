"""HTTP client for the platform's runner API (Bearer RUNNER_TOKEN)."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger(__name__)


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class JobLost(RuntimeError):
    """The platform gave this job to someone else (it was requeued)."""


class PlatformClient:
    def __init__(self, url: str, token: str, timeout: float = 30.0):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.runner_id: int | None = None

    def request(self, method: str, path: str, body: dict | None = None, retries: int = 5) -> dict:
        data = json.dumps(body or {}).encode() if method != "GET" else None
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        if self.runner_id is not None:
            headers["X-Runner-Id"] = str(self.runner_id)
        delay = 2.0
        for attempt in range(retries + 1):
            req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as err:
                try:
                    message = json.loads(err.read()).get("error", err.reason)
                except Exception:
                    message = str(err.reason)
                if err.code == 409:
                    raise JobLost(message) from err
                if err.code >= 500 and attempt < retries:
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                raise ApiError(err.code, message) from err
            except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
                if attempt >= retries:
                    raise
                log.warning("platform unreachable (%s); retrying in %.0fs", err, delay)
                time.sleep(delay)
                delay = min(delay * 2, 30)
        raise AssertionError("unreachable")

    # ------------------------------------------------------------ runner lifecycle

    def register(self, name: str, rtype: str, capabilities: list[str], **extra: Any) -> dict:
        runner = self.request(
            "POST", "/api/runners/register", {"name": name, "type": rtype, "capabilities": capabilities, **extra}
        )["runner"]
        self.runner_id = runner["id"]
        return runner

    def heartbeat(self, info: dict | None = None) -> dict:
        return self.request("POST", f"/api/runners/{self.runner_id}/heartbeat", {"info": info or {}}, retries=1)

    def claim(self, types: list[str] | None = None) -> dict | None:
        return self.request("POST", "/api/jobs/claim", {"runner_id": self.runner_id, "types": types})["job"]

    # ------------------------------------------------------------ job reporting

    def progress(self, job_id: int, fields: dict) -> dict:
        return self.request("POST", f"/api/jobs/{job_id}/progress", fields)

    def item_result(self, job_id: int, video_id: int, result: dict) -> None:
        self.request("POST", f"/api/jobs/{job_id}/items/{video_id}/result", result)

    def complete(self, job_id: int) -> None:
        self.request("POST", f"/api/jobs/{job_id}/complete")

    def fail(self, job_id: int, error: str, log_tail: str = "", retry: bool = False) -> None:
        self.request("POST", f"/api/jobs/{job_id}/fail", {"error": error, "log_tail": log_tail, "retry": retry})

    def log(self, job_id: int, text: str) -> None:
        self.request("POST", f"/api/jobs/{job_id}/log", {"text": text}, retries=1)

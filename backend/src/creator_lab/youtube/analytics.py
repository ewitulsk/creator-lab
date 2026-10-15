"""YouTube Analytics API v2 (owner data). Unsupported metrics degrade gracefully."""

from __future__ import annotations

import logging
from collections.abc import Callable

import httpx

log = logging.getLogger(__name__)

REPORTS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"


class AnalyticsError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


def rows_as_dicts(payload: dict) -> list[dict]:
    names = [h["name"] for h in payload.get("columnHeaders") or []]
    return [dict(zip(names, row, strict=False)) for row in payload.get("rows") or []]


class AnalyticsClient:
    """on_call(params, payload | None, error | None) is called for every request (logging/storage)."""

    def __init__(
        self,
        token: Callable[[], str],
        transport: httpx.BaseTransport | None = None,
        on_call: Callable[[dict, dict | None, str | None], None] | None = None,
    ):
        self.token = token
        self.http = httpx.Client(timeout=60, transport=transport)
        self.on_call = on_call
        self.calls = 0

    def report(
        self,
        *,
        start: str,
        end: str,
        metrics: list[str],
        dimensions: str | None = None,
        filters: str | None = None,
        sort: str | None = None,
        max_results: int | None = None,
    ) -> dict:
        params = {"ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": ",".join(metrics)}
        if dimensions:
            params["dimensions"] = dimensions
        if filters:
            params["filters"] = filters
        if sort:
            params["sort"] = sort
        if max_results:
            params["maxResults"] = str(max_results)
        self.calls += 1
        for attempt in range(3):
            resp = self.http.get(REPORTS_URL, params=params, headers={"Authorization": f"Bearer {self.token()}"})
            if resp.status_code >= 500 and attempt < 2:
                continue
            break
        if resp.status_code != 200:
            try:
                message = resp.json()["error"]["message"]
            except Exception:
                message = resp.text[:300]
            if self.on_call:
                self.on_call(params, None, message)
            raise AnalyticsError(resp.status_code, message)
        payload = resp.json()
        if self.on_call:
            self.on_call(params, payload, None)
        return payload

    def report_degrading(self, metrics: list[str], **kwargs) -> tuple[dict | None, list[str]]:
        """Ask for all metrics; if the API rejects the set, keep only the ones it accepts on
        their own. Returns (payload or None, rejected metrics)."""
        try:
            return self.report(metrics=metrics, **kwargs), []
        except AnalyticsError as err:
            if err.status != 400 or len(metrics) == 1:
                if err.status == 400:
                    return None, list(metrics)
                raise
        accepted, rejected = [], []
        for m in metrics:
            try:
                self.report(metrics=[m], **kwargs)
                accepted.append(m)
            except AnalyticsError as err:
                if err.status != 400:
                    raise
                rejected.append(m)
        if not accepted:
            return None, rejected
        return self.report(metrics=accepted, **kwargs), rejected

"""A thin wrapper over google-genai's Interactions and Files APIs.

Everything the scorer needs goes through `LeverClient`, so tests swap in a fake
(`set_client_factory`) and CI never makes a real call or needs a key. The key is passed
to the SDK and nowhere else: not logged, not stored, not returned.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

TERMINAL = {"completed", "failed", "cancelled", "incomplete", "budget_exceeded"}


class GeminiError(RuntimeError):
    retryable = False


class RetryableGeminiError(GeminiError):
    retryable = True


class UrlRejected(GeminiError):
    """Gemini would not read this YouTube URL (private, unlisted, region...)."""


@dataclass
class Uploaded:
    name: str
    uri: str
    mime_type: str


@dataclass
class Generated:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    raw: dict = field(default_factory=dict)


class LeverClient:
    """The real client (google-genai)."""

    def __init__(self, api_key: str):
        from google import genai

        self._client = genai.Client(api_key=api_key)

    # ---- files

    def upload(self, path: Path, mime_type: str = "video/mp4", timeout_s: float = 900) -> Uploaded:
        f = self._client.files.upload(file=str(path), config={"mime_type": mime_type})
        t0 = time.monotonic()
        while _state(f) == "PROCESSING":
            if time.monotonic() - t0 > timeout_s:
                raise RetryableGeminiError("the uploaded video is still processing")
            time.sleep(3)
            f = self._client.files.get(name=f.name)
        if _state(f) == "FAILED":
            raise GeminiError("Gemini could not process the uploaded video")
        return Uploaded(name=f.name, uri=f.uri, mime_type=f.mime_type or mime_type)

    def delete(self, name: str) -> None:
        self._client.files.delete(name=name)

    # ---- generation

    def generate(
        self,
        model: str,
        parts: list[dict],
        schema: dict,
        temperature: float,
        agentic: bool,
        timeout_s: float = 3600,
    ) -> Generated:
        content = []
        for p in parts:
            if p["kind"] == "text":
                content.append({"type": "text", "text": p["text"]})
            else:
                video = {"type": "video", "uri": p["uri"]}
                if p.get("mime_type"):
                    video["mime_type"] = p["mime_type"]
                if agentic:
                    video["processing"] = "agentic"
                content.append(video)
        try:
            it = self._client.interactions.create(
                model=model,
                input=content,
                response_format={"type": "text", "mime_type": "application/json", "schema": schema},
                generation_config={"temperature": temperature},
                background=True,
            )
            t0 = time.monotonic()
            while _status(it) not in TERMINAL:
                if time.monotonic() - t0 > timeout_s:
                    raise RetryableGeminiError("Gemini did not finish in time")
                time.sleep(5)
                it = self._client.interactions.get(it.id)
        except GeminiError:
            raise
        except Exception as err:  # SDK/HTTP errors: classify without echoing request details
            raise classify(err, any(p["kind"] == "video" and "youtube.com" in p["uri"] for p in parts)) from None
        status = _status(it)
        if status != "completed":
            detail = "; ".join(str(getattr(e, "message", e)) for e in (getattr(it, "errors", None) or []))[:300]
            raise GeminiError(f"Gemini interaction {status}: {detail}")
        usage = getattr(it, "usage", None)
        return Generated(
            text=getattr(it, "output_text", None) or "",
            input_tokens=int(getattr(usage, "total_input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "total_output_tokens", 0) or 0),
            thought_tokens=int(getattr(usage, "total_thought_tokens", 0) or 0),
            raw={"id": getattr(it, "id", None), "status": status},
        )

    def check_model(self, model: str) -> dict:
        try:
            m = self._client.models.get(model=model)
        except Exception as err:
            raise classify(err, False) from None
        return {"name": getattr(m, "name", model), "display_name": getattr(m, "display_name", None)}


def _state(f) -> str:
    s = getattr(f, "state", None)
    return str(getattr(s, "name", s) or "").upper()


def _status(it) -> str:
    s = getattr(it, "status", None)
    return str(getattr(s, "value", s) or "")


def classify(err: Exception, had_url: bool) -> GeminiError:
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    msg = f"{type(err).__name__} {code or ''}: {getattr(err, 'message', None) or str(err)}"[:400]
    if code in (429, 500, 502, 503, 504) or "RESOURCE_EXHAUSTED" in msg or "UNAVAILABLE" in msg:
        return RetryableGeminiError(msg)
    if had_url and code in (400, 403, 404):
        return UrlRejected(msg)
    return GeminiError(msg)


# ------------------------------------------------------------------ factory (tests swap it)

_factory: Callable[[str], object] | None = None


def set_client_factory(fn: Callable[[str], object] | None) -> None:
    global _factory
    _factory = fn


def make_client(api_key: str):
    return (_factory or LeverClient)(api_key)


def with_retries(fn: Callable[[], object], tries: int = 5, base_delay: float = 4.0, sleep=time.sleep):
    """Retry rate limits and server errors with exponential backoff."""
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except RetryableGeminiError as err:
            if attempt == tries:
                raise
            delay = base_delay * 2 ** (attempt - 1)
            log.warning("Gemini: %s; retrying in %.0fs (attempt %d of %d)", err, delay, attempt, tries)
            sleep(delay)
    raise AssertionError("unreachable")

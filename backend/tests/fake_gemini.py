"""A fake Gemini lever client: records every call, answers from fixtures. No network."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

from creator_lab.levers import gemini, rubric

LEVERS = rubric.LEVERS


def score_response(r: rubric.Rubric, base: int = 3, attention: int | None = None) -> dict:
    levers = {}
    for lv in LEVERS:
        s = attention if (lv == "attention" and attention is not None) else base
        levers[lv] = {
            "score": s,
            "confidence": "med",
            "reason": f"{lv} reason",
            "evidence": [{"t_start": 1.0, "t_end": 4.0, "note": f"{lv} moment"}],
            "subscores": dict.fromkeys(r.items(lv), s),
        }
    return {
        "levers": levers,
        "emotional_moments": [{"t": 12.5, "intensity": 4, "emotion": "joy", "kind": "elicited"}],
        "hook_score": 4,
    }


class FakeLeverClient:
    def __init__(self, r: rubric.Rubric):
        self.r = r
        self.calls: list[dict] = []
        self.uploads: list[Path] = []
        self.deleted: list[str] = []
        self.score_queue: list = []  # dicts (JSON) or strings (raw text) or exceptions, consumed first
        self.attention_scores = itertools.cycle([2, 3, 5])  # spread 3 -> flagged
        self.probe = {"identified": False, "creator": "unknown", "title": "unknown", "confidence": "low"}
        self.reject_urls = False
        self.fail_with: Exception | None = None
        self.checked: list[str] = []

    # ---- factory hook
    def __call__(self, api_key: str):
        self.api_key = api_key
        return self

    def upload(self, path: Path, mime_type: str = "video/mp4", timeout_s: float = 900):
        self.uploads.append(Path(path))
        return gemini.Uploaded(name=f"files/{len(self.uploads)}", uri=f"https://fake/files/{len(self.uploads)}",
                               mime_type="video/mp4")  # fmt: skip

    def delete(self, name: str) -> None:
        self.deleted.append(name)

    def check_model(self, model: str) -> dict:
        self.checked.append(model)
        return {"name": f"models/{model}"}

    def generate(self, model, parts, schema, temperature, agentic, timeout_s=3600):
        self.calls.append({"model": model, "parts": parts, "schema": schema, "temperature": temperature,
                           "agentic": agentic})  # fmt: skip
        if self.fail_with is not None:
            raise self.fail_with
        if self.reject_urls and any(p["kind"] == "video" and "youtube.com" in p["uri"] for p in parts):
            raise gemini.UrlRejected("400: cannot read that URL")
        text = parts[0]["text"]
        if text == rubric.PROBE_PROMPT:
            data = self.probe
        elif "Video A:" in [p.get("text") for p in parts]:
            data = {"levers": {lv: {"winner": "A", "confidence": "high", "reason": "A is better"} for lv in LEVERS}}
        else:
            if self.score_queue:
                item = self.score_queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                if isinstance(item, str):
                    return gemini.Generated(text=item, input_tokens=10, output_tokens=1)
                data = item
            else:
                data = score_response(self.r, attention=next(self.attention_scores))
        return gemini.Generated(text=json.dumps(data), input_tokens=100_000, output_tokens=2_000, thought_tokens=1_000)

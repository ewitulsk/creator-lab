"""The versioned rubric (rubrics/levers_v1.yaml): prompt text, response schema, validation.

The prompt is built from the rubric alone. It never contains the title, channel,
description, view counts or upload date: the caller has no way to pass them in.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..config import REPO_ROOT

RUBRIC_PATH = REPO_ROOT / "rubrics" / "levers_v1.yaml"
LEVERS = ("attention", "emotion", "comprehension", "valuable", "memorable", "shareable")
CONFIDENCE = ("low", "med", "high")
METHOD = "gemini_agentic"


@dataclass(frozen=True)
class Rubric:
    version: int
    hash: str
    data: dict

    def items(self, lever: str) -> list[str]:
        return list(self.data["levers"][lever].get("items", {}))


def load(path: Path = RUBRIC_PATH) -> Rubric:
    raw = path.read_bytes()
    data = yaml.safe_load(raw)
    missing = [lv for lv in LEVERS if lv not in data.get("levers", {})]
    if missing:
        raise ValueError(f"rubric is missing levers: {', '.join(missing)}")
    return Rubric(version=int(data["version"]), hash=hashlib.sha256(raw).hexdigest(), data=data)


# ------------------------------------------------------------------ prompts


def score_prompt(r: Rubric) -> str:
    d = r.data
    out = [d["instructions"].strip(), ""]
    for lever in LEVERS:
        spec = d["levers"][lever]
        out.append(f"## {lever} (System {spec.get('system', '?')}): {spec['question']}")
        for level in range(1, 6):
            out.append(f"  {level}: {spec['anchors'][level]}")
        if spec.get("items"):
            out.append("  Sub-items, each 1 to 5:")
            for name, text in spec["items"].items():
                out.append(f"  - {name}: {text}")
        out.append("")
    out += [d["emotional_moments"].strip(), "", d["hook"].strip(), "", "Answer with JSON only, following the schema."]
    return "\n".join(out)


PROBE_PROMPT = (
    "Can you identify this video or its creator? Answer unknown if not sure. "
    "Answer with JSON only, following the schema."
)


def pairwise_prompt(r: Rubric) -> str:
    d = r.data
    out = [
        "You are comparing two videos, Video A and Video B, on six levers that predict how well online "
        "videos perform. Watch and listen to both completely. Judge only what is in the videos. For each "
        "lever say which video is stronger (A, B or tie), how confident you are, and why in at most two "
        "sentences. Do not guess how popular either video is or who made it.",
        "",
    ]
    for lever in LEVERS:
        spec = d["levers"][lever]
        out.append(f"- {lever}: {spec['question']}")
    out += ["", "Answer with JSON only, following the schema."]
    return "\n".join(out)


# ------------------------------------------------------------------ schemas


def _score(lo: int = 1, hi: int = 5) -> dict:
    return {"type": "integer", "minimum": lo, "maximum": hi}


def score_schema(r: Rubric) -> dict:
    lever_props = {}
    for lever in LEVERS:
        items = r.items(lever)
        lever_props[lever] = {
            "type": "object",
            "properties": {
                "score": _score(),
                "confidence": {"type": "string", "enum": list(CONFIDENCE)},
                "reason": {"type": "string"},
                "evidence": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "t_start": {"type": "number"},
                            "t_end": {"type": "number"},
                            "note": {"type": "string"},
                        },
                        "required": ["t_start", "t_end", "note"],
                    },
                },
                "subscores": {
                    "type": "object",
                    "properties": {i: _score() for i in items},
                    "required": items,
                },
            },
            "required": ["score", "confidence", "reason", "evidence", "subscores"],
        }
    return {
        "type": "object",
        "properties": {
            "levers": {"type": "object", "properties": lever_props, "required": list(LEVERS)},
            "emotional_moments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "t": {"type": "number"},
                        "intensity": _score(),
                        "emotion": {"type": "string"},
                        "kind": {"type": "string", "enum": ["displayed", "elicited"]},
                    },
                    "required": ["t", "intensity", "emotion", "kind"],
                },
            },
            "hook_score": _score(),
        },
        "required": ["levers", "emotional_moments", "hook_score"],
    }


PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "identified": {"type": "boolean"},
        "creator": {"type": "string"},
        "title": {"type": "string"},
        "confidence": {"type": "string", "enum": list(CONFIDENCE)},
    },
    "required": ["identified", "creator", "title", "confidence"],
}


def pairwise_schema() -> dict:
    one = {
        "type": "object",
        "properties": {
            "winner": {"type": "string", "enum": ["A", "B", "tie"]},
            "confidence": {"type": "string", "enum": list(CONFIDENCE)},
            "reason": {"type": "string"},
        },
        "required": ["winner", "confidence", "reason"],
    }
    return {
        "type": "object",
        "properties": {
            "levers": {"type": "object", "properties": dict.fromkeys(LEVERS, one), "required": list(LEVERS)}
        },
        "required": ["levers"],
    }


# ------------------------------------------------------------------ validation


class InvalidResponse(ValueError):
    pass


def _int_1_5(v, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or not 1 <= v <= 5:
        raise InvalidResponse(f"{where} must be a whole number from 1 to 5 (got {v!r})")
    return int(v)


def _num(v, where: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        raise InvalidResponse(f"{where} must be a non-negative number of seconds (got {v!r})")
    return float(v)


def validate_scores(data, r: Rubric) -> dict:
    """Check a scoring response against the rubric. Returns a cleaned copy."""
    if not isinstance(data, dict) or not isinstance(data.get("levers"), dict):
        raise InvalidResponse("response must be an object with 'levers'")
    levers = {}
    for lever in LEVERS:
        x = data["levers"].get(lever)
        if not isinstance(x, dict):
            raise InvalidResponse(f"missing lever {lever}")
        conf = x.get("confidence")
        if conf not in CONFIDENCE:
            raise InvalidResponse(f"{lever}.confidence must be low, med or high")
        evidence = []
        for i, e in enumerate(x.get("evidence") or []):
            if not isinstance(e, dict):
                raise InvalidResponse(f"{lever}.evidence[{i}] must be an object")
            t0, t1 = _num(e.get("t_start"), f"{lever}.evidence[{i}].t_start"), _num(e.get("t_end"), "t_end")
            evidence.append({"t_start": t0, "t_end": max(t0, t1), "note": str(e.get("note", ""))[:500]})
        subs_in = x.get("subscores") or {}
        subs = {item: _int_1_5(subs_in.get(item), f"{lever}.subscores.{item}") for item in r.items(lever)}
        levers[lever] = {
            "score": _int_1_5(x.get("score"), f"{lever}.score"),
            "confidence": conf,
            "reason": str(x.get("reason", ""))[:1000],
            "evidence": evidence,
            "subscores": subs,
        }
    moments = []
    for i, m in enumerate(data.get("emotional_moments") or []):
        if not isinstance(m, dict) or m.get("kind") not in ("displayed", "elicited"):
            raise InvalidResponse(f"emotional_moments[{i}] needs kind displayed or elicited")
        moments.append(
            {
                "t": _num(m.get("t"), f"emotional_moments[{i}].t"),
                "intensity": _int_1_5(m.get("intensity"), f"emotional_moments[{i}].intensity"),
                "emotion": str(m.get("emotion", ""))[:80],
                "kind": m["kind"],
            }
        )
    return {
        "levers": levers,
        "emotional_moments": moments,
        "hook_score": _int_1_5(data.get("hook_score"), "hook_score"),
    }


def validate_probe(data) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("identified"), bool):
        raise InvalidResponse("probe response needs a boolean 'identified'")
    return {
        "identified": data["identified"],
        "creator": str(data.get("creator") or "unknown")[:200],
        "title": str(data.get("title") or "unknown")[:300],
        "confidence": data.get("confidence") if data.get("confidence") in CONFIDENCE else "low",
    }


def validate_pairwise(data) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("levers"), dict):
        raise InvalidResponse("response must be an object with 'levers'")
    out = {}
    for lever in LEVERS:
        x = data["levers"].get(lever)
        if not isinstance(x, dict) or x.get("winner") not in ("A", "B", "tie"):
            raise InvalidResponse(f"{lever}.winner must be A, B or tie")
        out[lever] = {
            "winner": x["winner"],
            "confidence": x.get("confidence") if x.get("confidence") in CONFIDENCE else "low",
            "reason": str(x.get("reason", ""))[:1000],
        }
    return {"levers": out}

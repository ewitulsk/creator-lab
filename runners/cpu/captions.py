"""Captions with yt-dlp: uploaded captions first, auto-generated as a fallback.

Writes captions.<lang>.vtt (as served), captions.<lang>.json (timed cues, with the rolling
duplicates of auto-captions removed) and captions.<lang>.txt (plain text).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from ..common.runner import Reporter, RetryableError

log = logging.getLogger("runner.captions")

TIME_RE = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})")
TAG_RE = re.compile(r"<[^>]+>")


def _seconds(text: str) -> float:
    m = TIME_RE.match(text.strip())
    if not m:
        raise ValueError(f"bad timestamp {text!r}")
    h, mi, s, ms = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000


def parse_vtt(text: str) -> list[dict]:
    """WebVTT to [{start, end, text}]. YouTube auto-captions repeat the previous line in
    each cue (a rolling window); only the new lines of each cue are kept."""
    # Group lines by timing line (blank lines inside cues are common in YouTube's files).
    raw: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if "-->" in line:
            raw.append((line, []))
        elif raw:
            raw[-1][1].append(line)
    cues: list[dict] = []
    prev_lines: list[str] = []
    for timing, lines in raw:
        start_s, end_s = (part.strip().split(" ")[0] for part in timing.split("-->"))
        body = [TAG_RE.sub("", ln).strip() for ln in lines]
        # Drop the numeric id that precedes the next cue's timing line.
        while body and body[-1].isdigit():
            body.pop()
        body = [b for b in body if b]
        new = [b for b in body if b not in prev_lines]
        if body:
            prev_lines = body
        if not new:
            continue
        start, end = _seconds(start_s), _seconds(end_s)
        if end - start < 0.02 and cues:  # zero-length repeat cues
            continue
        cues.append({"start": round(start, 3), "end": round(end, 3), "text": " ".join(new)})
    return cues


def cues_text(cues: list[dict]) -> str:
    return " ".join(c["text"] for c in cues).replace("  ", " ").strip()


def pick_language(info: dict, preferred: str | None) -> tuple[str | None, str | None]:
    """(language code, 'uploaded'|'auto') following: uploaded in the video's language,
    uploaded English, auto in the video's language, auto English."""
    uploaded = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    langs = [x for x in [preferred, (preferred or "").split("-")[0], "en", "en-US", "en-GB"] if x]
    for source, table in (("uploaded", uploaded), ("auto", auto)):
        for lang in langs:
            if lang in table:
                return lang, source
        if source == "uploaded":
            for lang in table:
                if lang.split("-")[0] in {x.split("-")[0] for x in langs} and lang != "live_chat":
                    return lang, source
    # Auto-captions in the original language are listed as '<lang>-orig' sometimes.
    for lang in auto:
        if lang.endswith("-orig"):
            return lang, "auto"
    return None, None


def fetch_captions(item: dict, reporter: Reporter) -> dict:
    import yt_dlp

    out_dir = Path(item["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    base = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(base) as ydl:
            info = ydl.extract_info(item["url"], download=False)
    except yt_dlp.utils.DownloadError as err:
        if "429" in str(err) or "Too Many Requests" in str(err):
            raise RetryableError(str(err)[:300]) from err
        raise
    lang, source = pick_language(info, item.get("language"))
    if lang is None:
        return {"status": "done", "summary": {"available": False, "reason": "no captions on YouTube"}}
    reporter.update(stage_percent=40, message=f"fetching {source} captions ({lang})")
    opts = base | {
        "writesubtitles": source == "uploaded",
        "writeautomaticsub": source == "auto",
        "subtitleslangs": [lang],
        "subtitlesformat": "vtt",
        "outtmpl": str(out_dir / "captions.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.process_ie_result(info, download=True)
    written = next(out_dir.glob(f"captions.{lang}.vtt"), None) or next(out_dir.glob("captions.*.vtt"), None)
    if written is None:
        raise RuntimeError(f"yt-dlp did not write the {lang} captions")
    short = lang.removesuffix("-orig")
    vtt = out_dir / f"captions.{short}.vtt"
    if written != vtt:
        written.replace(vtt)
    cues = parse_vtt(vtt.read_text(encoding="utf-8"))
    json_path = out_dir / f"captions.{short}.json"
    json_path.write_text(
        json.dumps({"language": short, "source": source, "cues": cues}, ensure_ascii=False), encoding="utf-8"
    )
    txt = out_dir / f"captions.{short}.txt"
    text = cues_text(cues)
    txt.write_text(text + "\n", encoding="utf-8")
    return {
        "status": "done",
        "output_path": str(json_path),
        "summary": {
            "available": True,
            "language": short,
            "source": source,
            "cues": len(cues),
            "words": len(text.split()),
        },
        "assets": [
            {
                "kind": "captions",
                "path": str(vtt),
                "variant": short,
                "meta": {"source": source, "cues_path": str(json_path)},
            },
            {"kind": "captions_text", "path": str(txt), "variant": short, "meta": {"source": source}},
        ],
    }


def run_captions(job: dict, reporter: Reporter) -> None:
    for i, item in enumerate(job["items"], 1):
        if item["status"] == "done":
            continue
        reporter.video(i, item["video_id"], f"captions for {item['title'][:60]}")
        try:
            result = fetch_captions(item, reporter)
        except RetryableError:
            raise
        except Exception as err:
            log.error("captions failed for %s: %s", item["external_id"], err)
            result = {"status": "failed", "error": str(err)[:1000]}
        reporter.update(stage_percent=100, force=True)
        reporter.client.item_result(job["id"], item["video_id"], result)

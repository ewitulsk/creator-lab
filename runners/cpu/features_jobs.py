"""CPU analyzer jobs: visual and audio features, TRIBE engagement curves, ResMem."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..common.runner import Reporter

log = logging.getLogger("runner.features")


def _each(job: dict, reporter: Reporter, label: str, work) -> None:
    for i, item in enumerate(job["items"], 1):
        if item["status"] == "done":
            continue
        reporter.video(i, item["video_id"], f"{label} for {item['title'][:60]}")
        try:
            result = work(item)
        except Exception as err:
            log.error("%s failed for %s: %s", label, item["external_id"], err)
            result = {"status": "failed", "error": f"{type(err).__name__}: {err}"[:1000]}
        reporter.update(stage_percent=100, force=True)
        reporter.client.item_result(job["id"], item["video_id"], result)


def run_visual(job: dict, reporter: Reporter) -> None:
    from .visual import analyze_video

    cache = Path(job["runner_cache_dir"]) / "models"

    def work(item):
        video = item["inputs"].get("media")
        if not video or not Path(video).exists():
            return {"status": "failed", "error": "the video isn't downloaded"}
        result = analyze_video(Path(video), cache, progress=lambda p: reporter.update(stage_percent=p))
        out = Path(item["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        path = out / "visual_features.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        return {"status": "done", "output_path": str(path), "summary": result["summary"]}

    _each(job, reporter, "visual features", work)


def run_audio(job: dict, reporter: Reporter) -> None:
    from .audio import analyze_audio

    def work(item):
        video = item["inputs"].get("media")
        if not video or not Path(video).exists():
            return {"status": "failed", "error": "the video isn't downloaded"}
        cues = None
        text = item["inputs"].get("captions")  # captions.<lang>.txt; the timed cues sit next to it
        if text:
            cues = Path(text).with_suffix(".json")
        reporter.update(stage_percent=10, message="decoding audio")
        result = analyze_audio(Path(video), cues)
        out = Path(item["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        path = out / "audio_features.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        return {"status": "done", "output_path": str(path), "summary": result["summary"]}

    _each(job, reporter, "audio features", work)


def run_engagement(job: dict, reporter: Reporter) -> None:
    from .engagement import run

    atlas = Path(job["data_root"]) / "brain" / "atlas.json"
    cache = Path(job["runner_cache_dir"]) / "atlas"

    def work(item):
        tribe = item["inputs"].get("tribe_video")
        if not tribe or not (Path(tribe) / "preds.npy").exists():
            return {"status": "failed", "error": "no TRIBE result for this video"}
        out = Path(item["output_dir"])
        summary = run(Path(tribe), atlas, cache, out)
        return {"status": "done", "output_path": str(out / "engagement.json"), "summary": summary}

    _each(job, reporter, "engagement curves", work)


def resmem_available() -> bool:
    try:
        import resmem  # noqa: F401

        return True
    except Exception:
        return False


def run_resmem(job: dict, reporter: Reporter) -> None:
    from PIL import Image
    from resmem import ResMem, transformer  # optional extra: uv sync --extra resmem

    model = ResMem(pretrained=True)
    model.eval()

    def work(item):
        import torch

        thumb = item["inputs"].get("thumbnail")
        if not thumb or not Path(thumb).exists():
            return {"status": "failed", "error": "no thumbnail on disk"}
        img = Image.open(thumb).convert("RGB")
        with torch.no_grad():
            score = float(model(transformer(img).view(-1, 3, 227, 227)).item())
        out = Path(item["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "resmem.json").write_text(json.dumps({"memorability": score}), encoding="utf-8")
        return {"status": "done", "output_path": str(out / "resmem.json"), "summary": {"memorability": round(score, 5)}}

    _each(job, reporter, "ResMem", work)

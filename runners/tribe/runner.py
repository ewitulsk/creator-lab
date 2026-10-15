"""The GPU runner for TRIBE v2 jobs (tribe_video, tribe_title). Runs in BrainThing's venv."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..common.runner import BaseRunner, Reporter
from .pipeline import TITLE_STAGES, VIDEO_STAGES, Item, RunOptions, TribePipeline

log = logging.getLogger("runner.tribe")


class TribeRunner(BaseRunner):
    type = "gpu"
    capabilities = ["tribe_video", "tribe_title"]
    default_name = "tribe"

    def gpu_name(self) -> str | None:
        try:
            import torch

            return torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
        except Exception:
            return None

    def info(self) -> dict:
        info = super().info()
        try:
            import torch

            if torch.cuda.is_available():
                free, total = torch.cuda.mem_get_info()
                info |= {"vram_free_gb": round(free / 1024**3, 1), "vram_total_gb": round(total / 1024**3, 1)}
        except Exception:
            pass
        return info

    def data_root(self) -> Path | None:
        return getattr(self, "_data_root", None)

    def stages_for(self, job: dict) -> list[str]:
        return list(VIDEO_STAGES if job["type"] == "tribe_video" else TITLE_STAGES)

    def handle(self, job: dict, reporter: Reporter) -> None:
        self._data_root = Path(job["data_root"])
        from .brain_assets import ensure_brain_assets

        ensure_brain_assets(self._data_root, Path(job["runner_cache_dir"]) / "tribe")
        kind = "video" if job["type"] == "tribe_video" else "title"
        input_name = "media" if kind == "video" else "title_text"
        items: list[Item] = []
        for p in job["items"]:
            if p["status"] == "done":
                continue
            path = p["inputs"].get(input_name)
            item = Item(
                key=p["external_id"],
                video_id=p["video_id"],
                title=p["title"],
                input_path=Path(path) if path else Path(),
                output_dir=Path(p["output_dir"]),
            )
            if not path or not Path(path).exists():
                item.error = f"input {input_name} is missing (download it first)"
            items.append(item)
        for it in items:
            if it.error:
                reporter.client.item_result(job["id"], it.video_id, {"status": "failed", "error": it.error})
        if not any(it.error is None for it in items):
            return

        def report(it: Item) -> None:
            if it.error:
                result = {"status": "failed", "error": it.error[:2000]}
            else:
                assets = []
                wav = it.input_path.with_suffix(".wav")
                if kind == "video" and wav.exists():
                    assets.append({"kind": "audio", "path": str(wav), "bytes": wav.stat().st_size})
                result = {
                    "status": "done",
                    "output_path": str(it.output_dir),
                    "summary": it.result | {"stages": [s.__dict__ for s in pipeline.stats]},
                    "assets": assets,
                }
            reporter.client.item_result(job["id"], it.video_id, result)

        options = RunOptions.from_params(job.get("params"))
        pipeline = TribePipeline(Path(job["runner_cache_dir"]) / "tribe", reporter, options)
        stats = pipeline.run(items, kind, report)
        # Videos that failed before the predict stage were never reported.
        for it in items:
            if it.error and not it.result and it.video_id is not None:
                try:
                    reporter.client.item_result(job["id"], it.video_id, {"status": "failed", "error": it.error[:2000]})
                except Exception:
                    pass
        log.info("stage timings: %s", json.dumps([s.__dict__ for s in stats]))

"""The CPU runner: downloads, captions, heatmaps, cheap features, engagement curves (and ResMem if installed)."""

from __future__ import annotations

from pathlib import Path

from ..common.runner import BaseRunner, Reporter
from .captions import run_captions
from .download import run_downloads
from .features_jobs import resmem_available, run_audio, run_engagement, run_resmem, run_visual
from .heatmap import run_heatmaps

HANDLERS = {
    "media": run_downloads,
    "captions": run_captions,
    "heatmap": run_heatmaps,
    "visual_features": run_visual,
    "audio_features": run_audio,
    "tribe_engagement": run_engagement,
}
if resmem_available():  # optional: uv sync --project runners --extra resmem
    HANDLERS["resmem"] = run_resmem


class CpuRunner(BaseRunner):
    type = "cpu"
    capabilities = list(HANDLERS)
    default_name = "cpu"

    def stages_for(self, job: dict) -> list[str]:
        names = {"media": "download", "visual_features": "visual features", "audio_features": "audio features"}
        return [names.get(job["type"], job["type"].replace("_", " "))]

    def handle(self, job: dict, reporter: Reporter) -> None:
        self._data_root = Path(job["data_root"])
        reporter.stage(self.stages_for(job)[0])
        HANDLERS[job["type"]](job, reporter)

    def data_root(self) -> Path | None:
        return getattr(self, "_data_root", None)

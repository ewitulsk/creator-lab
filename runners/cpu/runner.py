"""The CPU runner: downloads, captions and (later) cheap per-video features."""

from __future__ import annotations

from pathlib import Path

from ..common.runner import BaseRunner, Reporter
from .captions import run_captions
from .download import run_downloads

HANDLERS = {
    "media": run_downloads,
    "captions": run_captions,
}


class CpuRunner(BaseRunner):
    type = "cpu"
    capabilities = list(HANDLERS)
    default_name = "cpu"

    def stages_for(self, job: dict) -> list[str]:
        return {"media": ["download"], "captions": ["captions"]}.get(job["type"], [job["type"]])

    def handle(self, job: dict, reporter: Reporter) -> None:
        self._data_root = Path(job["data_root"])
        reporter.stage(self.stages_for(job)[0])
        HANDLERS[job["type"]](job, reporter)

    def data_root(self) -> Path | None:
        return getattr(self, "_data_root", None)

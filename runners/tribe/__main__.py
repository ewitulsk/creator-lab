"""TRIBE runner. Run it with BrainThing's Python, from the repo root:

    D:\\VideoAnalysis\\BrainThing\\.venv\\Scripts\\python.exe -m runners.tribe

Benchmark one file without the platform (writes outputs and benchmark.json):

    ...\\python.exe -m runners.tribe --benchmark path\\to\\video.mp4 --out results\\benchmark
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import time
from pathlib import Path

from ..common.config import REPO_ROOT, read_env_file
from ..common.runner import setup_logging

log = logging.getLogger("runner.tribe")


class PrintProgress:
    """Progress sink for benchmark runs: logs the same state the platform would see."""

    def __init__(self):
        self.stage_name, self.video_index, self.pct, self._last = "", 1, 0.0, 0.0

    def stage(self, name, message=""):
        self.stage_name, self.pct = name, 0.0
        log.info("== %s %s", name, message)

    def video(self, index, video_id, message=""):
        self.video_index, self.pct = index, 0.0
        log.info("   video %d: %s", index, message)

    def update(self, stage_percent=None, message=None, force=False):
        if stage_percent is not None:
            self.pct = stage_percent
        now = time.monotonic()
        if force or now - self._last > 15:
            self._last = now
            log.info("   %s on video %d, %.1f%% %s", self.stage_name, self.video_index, self.pct, message or "")


def benchmark(path: Path, out: Path, cache: Path, options) -> None:
    import torch

    from .pipeline import Item, TribePipeline

    kind = "title" if path.suffix.lower() == ".txt" else "video"
    item = Item(key=path.stem, video_id=None, title=path.name, input_path=path.resolve(), output_dir=out.resolve())
    pipe = TribePipeline(cache, PrintProgress(), options)
    t0 = time.monotonic()
    stats = pipe.run([item], kind, lambda it: None)
    total = time.monotonic() - t0
    report = {
        "input": str(path),
        "kind": kind,
        "duration_s": item.duration,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "host": platform.node(),
        "total_seconds": round(total, 1),
        "realtime_factor": round(total / item.duration, 2) if item.duration else None,
        "stages": [s.__dict__ for s in stats],
        "error": item.error,
        "summary": item.result,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("benchmark: %s", json.dumps(report, indent=2))


def main() -> None:
    p = argparse.ArgumentParser(description="creator-lab TRIBE v2 runner (GPU)")
    p.add_argument("--once", action="store_true", help="run at most one job, then exit")
    p.add_argument("--benchmark", type=Path, help="run one video/title file standalone and time each stage")
    p.add_argument("--out", type=Path, default=Path("benchmark-out"))
    p.add_argument("--cache", type=Path, help="feature cache (default DATA_ROOT/runner_cache/tribe)")
    p.add_argument("--no-fp16", action="store_true", help="run Llama and V-JEPA2 in float32")
    p.add_argument("--word-alignment", choices=["fixed", "original"], default="fixed")
    p.add_argument("--features-dir", default="features", help="feature cache folder under the cache dir")
    p.add_argument("--seed-cache", type=Path, help="existing neuralset cache to copy video/audio features from")
    p.add_argument("--path-map", action="append", default=[], help="OLD=NEW path prefix rewrite for seeded keys")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    setup_logging(args.verbose)
    if args.benchmark:
        env = read_env_file(REPO_ROOT / "backend" / ".env")
        data_root = Path(env.get("DATA_ROOT") or REPO_ROOT.parent / "data")
        from .pipeline import RunOptions

        seed = None
        if args.seed_cache:
            seed = {"source": str(args.seed_cache), "path_map": dict(m.split("=", 1) for m in args.path_map)}
        options = RunOptions(
            fp16=not args.no_fp16, word_alignment=args.word_alignment, features_dir=args.features_dir, seed_cache=seed
        )
        benchmark(args.benchmark, args.out, args.cache or data_root / "runner_cache" / "tribe", options)
        return
    from .runner import TribeRunner

    TribeRunner().serve(once=args.once)


if __name__ == "__main__":
    main()

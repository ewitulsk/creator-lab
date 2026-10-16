"""Run the CPU runner: `uv run --project runners python -m runners.cpu` from the repo root."""

from __future__ import annotations

import argparse

from ..common.runner import setup_logging
from .runner import CpuRunner


def main() -> None:
    p = argparse.ArgumentParser(description="creator-lab CPU runner (downloads, captions, features)")
    p.add_argument("--once", action="store_true", help="run at most one job, then exit")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    setup_logging(args.verbose)
    CpuRunner().serve(once=args.once)


if __name__ == "__main__":
    main()

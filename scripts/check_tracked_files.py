#!/usr/bin/env python3
"""Fail if git tracks (or is about to commit) secrets, databases, arrays, model weights or media.

    python scripts/check_tracked_files.py            # everything tracked (CI)
    python scripts/check_tracked_files.py --staged   # tracked + staged (pre-commit hook)

The repo is public: .env files, the SQLite database, *.npy outputs and downloaded videos
must never be committed. Small study results (JSON, CSV, figures) are fine.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import PurePosixPath

BLOCKED_SUFFIXES = {
    # secrets
    ".env",
    # databases
    ".sqlite",
    ".sqlite3",
    ".db",
    # arrays and model weights
    ".npy",
    ".npz",
    ".pt",
    ".pth",
    ".ckpt",
    ".safetensors",
    ".parquet",
    # media
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
    ".avi",
    ".wav",
    ".mp3",
    ".m4a",
    ".flac",
    ".ogg",
}
ALLOWED_NAMES = {".env.example"}


def blocked(path: str) -> str | None:
    p = PurePosixPath(path)
    name = p.name.lower()
    if name in ALLOWED_NAMES:
        return None
    if name == ".env" or name.startswith(".env.") or name.endswith(".env"):
        return "secrets (.env)"
    if p.parts and p.parts[0] == "data":
        return "the data folder"
    if any(name.endswith(s) for s in BLOCKED_SUFFIXES) or ".sqlite-" in name:
        return f"a {p.suffix} file"
    return None


def git_files(staged: bool) -> list[str]:
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.splitlines()
    if staged:
        files += subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
    return sorted(set(files))


def main() -> int:
    bad = [(f, why) for f in git_files("--staged" in sys.argv) if (why := blocked(f))]
    if bad:
        print("Refusing: these files must never be committed to this public repo:", file=sys.stderr)
        for f, why in bad:
            print(f"  {f}  ({why})", file=sys.stderr)
        print("Unstage them (git rm --cached <file>) and check .gitignore.", file=sys.stderr)
        return 1
    print(f"ok: {len(git_files(False))} tracked files, none blocked")
    return 0


if __name__ == "__main__":
    sys.exit(main())

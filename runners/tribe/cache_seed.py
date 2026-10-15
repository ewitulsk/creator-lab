"""Seed the feature cache from an existing neuralset (exca) cache, e.g. BrainThing's fp32 run.

exca stores one folder per extractor config, named by its uid, holding `*-info.jsonl`
index files (one JSON line per cached item, keyed by "#key" = "<filepath>_<offset>_<duration>")
and the `data/*.data` memmaps they point into. Features computed for a file that has
since moved are keyed by the old path, so seeding copies the extractor's folder into
this runner's cache with the path prefix in every key rewritten. The source cache is
only read.

The seeded features keep their original dtype (BrainThing's run was float32); meta.json
records which extractors were seeded, from where, and in which dtype.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

log = logging.getLogger("runner.tribe")


def rewrite_key(key: str, path_map: dict[str, str]) -> str:
    for old, new in path_map.items():
        if key.startswith(old):
            return new + key[len(old) :]
    return key


def copy_uid_folder(src: Path, dst: Path, path_map: dict[str, str]) -> dict:
    """Copy one exca uid folder, rewriting item keys. Returns counts and dtypes found."""
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "data").mkdir(exist_ok=True)
    copied, dtypes = 0, set()
    for f in sorted((src / "data").glob("*")):
        target = dst / "data" / f.name
        if not target.exists():
            shutil.copy2(f, target)
    for name in ("config.yaml", "uid.yaml", "full-uid.yaml"):
        if (src / name).exists() and not (dst / name).exists():
            shutil.copy2(src / name, dst / name)
    for info in sorted(src.glob("*-info.jsonl")):
        lines = []
        for line in info.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if "#key" in rec:
                rec["#key"] = rewrite_key(rec["#key"], path_map)
            dtype = (rec.get("data") or {}).get("dtype")
            if dtype:
                dtypes.add(dtype)
            lines.append(json.dumps(rec))
            copied += 1
        (dst / f"seeded-{info.name}").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"items": copied, "dtypes": sorted(dtypes)}


def seed_feature_cache(extractor, source_root: Path, path_map: dict[str, str]) -> dict:
    """Copy this extractor's cached items from source_root into its own cache folder."""
    target = Path(extractor.infra.uid_folder(create=True))
    method_dir = target.parent.name  # e.g. neuralset.extractors.video.HuggingFaceVideo._get_data,release
    src = Path(source_root) / method_dir / target.name
    if not src.exists():
        log.warning("nothing to seed for %s: %s does not exist", method_dir, src)
        return {"source": str(src), "items": 0, "found": False}
    info = copy_uid_folder(src, target, path_map)
    log.info("seeded %d cached items for %s from %s (%s)", info["items"], method_dir, src, ",".join(info["dtypes"]))
    return {"source": str(src), "found": True, "path_map": path_map, **info}

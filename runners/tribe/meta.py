"""Run metadata for meta.json: config, versions, hashes, hardware, dtypes."""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import subprocess
from importlib import metadata
from pathlib import Path

log = logging.getLogger("runner.tribe")

LIBRARIES = (
    "torch",
    "transformers",
    "neuralset",
    "neuraltrain",
    "tribev2",
    "exca",
    "numpy",
    "pandas",
    "huggingface_hub",
)


def _version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def git_state(path: Path) -> dict:
    try:
        commit = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
        dirty = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"], capture_output=True, text=True, check=True
        )
        return {
            "commit": commit.stdout.strip(),
            "modified_files": [ln[3:] for ln in dirty.stdout.splitlines() if ln.strip()],
        }
    except Exception as err:
        return {"error": str(err)}


def file_sha256(path: Path, cache_file: Path) -> str:
    """sha256 of a (large) file, cached by path, size and mtime."""
    st = path.stat()
    key = f"{path}|{st.st_size}|{int(st.st_mtime)}"
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    if key not in cache:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while block := f.read(8 * 1024 * 1024):
                h.update(block)
        cache[key] = h.hexdigest()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache))
    return cache[key]


def collect_meta(xp, options, seeded: dict, runner_version: str, events_version: str) -> dict:
    import torch

    meta: dict = {
        "runner": {"version": runner_version, "events_version": events_version, "options": options.__dict__},
        "model": {"id": "facebook/tribev2", "checkpoint": "best.ckpt"},
        "versions": {name: _version(name) for name in LIBRARIES} | {"python": platform.python_version()},
        "hardware": {"host": platform.node()},
        "dtypes": {
            "whisperx": "float16 (uvx whisperx, faster-whisper large-v3)",
            "llama": options.half_dtype if options.fp16 else "float32",
            "vjepa2": options.half_dtype if options.fp16 else "float32",
            "w2v_bert": "float32",
            "tribe": "float32",
            "outputs": "float16 preds; features stored float16",
        },
        "patches": [
            f"{options.half_dtype} loading for Llama-3.2-3B and V-JEPA2 (inputs cast in, outputs cast back to float32)"
            if options.fp16
            else "half precision off",
            f"V-JEPA2 fast path: one sequential decode per video, CPU prefetch, batch {options.video_batch_size}"
            if options.fast_video
            else "V-JEPA2 via neuralset's per-clip moviepy path",
            f"Llama word features sliced on the GPU, batch {options.text_batch_size}"
            if options.fast_text
            else "Llama via neuralset's loop (batch 4)",
            "words transcribed before chunking" if options.word_alignment == "fixed" else "tribev2 demo word order",
            "extractor models freed after each stage",
            "tqdm and WhisperX progress hooks",
            "data.num_workers=0",
        ],
        "features_seeded_from_cache": seeded,
    }
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        meta["hardware"] |= {
            "gpu": props.name,
            "vram_gb": round(props.total_memory / 1024**3, 1),
            "cuda": torch.version.cuda,
        }
    try:
        from huggingface_hub import hf_hub_download

        ckpt = Path(hf_hub_download("facebook/tribev2", "best.ckpt"))
        cfg = Path(hf_hub_download("facebook/tribev2", "config.yaml"))
        meta["model"] |= {
            "revision": ckpt.parent.name,
            "checkpoint_sha256": file_sha256(ckpt, Path(xp.cache_folder) / "checkpoint_hashes.json"),
            "config_sha256": hashlib.sha256(cfg.read_bytes()).hexdigest(),
        }
    except Exception as err:
        meta["model"]["hash_error"] = str(err)
    try:
        import tribev2

        meta["versions"]["tribev2_git"] = git_state(Path(tribev2.__file__).resolve().parents[1])
    except Exception as err:
        meta["versions"]["tribev2_git"] = {"error": str(err)}
    d = xp.data
    meta["data"] = {
        "tr_s": float(d.TR),
        "neuro_frequency_hz": float(d.neuro.frequency),
        "feature_frequency_hz": d.frequency,
        "duration_trs": d.duration_trs,
        "overlap_trs": d.overlap_trs_train,
        "stride_drop_incomplete": d.stride_drop_incomplete,
        "features_to_use": list(d.features_to_use),
        "remove_empty_segments": getattr(xp, "remove_empty_segments", None),
        "neuro_offset_s": getattr(d.neuro, "offset", None),
    }
    try:
        meta["config"] = json.loads(json.dumps(xp.model_dump(), default=str))
    except Exception as err:
        meta["config"] = {"error": str(err)}
    return meta

"""Runtime patches for TRIBE v2 / neuralset. BrainThing's Meta code is never edited.

1. tqdm hook: every progress bar neuralset/tribev2 opens feeds the live stage percent.
2. fp16 loading: Llama-3.2-3B and V-JEPA2 load in float16. Inputs are cast to the model
   dtype on the way in and outputs back to float32 on the way out, so cached features and
   the TRIBE head see the same dtype as before.
3. WhisperX progress: the transcription subprocess runs with --print_progress, and its
   percentages are forwarded.
4. GPU freeing: extractors keep their model in `_model` after `prepare`; drop it (and the
   CUDA cache) between stages so only one model is ever on the GPU.
"""

from __future__ import annotations

import gc
import logging
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("runner.tribe")

# ------------------------------------------------------------------ 1. tqdm hook

_listener: Callable[[float, str], None] | None = None
_live_bars: list = []
_bars_lock = threading.Lock()


def set_progress_listener(fn: Callable[[float, str], None] | None) -> None:
    """fn(fraction 0..1, description of the innermost bar)."""
    global _listener
    _listener = fn
    with _bars_lock:
        _live_bars.clear()


def _nested_fraction() -> tuple[float, str]:
    """Combine nested bars: outer n/total plus the inner bar's share of one outer step."""
    with _bars_lock:
        bars = [b for b in _live_bars if getattr(b, "total", None)]
    frac, scale, desc = 0.0, 1.0, ""
    for b in bars:
        frac += scale * min(b.n, b.total) / b.total
        scale /= b.total
        desc = b.desc or desc
    return min(frac, 1.0), desc


def install_tqdm_hook() -> None:
    import tqdm
    import tqdm.auto
    import tqdm.std

    original = tqdm.std.tqdm
    if getattr(original, "_creator_lab_hooked", False):
        return

    class HookedTqdm(original):  # type: ignore[misc,valid-type]
        _creator_lab_hooked = True

        def __init__(self, *args, **kwargs):
            kwargs.setdefault("mininterval", 1.0)
            super().__init__(*args, **kwargs)
            with _bars_lock:
                _live_bars.append(self)

        def update(self, n=1):
            out = super().update(n)
            if _listener is not None:
                frac, desc = _nested_fraction()
                _listener(frac, desc)
            return out

        def close(self):
            with _bars_lock:
                if self in _live_bars:
                    _live_bars.remove(self)
            return super().close()

    tqdm.tqdm = HookedTqdm
    tqdm.std.tqdm = HookedTqdm
    tqdm.auto.tqdm = HookedTqdm
    # Modules that did `from tqdm import tqdm` at import time hold the old class.
    for mod in list(sys.modules.values()):
        if mod is not None and getattr(mod, "tqdm", None) is original:
            try:
                mod.tqdm = HookedTqdm
            except Exception:
                pass


# ------------------------------------------------------------------ 2. fp16 loading

FP16_MODELS = ("llama", "vjepa2")
_fp16_enabled = True
_half_dtype = "float16"  # or "bfloat16"


def set_fp16(enabled: bool, dtype: str = "float16") -> None:
    global _fp16_enabled, _half_dtype
    if dtype not in ("float16", "bfloat16"):
        raise ValueError("half precision dtype must be float16 or bfloat16")
    _fp16_enabled = enabled
    _half_dtype = dtype


def half_dtype():
    import torch

    return getattr(torch, _half_dtype)


def _to_float32(obj):
    import torch

    if isinstance(obj, torch.Tensor):
        return obj.float() if obj.is_floating_point() and obj.dtype != torch.float32 else obj
    if isinstance(obj, tuple):
        return tuple(_to_float32(x) for x in obj)
    if isinstance(obj, list):
        return [_to_float32(x) for x in obj]
    if hasattr(obj, "keys") and hasattr(obj, "__setitem__"):  # transformers ModelOutput / dict
        for k in list(obj.keys()):
            obj[k] = _to_float32(obj[k])
        return obj
    return obj


def _cast_inputs(dtype):
    import torch

    def cast(x):
        if isinstance(x, torch.Tensor) and x.is_floating_point() and x.dtype != dtype:
            return x.to(dtype)
        if isinstance(x, (list, tuple)):
            return type(x)(cast(v) for v in x)
        return x

    def hook(_module, args, kwargs):
        return tuple(cast(a) for a in args), {k: cast(v) for k, v in kwargs.items()}

    return hook


def make_fp16(model, dtype=None):
    """Wrap a half-precision model so callers keep seeing float32 tensors."""
    pre = _cast_inputs(dtype if dtype is not None else half_dtype())

    def post(_module, _args, out):
        return _to_float32(out)

    pre._creator_lab_cast = True
    post._creator_lab_cast = True
    model.register_forward_pre_hook(pre, with_kwargs=True)
    model.register_forward_hook(post)
    return model


def install_fp16_loading() -> None:
    import transformers

    for cls_name in ("AutoModel", "AutoModelForCausalLM"):
        cls = getattr(transformers, cls_name)
        original = cls.from_pretrained.__func__ if hasattr(cls.from_pretrained, "__func__") else None
        if original is None or getattr(original, "_creator_lab_fp16", False):
            continue

        def from_pretrained(klass, name, *args, _orig=original, **kwargs):
            wants_fp16 = _fp16_enabled and any(k in str(name).lower() for k in FP16_MODELS)
            if wants_fp16:
                kwargs.pop("torch_dtype", None)
                kwargs["dtype"] = half_dtype()
                log.info("loading %s in %s", name, _half_dtype)
            model = _orig(klass, name, *args, **kwargs)
            return make_fp16(model, half_dtype()) if wants_fp16 else model

        from_pretrained._creator_lab_fp16 = True
        cls.from_pretrained = classmethod(from_pretrained)


# ------------------------------------------------------------------ 3. WhisperX progress

_whisper_progress: Callable[[float, str], None] | None = None
PROGRESS_RE = re.compile(r"Progress:\s*([\d.]+)%")


def set_whisper_progress(fn: Callable[[float, str], None] | None) -> None:
    global _whisper_progress
    _whisper_progress = fn


def install_whisperx_progress() -> None:
    """Same transcription as tribev2's ExtractWordsFromAudio, with live progress."""
    import json
    import os
    import tempfile

    import pandas as pd
    import torch
    from tribev2.eventstransforms import ExtractWordsFromAudio

    def transcribe(wav_filename: Path, language: str) -> pd.DataFrame:
        from huggingface_hub import snapshot_download

        codes = dict(english="en", french="fr", spanish="es", dutch="nl", chinese="zh")
        if language not in codes:
            raise ValueError(f"Language {language} not supported")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        whisper_model = snapshot_download("Systran/faster-whisper-large-v3")
        with tempfile.TemporaryDirectory() as output_dir:
            cmd = [
                "uvx",
                "whisperx",
                str(wav_filename),
                "--model",
                whisper_model,
                "--language",
                codes[language],
                "--device",
                device,
                "--compute_type",
                "float16",
                "--batch_size",
                "16",
                *(["--align_model", "WAV2VEC2_ASR_LARGE_LV60K_960H"] if language == "english" else []),
                "--output_dir",
                output_dir,
                "--output_format",
                "json",
                "--print_progress",
                "True",
            ]
            env = {k: v for k, v in os.environ.items() if k != "MPLBACKEND"}
            env.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, errors="replace"
            )
            assert proc.stdout is not None
            phase, last, tail = 0, 0.0, []
            for line in proc.stdout:
                tail = (tail + [line.rstrip()])[-60:]
                m = PROGRESS_RE.search(line)
                if m and _whisper_progress is not None:
                    pct = float(m.group(1))
                    if pct + 5 < last:  # second pass (alignment) starts back at 0
                        phase = 1
                    last = pct
                    overall = 0.7 * pct if phase == 0 else 70 + 0.3 * pct
                    _whisper_progress(overall, "transcribing" if phase == 0 else "aligning words")
            if proc.wait() != 0:
                raise RuntimeError("whisperx failed:\n" + "\n".join(tail))
            transcript = json.loads((Path(output_dir) / f"{wav_filename.stem}.json").read_text(encoding="utf-8"))
        words = []
        for i, segment in enumerate(transcript["segments"]):
            sentence = segment["text"].replace('"', "")
            for word in segment["words"]:
                if "start" not in word:
                    continue
                words.append(
                    {
                        "text": word["word"].replace('"', ""),
                        "start": word["start"],
                        "duration": word["end"] - word["start"],
                        "sequence_id": i,
                        "sentence": sentence,
                    }
                )
        return pd.DataFrame(words)

    ExtractWordsFromAudio._get_transcript_from_audio = staticmethod(transcribe)


# ------------------------------------------------------------------ 4. GPU freeing


def free_extractor(extractor) -> None:
    """Drop an extractor's cached model(s) and empty the CUDA cache."""
    import torch

    for target in (extractor, getattr(extractor, "image", None)):
        if target is None:
            continue
        for attr in ("_model", "_tokenizer", "_processor"):
            # delattr only: the text extractor reloads when `_model` is absent, but would
            # break on a `_model` set to None.
            if hasattr(target, attr):
                try:
                    delattr(target, attr)
                except Exception:
                    pass
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def install_all() -> None:
    install_fp16_loading()
    install_whisperx_progress()
    install_tqdm_hook()

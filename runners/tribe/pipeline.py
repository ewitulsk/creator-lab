"""TRIBE v2, staged across a batch of videos with one model on the GPU at a time.

    1. Audio + WhisperX     extract each video's audio, transcribe with WhisperX large-v3 (fp16)
    2. Llama-3.2-3B         word features (fp16), then unload
    3. w2v-BERT 2.0         audio features, then unload
    4. V-JEPA2              video features (fp16), then unload
    5. TRIBE predict        the brain model, from the feature cache

Features are cached per event (exca), so an interrupted job resumes where it stopped.

Fix for the "2.8 timesteps per second" problem: tribev2's demo pipeline chunks the audio
into 30-60 s pieces *before* transcribing, and ExtractWordsFromAudio then copies the whole
file's transcript into every chunk, shifted by chunk start + offset. A 658 s video gets
its words duplicated 11 times out to ~1,900 s, so TRIBE predicts ~1,900 rows (and the
text features inside the real 658 s are wrong too). Here words are extracted from the
full, unchunked audio first and the chunking happens afterwards, which gives one row per
second of video. Text inputs (TTS of a title) are shorter than one chunk, so they were
never affected.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import patches
from .outputs import Predicted, write_run

log = logging.getLogger("runner.tribe")

VIDEO_STAGES = ["Load TRIBE", "Audio + WhisperX", "Llama-3.2-3B", "w2v-BERT 2.0", "V-JEPA2", "TRIBE predict"]
TITLE_STAGES = ["Load TRIBE", "TTS + WhisperX", "Llama-3.2-3B", "w2v-BERT 2.0", "TRIBE predict"]
EXTRACTOR_STAGES = {"Llama-3.2-3B": "text_feature", "w2v-BERT 2.0": "audio_feature", "V-JEPA2": "video_feature"}
EVENTS_VERSION = "2"  # bump when build_*_events changes
RUNNER_VERSION = "1.1"


@dataclass
class RunOptions:
    """Per-job switches (job params override the defaults)."""

    fp16: bool = True  # Llama-3.2-3B and V-JEPA2 in float16
    # fixed: transcribe the whole audio, then chunk. original: tribev2's demo order
    # (chunk, then copy the full transcript into every chunk), kept for comparison only.
    word_alignment: str = "fixed"
    features_dir: str = "features"  # feature cache folder under the runner cache
    # Seed the feature cache from an existing neuralset cache (e.g. BrainThing's fp32 run):
    # {"source": dir, "path_map": {old_prefix: new_prefix}, "extractors": ["video_feature", ...]}
    seed_cache: dict | None = None

    @classmethod
    def from_params(cls, params: dict | None) -> RunOptions:
        params = params or {}
        known = {k: params[k] for k in ("fp16", "word_alignment", "features_dir", "seed_cache") if k in params}
        opts = cls(**known)
        if opts.word_alignment not in ("fixed", "original"):
            raise ValueError("word_alignment must be fixed or original")
        return opts


class Progress:
    """What the pipeline needs from a progress sink (runners.common.Reporter fits)."""

    def stage(self, name: str, message: str = "") -> None: ...
    def video(self, index: int, video_id: int | None, message: str = "") -> None: ...
    def update(self, stage_percent: float | None = None, message: str | None = None, force: bool = False) -> None: ...


@dataclass
class Item:
    key: str  # external id; names cache files
    video_id: int | None
    title: str
    input_path: Path  # video.mp4 or title.txt
    output_dir: Path
    events: object = None  # pandas DataFrame once built
    duration: float | None = None
    error: str | None = None
    result: dict = field(default_factory=dict)


@dataclass
class StageStat:
    stage: str
    seconds: float
    peak_vram_gb: float | None


class TribePipeline:
    def __init__(self, cache_dir: Path, progress: Progress, options: RunOptions | None = None):
        self.cache_dir = Path(cache_dir)
        self.progress = progress
        self.options = options or RunOptions()
        self.xp = None
        self.stats: list[StageStat] = []
        self.atlas = None
        self.meta: dict = {}

    # ------------------------------------------------------------ helpers

    def _timed(self, name: str, fn: Callable[[], None]) -> None:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        t0 = time.monotonic()
        self.progress.stage(name)
        fn()
        peak = torch.cuda.max_memory_allocated() / 1024**3 if torch.cuda.is_available() else None
        stat = StageStat(name, round(time.monotonic() - t0, 1), round(peak, 2) if peak is not None else None)
        self.stats.append(stat)
        log.info("stage %s took %.0fs (peak VRAM %s GB)", name, stat.seconds, stat.peak_vram_gb)

    def _listen(self) -> None:
        patches.set_progress_listener(lambda frac, desc: self.progress.update(stage_percent=100 * frac, message=desc))
        patches.set_whisper_progress(lambda pct, msg: self.progress.update(stage_percent=pct, message=msg))

    @staticmethod
    def _each(items: list[Item]) -> list[tuple[int, Item]]:
        return [(i, it) for i, it in enumerate(items, 1) if it.error is None]

    # ------------------------------------------------------------ stages

    def load(self) -> None:
        import torch  # noqa: F401  (import before tribev2 so CUDA init order is stable)
        from tribev2 import TribeModel

        patches.install_all()
        patches.set_fp16(self.options.fp16)
        features = self.cache_dir / self.options.features_dir
        features.mkdir(parents=True, exist_ok=True)
        self.progress.update(message="loading the TRIBE checkpoint")
        # The brain model stays on the CPU until the predict stage, so feature models get the whole GPU.
        self.xp = TribeModel.from_pretrained(
            "facebook/tribev2",
            cache_folder=str(features),
            device="cpu",
            config_update={"data.num_workers": 0},
        )
        patches.install_tqdm_hook()  # again: tribev2 imports modules that import tqdm
        seeded = {}
        if self.options.seed_cache:
            from .cache_seed import seed_feature_cache

            for name in self.options.seed_cache.get("extractors", ["audio_feature", "video_feature"]):
                seeded[name] = seed_feature_cache(
                    getattr(self.xp.data, name),
                    Path(self.options.seed_cache["source"]),
                    self.options.seed_cache.get("path_map", {}),
                )
        from .meta import collect_meta

        self.meta = collect_meta(self.xp, self.options, seeded, RUNNER_VERSION, EVENTS_VERSION)
        from .atlas import load_atlas

        try:
            self.atlas = load_atlas(str(self.cache_dir / "atlas"))
        except Exception as err:  # regions are a bonus; never lose the predictions over them
            log.warning("could not load the HCP atlas (%s); regions.parquet will be skipped", err)

    def build_video_events(self, item: Item):
        import pandas as pd
        from neuralset.events.transforms import (
            AddContextToWords,
            AddSentenceToWords,
            AddText,
            ChunkEvents,
            ExtractAudioFromVideo,
            RemoveMissing,
        )
        from neuralset.events.utils import standardize_events
        from tribev2.eventstransforms import ExtractWordsFromAudio

        event = {
            "type": "Video",
            "filepath": str(item.input_path),
            "start": 0,
            "timeline": f"v-{item.key}",
            "subject": "default",
        }
        events = standardize_events(pd.DataFrame([event]))
        if self.options.word_alignment == "original":
            from tribev2.demo_utils import get_audio_and_text_events

            return get_audio_and_text_events(events)  # the buggy demo order, for comparison
        # Transcribe the whole audio BEFORE chunking (see the module docstring).
        for transform in (
            ExtractAudioFromVideo(),
            ExtractWordsFromAudio(),
            ChunkEvents(event_type_to_chunk="Audio", max_duration=60, min_duration=30),
            ChunkEvents(event_type_to_chunk="Video", max_duration=60, min_duration=30),
            AddText(),
            AddSentenceToWords(max_unmatched_ratio=0.05),
            AddContextToWords(sentence_only=False, max_context_len=1024, split_field=""),
            RemoveMissing(),
        ):
            events = transform(events)
        return standardize_events(events)

    def build_title_events(self, item: Item):
        return self.xp.get_events_dataframe(text_path=str(item.input_path))

    def events_stage(self, items: list[Item], kind: str) -> None:
        import pandas as pd

        from runners.common.media import probe

        self._listen()
        cache = self.cache_dir / "events"
        cache.mkdir(parents=True, exist_ok=True)
        for i, it in self._each(items):
            self.progress.video(i, it.video_id, f"{'transcribing' if kind == 'video' else 'speaking'} {it.title[:60]}")
            path = (
                cache
                / f"{kind}-{it.key}-v{EVENTS_VERSION}-{self.options.word_alignment}-{it.input_path.stat().st_size}.pkl"
            )
            try:
                if path.exists():
                    it.events = pd.read_pickle(path)
                else:
                    it.events = self.build_video_events(it) if kind == "video" else self.build_title_events(it)
                    it.events.to_pickle(path)
                if kind == "video":
                    it.duration = probe(it.input_path)["duration"]
                else:
                    it.duration = float(it.events.start.max() + it.events.duration.max())
                n_words = int((it.events.type == "Word").sum())
                log.info("%s: %d words, %.0fs", it.key, n_words, it.duration or 0)
            except patches_cancel_types() as err:
                raise err
            except Exception as err:
                log.exception("events failed for %s", it.key)
                it.error = f"{type(err).__name__}: {err}"
            self.progress.update(stage_percent=100, force=True)

    def extractor_stage(self, items: list[Item], stage: str) -> None:
        from neuralset.events.etypes import EventTypesHelper

        extractor = getattr(self.xp.data, EXTRACTOR_STAGES[stage])
        types = EventTypesHelper(extractor.event_types).names
        self._listen()
        try:
            for i, it in self._each(items):
                self.progress.video(i, it.video_id, f"{stage} on {it.title[:60]}")
                if not it.events.type.isin(types).any():
                    continue
                try:
                    extractor.prepare(it.events)
                except patches_cancel_types():
                    raise
                except Exception as err:
                    log.exception("%s failed for %s", stage, it.key)
                    it.error = f"{stage}: {type(err).__name__}: {err}"
                self.progress.update(stage_percent=100, force=True)
        finally:
            patches.free_extractor(extractor)

    def predict_stage(self, items: list[Item], on_done: Callable[[Item], None]) -> None:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.xp._model.to(device)
        self._listen()
        try:
            for i, it in self._each(items):
                self.progress.video(i, it.video_id, f"TRIBE on {it.title[:60]}")
                try:
                    predicted = self._predict(it, device)
                    it.result = write_run(
                        it.output_dir,
                        predicted,
                        it.events,
                        it.duration,
                        self.atlas,
                        self.run_meta(it),
                    )
                except patches_cancel_types():
                    raise
                except Exception as err:
                    log.exception("predict failed for %s", it.key)
                    it.error = f"TRIBE predict: {type(err).__name__}: {err}"
                on_done(it)
                self.progress.update(stage_percent=100, force=True)
        finally:
            self.xp._model.to("cpu")
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def _predict(self, it: Item, device: str) -> Predicted:
        """TRIBE's own predict loop, but keeping every row (kept or not), each row's time,
        and the per-timestep pre-brain features it was fed."""
        import torch
        from einops import rearrange

        xp = self.xp
        model = xp._model
        tr = xp.data.TR
        loader = xp.data.get_loaders(events=it.events, split_to_build="all")["all"]
        for name in ("text_feature", "audio_feature", "video_feature"):
            patches.free_extractor(getattr(xp.data, name))  # get_loaders may have loaded one to check the cache
        out = Predicted()
        seg_index = 0
        n_batches = len(loader)
        with torch.inference_mode():
            for b_i, batch in enumerate(loader):
                batch = batch.to(device)
                y = model(batch).detach().float().cpu().numpy()  # (b, d, t)
                y = rearrange(y, "b d t -> (b t) d")
                rows_here = []
                for seg in batch.segments:
                    for t in np.arange(0, seg.duration - 1e-2, tr):
                        row = seg.copy(offset=t, duration=tr)
                        types = {type(e).__name__ for e in row.ns_events}
                        rows_here.append(
                            {
                                "row": len(out.table) + len(rows_here),
                                "batch": b_i,
                                "segment": seg_index,
                                "segment_start_s": float(seg.start),
                                "segment_duration_s": float(seg.duration),
                                "start_s": float(row.start),
                                "duration_s": float(tr),
                                "kept": bool(types),  # TRIBE's predict() drops rows without events
                                "n_events": len(row.ns_events),
                                "has_video": "Video" in types,
                                "has_audio": "Audio" in types,
                                "has_word": "Word" in types,
                                "has_text": bool(types & {"Text", "Sentence"}),
                                "event_types": ",".join(sorted(types)),
                            }
                        )
                    seg_index += 1
                if len(rows_here) != y.shape[0]:
                    raise ValueError(f"{len(rows_here)} rows for {y.shape[0]} predictions")
                out.table += rows_here
                out.rows.append(y)
                for mod in ("text", "audio", "video"):
                    if mod not in batch.data:
                        continue
                    x = batch.data[mod].float().cpu()  # (B, L, D, T) or (B, D, T)
                    t_len = x.shape[-1]
                    for b, seg in enumerate(batch.segments):
                        times = seg.start + (np.arange(t_len) + 0.5) * seg.duration / t_len
                        values = np.moveaxis(x[b].numpy(), -1, 0)  # (T, L, D)
                        out.features.setdefault(mod, []).append((times, values))
                self.progress.update(stage_percent=100 * (b_i + 1) / max(n_batches, 1))
        out.frequency = {
            mod: float(getattr(getattr(xp.data, f"{mod}_feature"), "frequency", 0) or 0) for mod in out.features
        }
        out.tr = float(tr)
        return out

    def run_meta(self, it: Item) -> dict:
        meta = dict(self.meta)
        meta["input"] = {"path": str(it.input_path), "duration_s": it.duration, "key": it.key}
        meta["stages"] = [s.__dict__ for s in self.stats]
        return meta

    # ------------------------------------------------------------ the whole run

    def run(self, items: list[Item], kind: str, on_done: Callable[[Item], None]) -> list[StageStat]:
        stages = VIDEO_STAGES if kind == "video" else TITLE_STAGES
        self._timed(stages[0], self.load)
        self._timed(stages[1], lambda: self.events_stage(items, kind))
        for stage in stages[2:-1]:
            self._timed(stage, lambda stage=stage: self.extractor_stage(items, stage))
        self._timed(stages[-1], lambda: self.predict_stage(items, on_done))
        patches.set_progress_listener(None)
        patches.set_whisper_progress(None)
        return self.stats


def patches_cancel_types() -> tuple[type[BaseException], ...]:
    """Exceptions that must abort the whole job instead of failing one video."""
    from runners.common.client import JobLost
    from runners.common.runner import JobCancelled

    return (JobCancelled, JobLost, KeyboardInterrupt)

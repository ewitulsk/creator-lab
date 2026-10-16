"""Faster V-JEPA2 video features (a runtime patch: neuralset itself is not edited).

neuralset's HuggingFaceVideo encodes one 4 s, 64-frame clip every 0.5 s, one clip per
forward pass, and reads each frame through moviepy. moviepy seeks backwards for every clip
and restarts ffmpeg, so every second of 1080p video is decoded ~8 times on one CPU thread
while the GPU waits. This replaces the body of `HuggingFaceVideo._get_data` (the function
exca's cache wraps, so cache keys and cached files are unchanged) with:

  * one sequential ffmpeg decode per video event, using moviepy's own command line
    (same scale filter, sws flags and pixel format) and moviepy's frame numbering
    (frame = int(fps * t + 1e-5)), keeping a sliding window of decoded frames;
  * a background thread that assembles clips and runs the video processor while the
    GPU works on the previous batch (bounded queue, pinned memory);
  * `batch_size` clips per forward pass;
  * per-layer token means in float32 straight from the half-precision hidden states,
    instead of first copying all 41 layers to float32 (~1.9 GB per clip);
  * `skip_predictor=True`: transformers runs V-JEPA2's predictor head by default, but the
    features come from the encoder's hidden states only.

The frames, processor and aggregation are the same as neuralset's, so features match the
original path up to GPU batching noise. `compare_with_original` checks that on real media.
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial

import numpy as np

log = logging.getLogger("runner.tribe")


@dataclass
class FastVideoOptions:
    batch_size: int = 1
    prefetch: int = 8  # clips prepared ahead of the GPU
    cudnn_benchmark: bool = True


_options = FastVideoOptions()
_installed = False
# Last run's throughput, for the speed report: {"clips", "seconds", "video_seconds", ...}
last_stats: dict = {}


def configure(**kwargs) -> FastVideoOptions:
    global _options
    _options = FastVideoOptions(**{**_options.__dict__, **kwargs})
    return _options


# ------------------------------------------------------------------ frames


def frame_number(fps: float, t: float) -> int:
    """moviepy's FFMPEG_VideoReader.get_frame_number."""
    return int(fps * t + 0.00001)


class FrameWindow:
    """Decode a video once, front to back, and serve frames by index.

    Requests must be roughly increasing (each clip may reach back up to `keep` frames
    before the newest request). Past the end of the stream the last frame is repeated,
    like moviepy does.
    """

    def __init__(self, read_frame: Callable[[], np.ndarray | None], first_index: int, keep: int):
        self._read = read_frame
        self._next = first_index  # index of the next frame the decoder will produce
        self._frames: dict[int, np.ndarray] = {}
        self._keep = keep
        self._last: np.ndarray | None = None
        self.decoded = 0

    def get(self, index: int) -> np.ndarray:
        if index in self._frames:
            return self._frames[index]
        if index < self._next:
            raise ValueError(f"frame {index} was already dropped (window starts at {min(self._frames, default=None)})")
        while self._next <= index:
            frame = self._read()
            if frame is None:  # end of stream: repeat the last frame
                frame = self._last
                if frame is None:
                    raise RuntimeError("the video has no frames")
            else:
                self.decoded += 1
            self._last = frame
            self._frames[self._next] = frame
            self._next += 1
        for old in [i for i in self._frames if i < index - self._keep]:
            del self._frames[old]
        return self._frames[index]


class FfmpegFrames:
    """moviepy's reader command, without the per-clip seeks."""

    def __init__(self, reader, start_index: int):
        from moviepy.config import FFMPEG_BINARY
        from moviepy.tools import cross_platform_popen_params, ffmpeg_escape_filename

        self.size = tuple(reader.size)
        self.depth = int(getattr(reader, "depth", 3))
        self.nbytes = self.depth * self.size[0] * self.size[1]
        fps = reader.fps
        start_time = start_index * (1 / fps) - 0.00001 if start_index else 0.0
        if start_time:
            offset = min(1, start_time)
            i_arg = [
                "-ss",
                f"{start_time - offset:.6f}",
                "-i",
                ffmpeg_escape_filename(reader.filename),
                "-ss",
                f"{offset:.6f}",
            ]
        else:
            i_arg = ["-i", ffmpeg_escape_filename(reader.filename)]
        cmd = [FFMPEG_BINARY, *i_arg, "-loglevel", "error", "-f", "image2pipe"]
        cmd += ["-vf", f"scale={self.size[0]}:{self.size[1]}", "-sws_flags", reader.resize_algo]
        cmd += ["-pix_fmt", reader.pixel_format, "-vcodec", "rawvideo", "-"]
        params = cross_platform_popen_params(
            {
                "bufsize": self.nbytes * 4 + 100,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            }
        )
        self.proc = subprocess.Popen(cmd, **params)

    def read(self) -> np.ndarray | None:
        assert self.proc.stdout is not None
        buf = self.proc.stdout.read(self.nbytes)
        if len(buf) != self.nbytes:
            return None
        w, h = self.size
        return np.frombuffer(buf, dtype="uint8").reshape(h, w, self.depth)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()


# ------------------------------------------------------------------ clips


def clip_plan(duration: float, freq: float, clip_duration: float | None, num_frames: int, expect_frames: int):
    """neuralset's clip end times and the per-clip frame offsets (oldest first)."""
    times = np.linspace(0, duration, expect_frames + 1)[1:]
    span = 1 / freq if clip_duration is None else clip_duration
    subtimes = [k / num_frames * span for k in reversed(range(num_frames))]
    return times, subtimes


def _clip_indices(times, subtimes, start: float, fps: float) -> list[list[int]]:
    # moviepy: subclip time t -> source time t + start -> frame int(fps * (t + start) + 1e-5)
    return [[frame_number(fps, max(0, t - t2) + start) for t2 in subtimes] for t in times]


class _Prefetcher:
    """Builds processed clips on a background thread."""

    _DONE = object()

    def __init__(self, make: Callable[[int], object], n: int, depth: int):
        self._q: queue.Queue = queue.Queue(maxsize=max(depth, 1))
        self._make = make
        self._n = n
        self._stop = threading.Event()
        self.error: BaseException | None = None
        self.wait_s = 0.0  # time the GPU side spent waiting on the CPU side
        self._t = threading.Thread(target=self._run, name="vjepa-prefetch", daemon=True)
        self._t.start()

    def _run(self) -> None:
        try:
            for k in range(self._n):
                if self._stop.is_set():
                    return
                item = self._make(k)
                while not self._stop.is_set():
                    try:
                        self._q.put((k, item), timeout=0.5)
                        break
                    except queue.Full:
                        continue
        except BaseException as err:  # surfaced on the consumer side
            self.error = err
        finally:
            self._q.put(self._DONE)

    def __iter__(self) -> Iterator[tuple[int, object]]:
        while True:
            t0 = time.monotonic()
            got = self._q.get()
            self.wait_s += time.monotonic() - t0
            if got is self._DONE:
                if self.error is not None:
                    raise self.error
                return
            yield got

    def close(self) -> None:
        self._stop.set()
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        self._t.join(timeout=10)


def _strip_cast_hooks(model) -> None:
    """Drop the fp16 patch's output hook: the fast path casts per layer itself."""
    for hooks in (model._forward_hooks,):
        for key, fn in list(hooks.items()):
            if getattr(fn, "_creator_lab_cast", False):
                del hooks[key]


def _prepare_clip(processor, window: FrameWindow, indices: list[list[int]], k: int) -> dict:
    """CPU side, on the prefetch thread: the clip's frames through the video processor."""
    import torch
    from neuralset.extractors.image import _fix_pixel_values

    inputs = processor(videos=[window.get(i) for i in indices[k]], return_tensors="pt")
    _fix_pixel_values(inputs)
    pin = torch.cuda.is_available()
    return {key: v.pin_memory() if pin else v for key, v in inputs.items()}


class _BatchEncoder:
    """GPU side: several clips per forward pass, token means per selected layer in float32."""

    def __init__(self, net, device, dtype, layer_ids: list[int], n_clips: int, bar):
        import inspect

        self.net, self.device, self.dtype, self.layer_ids = net, device, dtype, layer_ids
        # transformers' VJEPA2Model also runs its 12-layer predictor unless told not to; the
        # features only use the encoder's hidden states, so skipping it changes nothing.
        self.extra = {"skip_predictor": True} if "skip_predictor" in inspect.signature(net.forward).parameters else {}
        self.n_clips = n_clips
        self.bar = bar
        self.pending: list[tuple[int, dict]] = []
        self.output: np.ndarray | None = None

    def add(self, k: int, inputs: dict) -> None:
        self.pending.append((k, inputs))

    def flush(self) -> None:
        import torch

        if not self.pending:
            return
        batch = {}
        for key in self.pending[0][1]:
            v = torch.cat([p[1][key] for p in self.pending]).to(self.device, non_blocking=True)
            batch[key] = v.to(self.dtype) if v.is_floating_point() else v
        with torch.inference_mode():
            states = self.net(**batch, **self.extra).hidden_states
            pooled = torch.stack([states[i].float().mean(dim=1) for i in self.layer_ids], dim=1).cpu().numpy()
        for (k, _), embd in zip(self.pending, pooled, strict=True):
            if self.output is None:
                self.output = np.zeros((self.n_clips,) + embd.shape)
            self.output[k] = embd
        self.bar.update(len(self.pending))
        self.pending.clear()


def fast_get_data(self, events) -> Iterator:
    """Drop-in body for HuggingFaceVideo._get_data, V-JEPA2 only (see the module doc)."""
    import torch
    from neuralset import base as nsbase
    from neuralset.extractors.video import _HFVideoModel
    from tqdm import tqdm

    opts = _options
    if opts.cudnn_benchmark:
        torch.backends.cudnn.benchmark = True
    model = _HFVideoModel(
        model_name=self.image.model_name,
        pretrained=self.image.pretrained,
        layer_type=self.layer_type,
        num_frames=self.num_frames,
    )
    if model.model.device.type == "cpu":
        model.model.to(self.image.device)
    net = model.model
    _strip_cast_hooks(net)
    device = next(net.parameters()).device
    dtype = next(net.parameters()).dtype
    log.info(
        "V-JEPA2 fast path: %s, attention=%s, batch %d, prefetch %d",
        dtype,
        getattr(net.config, "_attn_implementation", "?"),
        opts.batch_size,
        opts.prefetch,
    )
    n_layers_total = net.config.num_hidden_layers + 1
    selected = self.image._layer_subselection(torch.arange(n_layers_total).view(n_layers_total, 1))
    layer_ids = [int(i) for i in selected.view(-1)]

    stats = {"clips": 0, "seconds": 0.0, "video_seconds": 0.0, "decoded_frames": 0, "gpu_wait_s": 0.0}
    for event in events:
        t_event = time.monotonic()
        video = event.read()
        reader = video.reader if hasattr(video, "reader") else None
        if reader is None:  # not a VideoFileClip subclip: fall back to neuralset's own path
            raise RuntimeError("fast V-JEPA2 path needs a moviepy VideoFileClip")
        freq = self.frequency if self.frequency != "native" else event.frequency
        expect_frames = nsbase.Frequency(freq).to_ind(event.duration)
        times, subtimes = clip_plan(video.duration, freq, self.clip_duration, model.num_frames, expect_frames)
        start = float(event.offset)
        indices = _clip_indices(times, subtimes, start, reader.fps)
        first = min(min(ix) for ix in indices)
        span = max(max(ix) - min(ix) for ix in indices) + 2
        source = FfmpegFrames(reader, first)
        window = FrameWindow(source.read, first, keep=span)

        prefetch = _Prefetcher(partial(_prepare_clip, model.processor, window, indices), len(times), opts.prefetch)
        encoder = _BatchEncoder(
            net, device, dtype, layer_ids, len(times), tqdm(total=len(times), desc="Encoding video")
        )
        try:
            for k, inputs in prefetch:
                encoder.add(k, inputs)
                if len(encoder.pending) >= opts.batch_size:
                    encoder.flush()
            encoder.flush()
        finally:
            prefetch.close()
            source.close()
            encoder.bar.close()
            video.close()
        output = encoder.output
        assert output is not None
        stats["clips"] += len(times)
        stats["seconds"] += time.monotonic() - t_event
        stats["video_seconds"] += float(event.duration)
        stats["decoded_frames"] += window.decoded
        stats["gpu_wait_s"] += prefetch.wait_s
        last_stats.update(stats)
        output = output.transpose(list(range(1, output.ndim)) + [0])
        yield nsbase.TimedArray(
            data=output.astype(np.float32),
            frequency=freq,
            start=nsbase._UNSET_START,
            duration=event.duration,
        )


def install() -> bool:
    """Swap the method exca's cache calls. Other video models keep neuralset's path."""
    global _installed
    if _installed:
        return True
    from neuralset.extractors import video as nsvideo

    imethod = nsvideo.HuggingFaceVideo.model_fields["infra"].default._infra_method
    if imethod is None:
        log.warning("HuggingFaceVideo has no infra method; fast V-JEPA2 path not installed")
        return False
    original = imethod.method

    def _get_data(self, events):
        if "vjepa2" not in self.image.model_name:
            yield from original(self, events)
            return
        yield from fast_get_data(self, events)

    _get_data.__name__ = original.__name__
    _get_data.__qualname__ = original.__qualname__
    _get_data.__annotations__ = getattr(original, "__annotations__", {})
    _get_data._creator_lab_original = original
    imethod.method = _get_data
    _installed = True
    return True


def uninstall() -> None:
    global _installed
    from neuralset.extractors import video as nsvideo

    imethod = nsvideo.HuggingFaceVideo.model_fields["infra"].default._infra_method
    original = getattr(imethod.method, "_creator_lab_original", None)
    if original is not None:
        imethod.method = original
    _installed = False

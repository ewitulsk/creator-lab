"""The fast V-JEPA2 path's frame bookkeeping (no models, no media)."""

import numpy as np
import pytest

from runners.tribe import vjepa_fast as vf


def test_frame_number_matches_moviepy_rounding():
    assert vf.frame_number(30, 2.0) == 60
    assert vf.frame_number(30000 / 1001, 1001 / 30000 * 7) == 7  # float noise doesn't drop a frame
    assert vf.frame_number(29.97, 0.0) == 0


def test_clip_plan_matches_neuralset():
    times, subtimes = vf.clip_plan(duration=10.0, freq=2.0, clip_duration=4.0, num_frames=64, expect_frames=20)
    assert len(times) == 20 and times[0] == pytest.approx(0.5) and times[-1] == pytest.approx(10.0)
    assert len(subtimes) == 64 and subtimes[0] == pytest.approx(4.0 * 63 / 64) and subtimes[-1] == 0.0


def test_clip_indices_clamp_at_event_start_and_add_offset():
    times, subtimes = vf.clip_plan(4.0, 2.0, 4.0, 64, 8)
    idx = vf._clip_indices(times, subtimes, start=100.0, fps=30.0)
    assert idx[0][0] == 3000  # t - t2 < 0 clamps to the event start
    assert idx[-1][-1] == vf.frame_number(30.0, 104.0)
    assert all(a <= b for clip in idx for a, b in zip(clip, clip[1:], strict=False))


def _source(n):
    frames = iter(np.full((2, 2, 3), i, dtype=np.uint8) for i in range(n))
    return lambda: next(frames, None)


def test_frame_window_decodes_each_frame_once():
    win = vf.FrameWindow(_source(50), first_index=10, keep=8)
    clips = [list(range(s, s + 8)) for s in range(10, 40, 2)]  # overlapping, increasing
    for clip in clips:
        for i in clip:
            assert win.get(i)[0, 0, 0] == i - 10
    assert win.decoded == 36  # frames 10..45, each decoded once


def test_frame_window_repeats_last_frame_past_the_end():
    win = vf.FrameWindow(_source(3), first_index=0, keep=4)
    assert win.get(5)[0, 0, 0] == 2
    assert win.decoded == 3


def test_frame_window_refuses_dropped_frames():
    win = vf.FrameWindow(_source(100), first_index=0, keep=2)
    win.get(50)
    with pytest.raises(ValueError):
        win.get(10)


def test_prefetcher_preserves_order_and_surfaces_errors():
    out = [k for k, _ in vf._Prefetcher(lambda k: k * k, 20, depth=3)]
    assert out == list(range(20))

    def boom(k):
        if k == 3:
            raise RuntimeError("decode failed")
        return k

    with pytest.raises(RuntimeError, match="decode failed"):
        list(vf._Prefetcher(boom, 10, depth=2))


def test_run_options_accept_the_speed_switches():
    from runners.tribe.pipeline import RunOptions

    o = RunOptions.from_params({"half_dtype": "bfloat16", "video_batch_size": 2, "fast_video": False})
    assert (o.half_dtype, o.video_batch_size, o.fast_video) == ("bfloat16", 2, False)
    with pytest.raises(ValueError):
        RunOptions.from_params({"half_dtype": "float8"})


def test_word_token_span_matches_neuralset_slicing():
    from runners.tribe.text_fast import word_token_span

    for n_tokens, n_pads, n_prefix in [(10, 0, 7), (10, 3, 4), (10, 2, 8), (5, 0, 5), (6, 1, 9)]:
        x = np.arange(n_tokens)
        ref = x[:-n_pads] if n_pads else x  # neuralset: drop trailing pads
        ref = ref[-max(1, n_tokens - n_pads - n_prefix) :]  # then the word's tokens
        s, e = word_token_span(n_tokens, n_pads, n_prefix)
        assert x[s:e].tolist() == ref.tolist()

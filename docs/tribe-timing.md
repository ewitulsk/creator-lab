# TRIBE v2 timesteps, the roomba row count and the 120-row cycle

## Short version

The old `run_tribe.py` output for roomba (658.36 s of video) had **1,837 rows**, which
fits neither 1 Hz nor 2 Hz, and a **120-row cycle** explained about 30% of its variance.
Both come from one bug in tribev2's demo event pipeline: the WhisperX transcript is
copied into every 60 s audio chunk with the wrong time shift. The words end up
duplicated every **120 s**, and the timeline stretches to about 1,858 s. The creator-lab
TRIBE runner transcribes before chunking, which gives one row per second of video.

## How TRIBE produces rows

- TRIBE's fMRI targets are at `TR = 1 s` (`data.neuro.frequency = 1.0`). Features are
  sampled at 2 Hz (`data.frequency = 2.0`) and pooled by the model to TR resolution.
- Prediction windows are `duration_trs = 100` TRs long with no overlap, starting at the
  timeline start. Each window gives 100 rows, one per second.
- `TribeModel.predict()` drops rows whose 1 s slice contains no event
  (`remove_empty_segments=True`).
- So for a timeline of length T seconds, TRIBE returns `ceil(T / 100) × 100` rows
  minus the empty ones. **One row is always one second.**

## What went wrong in the demo pipeline

`tribev2/demo_utils.py::get_audio_and_text_events` applies, in this order:

1. `ExtractAudioFromVideo`
2. `ChunkEvents(Audio, 30–60 s)` and `ChunkEvents(Video, 30–60 s)`
3. `ExtractWordsFromAudio`
4. `AddText`, `AddSentenceToWords`, `AddContextToWords`, `RemoveMissing`

After step 2, the 658 s audio is 11 Audio events with `start = 60k` and `offset = 60k`
(k = 0..10), all pointing at the same `.wav`. `ExtractWordsFromAudio` transcribes the
**whole** file once, then for **every** audio event appends a copy of the full
transcript shifted by `audio_event.start + audio_event.offset = 120k` seconds
(`tribev2/eventstransforms.py`, the `transcript["start"] += ...` line).

That gives 11 copies of all the words, starting at 0, 120, 240 … 1,200 s:

- The last copy ends at 1,200 + 658 = **1,858 s**, so the timeline is about 1,858 s
  long. That's 19 windows × 100 = **1,900 rows**, of which TRIBE kept **1,837** (the rest
  had no events). This matches the old run exactly ("Predicted 1837 / 1900 segments").
- Copies 120 s apart overlap everywhere inside the video, so the word and text features
  at t and t + 120 s are largely the same words. That's the **120-row cycle** (rows
  120 apart ≈ 0.9 similar). It is 120 **seconds**, not 60 s of 2 Hz frames. The video
  extractor's 60 s chunks are not the cause, since video features are not duplicated.
- Inside the real 0–658 s, the text features mix the right words with words from 2, 4,
  6 … minutes earlier, so the old predictions inside the video are also contaminated,
  not just padded.
- Text-only inputs (a title through TTS) are shorter than 60 s, so they are never chunked
  and never hit the bug. That's why they looked like "about 1 row per second".

## The fix (runner default)

`runners/tribe/pipeline.py::build_video_events` runs `ExtractWordsFromAudio` on the
single, unchunked Audio event (start 0, offset 0) first, so the words are placed once
at their true times, and only then chunks Audio and Video for the feature extractors.
The rest is unchanged. A 75 s test clip gave 76 rows (one past the end, dropped), so
1 row per second.

`preds.npy` is then built from the kept rows by their start time (one row per whole
second of input). Every raw row, kept or not, is saved in `raw/preds_all_rows.npy` with
its times in `rows.parquet`.

## Reproducing the original behaviour

For comparison, the old order is still available:

- job param `{"word_alignment": "original"}`, or
- `python -m runners.tribe --benchmark video.mp4 --word-alignment original`

## Checking any output for the cycle

```powershell
uv run --project runners python scripts/tribe_periodicity.py path\to\preds.npy --period 120
```

This prints the mean row-to-row correlation at each lag up to 200 and the share of
variance explained by a fixed 120-row cycle (per-phase means). On the old roomba file,
expect a sharp peak at lag 120 and roughly 30% explained. With the fixed runner, there
should be no peak at 120.

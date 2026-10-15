# TRIBE runner speed (RTX 5070 Ti, 16 GB)

Measured on 2026-10-15 before the first real batch, on a real 1080p30 upload, with
BrainThing's venv (torch 2.7.1+cu128, transformers with SDPA attention).

## V-JEPA2 (the slow stage)

tribev2 runs V-JEPA2 ViT-g/64 frames at 2 Hz with 4 s clips: every 0.5 s of video is one
64-frame clip (8,192 tokens through a 1B-parameter encoder). An 8.5 h batch is ~61,000 clips.

neuralset's own loop encodes one clip per forward pass and reads each frame through
moviepy, which seeks backwards and restarts ffmpeg for every clip, so each second of 1080p
is decoded ~8 times on one CPU thread while the GPU waits. transformers also runs V-JEPA2's
predictor head on every call, which the features never use.

`runners/tribe/vjepa_fast.py` replaces the body of `HuggingFaceVideo._get_data` at
runtime (same exca cache keys, neuralset not edited):

| | s per clip | video s per s | GPU util |
|---|---|---|---|
| Old BrainThing run (other machine settings) | ~4 | 0.13 | |
| neuralset path, fp16, this GPU | 1.74 | 0.29 | low (CPU decode) |
| fast path, fp16, batch 1, predictor running | 0.85 | 0.59 | 90% |
| **fast path, fp16, batch 1, `skip_predictor`** | **0.66** | **0.75** | 88% |
| fast path, bf16, batch 1 | 0.66 | 0.76 | 88% |
| fast path, fp16, batch 2 / 4 / 6 | 0.67 / 0.88* / 0.91* | | 87% |

\* batch 4 and 6 measured before `skip_predictor`; larger batches never helped, because
one clip already saturates the GPU. Batch 1 is the default.

Correctness, all on real frames:

* Frames: the sequential decoder returned the same pixels as moviepy for 768 of 768 frame
  requests (it decoded 181 frames where moviepy decodes ~8x that).
* Features, batch 1 vs neuralset's loop (same dtype): max relative error 1e-8 (identical).
  Batch 4: correlation 0.999997 (GPU batching noise).
* Precision vs float32: fp16 max relative error 4.7e-5 (min layer correlation 0.999999);
  bf16 8.2e-4 (0.999983). No inf/NaN in fp16. Same speed, so **fp16** is the default
  (`half_dtype` job param switches to bf16).

Memory: 3.1 GB allocated at batch 1, ~4 GB used on the card in total, far below the 16 GB,
so no shared-memory (sysmem) fallback. One model is on the GPU at a time; extractor models
are freed and the CUDA cache emptied between stages.

## Llama-3.2-3B word features

Each word is embedded with its preceding context (~1,300 tokens on long videos), so every
word is a full forward pass. neuralset copies all 29 layers of every context token to the
CPU per batch (~1.5 GB per batch of 4, float32) before picking out the word's tokens; the
GPU idled and the stage ran at ~1.7 words/s on the first real batch (~11 h for 68,700
words). `runners/tribe/text_fast.py` slices each word's tokens on the GPU and runs
neuralset's own `_aggregate_tokens` there (same exca cache keys).

Measured on 96 mid-video words from a 1h49m transcript:

| | s per word | vs neuralset | peak VRAM | features vs neuralset |
|---|---|---|---|---|
| neuralset, batch 4 | 0.229 | 1x | | |
| fast, batch 4 | 0.161 | 1.43x | 9.2 GB | identical (max rel. error 0.0) |
| **fast, batch 8** | **0.131** | **1.76x** | 9.4 GB | corr 0.9999995, max rel. 0.2% |
| fast, batch 16 | 0.128 | 1.79x | 12.9 GB | same as batch 8 |
| fast, batch 32 | 0.955 | 0.24x | 19.8 GB | spills into shared memory |

Batch 8 is the default: nearly batch 16's speed with ~6 GB of headroom. Batch 32 shows
what the shared-memory fallback costs. The remaining cost is the per-word recomputation
of the context itself, which tribev2's sliding contexts require (sharing one pass across
words would change the features, so it isn't done).

## Job params

| param | default | |
|---|---|---|
| `fp16` | true | Llama-3.2-3B and V-JEPA2 in half precision |
| `half_dtype` | float16 | or bfloat16 |
| `fast_video` | true | the V-JEPA2 fast path (false = neuralset's own loop) |
| `video_batch_size` | 1 | clips per forward pass |
| `video_prefetch` | 8 | clips prepared ahead on the CPU thread |
| `fast_text` | true | the Llama fast path (false = neuralset's own loop) |
| `text_batch_size` | 8 | words per Llama forward pass |

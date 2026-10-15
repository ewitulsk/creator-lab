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

## Job params

| param | default | |
|---|---|---|
| `fp16` | true | Llama-3.2-3B and V-JEPA2 in half precision |
| `half_dtype` | float16 | or bfloat16 |
| `fast_video` | true | the V-JEPA2 fast path (false = neuralset's own loop) |
| `video_batch_size` | 1 | clips per forward pass |
| `video_prefetch` | 8 | clips prepared ahead on the CPU thread |

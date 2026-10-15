# Runners

Runners are separate processes that pull jobs from the platform over HTTP
(`Authorization: Bearer RUNNER_TOKEN`). They read `RUNNER_TOKEN` and the server URL
from the environment, falling back to `backend/.env` on the same machine.

| runner | runs in | job types |
|---|---|---|
| `runners.cpu` | `uv` project in this folder | `media` (download, 1080p cap), `captions` |
| `runners.tribe` | BrainThing's venv (`BRAINTHING_DIR`) | `tribe_video`, `tribe_title` |

Start them from the **repo root** (the platform must be running):

```powershell
# CPU runner: downloads and captions
uv run --project runners python -m runners.cpu

# TRIBE runner (GPU), with BrainThing's Python
D:\VideoAnalysis\BrainThing\.venv\Scripts\python.exe -m runners.tribe
```

Environment variables (all optional on the same machine):

| variable | default |
|---|---|
| `CREATOR_LAB_URL` | `http://127.0.0.1:5050` |
| `RUNNER_TOKEN` | from `backend/.env` |
| `RUNNER_NAME` | `cpu@<host>` / `tribe@<host>` |
| `RUNNER_POLL_SECONDS` | `5` |

## Progress

While a job runs the runner reports its exact state every couple of seconds: stage,
stage i of n, video i of n, the video id, the stage percent, the overall percent and a
message (for example "V-JEPA2 on video 5 of 10, 37%"). Log lines ride along. A heartbeat
every 30 s keeps the runner marked online. A job whose runner is silent for 10 minutes is
requeued, and the user can cancel a job, which stops the runner at its next report.

## TRIBE runner

Stages run across the whole batch, one model on the GPU at a time:

1. Audio + WhisperX large-v3 (fp16)
2. Llama-3.2-3B (fp16)
3. w2v-BERT 2.0
4. V-JEPA2 `facebook/vjepa2-vitg-fpc64-256` (fp16)
5. TRIBE predict

Features are cached per event under `DATA_ROOT/runner_cache/tribe/features`, so an
interrupted job resumes. Outputs per video go to
`DATA_ROOT/analyzers/tribe_video/1/<video>/`:

- `preds.npy`: float16, one row per second of video x 20,484 fsaverage5 vertices
- `timing.json`: the start second of each row and how rows were aligned
- `regions.parquet`: network and HCP-MMP1 area means over time
- `embeddings.npz`: pooled pre-brain features (text, audio, video)

Benchmark one file without the platform:

```powershell
D:\VideoAnalysis\BrainThing\.venv\Scripts\python.exe -m runners.tribe --benchmark D:\VideoAnalysis\BrainThing\samples\roomba\roomba_h264.mp4 --out results\benchmarks\roomba
```

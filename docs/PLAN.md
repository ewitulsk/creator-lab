# creator-lab plan

A general creator-analytics platform. YouTube long-form and Shorts first, other
platforms later. Runs on Evan's desktop now; the cloud comes later.

## Principles

- **Collect broadly, study narrowly.** Every tracked channel and video lives in one store.
- **Analyzers.** Every kind of derived data is a versioned analyzer with a per-video
  status: `done`, `queued`, `running`, `failed` or `missing`.
- **The platform issues jobs and never runs heavy models.** Runners pull jobs.
- **Studies are reproducible.** Methodology is locked and hashed, and results are computed in-app.

## Architecture

- One Flask process with SQLite (SQLAlchemy plus Alembic) serves the built React SPA
  and the REST API. APScheduler runs in-process.
- Runners are separate processes that pull jobs over HTTP. In v1 they run on the same
  machine, so the job API hands out **local file paths** instead of uploading or
  downloading files. There is a seam for remote runners later (signed URLs).
- One job runs on one runner, and runners only claim job types they advertise.

## Repo layout

| path | contents |
|---|---|
| `backend/` | app factory, models, migrations, ingest, scheduler, OpenAPI spec |
| `frontend/` | React + Vite + TS SPA |
| `runners/common/` | register, heartbeat, claim, progress, results |
| `runners/tribe/` | TRIBE v2 runner (uses `BRAINTHING_DIR`) |
| `runners/cpu/` | downloads, captions, cheap features |
| `studies/` | study modules |
| `results/` | small committed study outputs (JSON, CSV, figures) |
| `docs/` | this plan and other docs |

The listing logic in `Dataset/build_dataset.py` is ported into ingest, and its pair
selection becomes the first study.

## Data model

- `platforms`
- `channels`: platform id, handle, title, country, uploads playlist, followed,
  auto_track_new, tags, notes, added_at
- `channel_snapshots`: subscribers, views, video count, captured_at
- `videos`: channel, platform id, title, description, published_at, duration,
  format (long / short / live / premiere), aspect, tags, category, language, discovered_at,
  selection (discovered / tracked)
- `video_snapshots`: views, likes, comments, captured_at, source
- `assets`: kind (video / audio / thumbnail / captions / title-text), path, bytes,
  sha256, width, height, codec
- `analyzers`: name, version, inputs, runner type, output format
- `analysis_results`: video, analyzer@version, status, output path, summary JSON, job, computed_at
- `jobs`: analyzer, status (queued / claimed / running / done / failed / cancelled),
  params, created_by, claimed_by, progress JSON, per-stage timings, timestamps, log path
- `job_items`: job, video, stage, status, error
- `runners`: name, type, gpu, capabilities, last heartbeat
- `studies`: slug, title, summary, status (draft / locked / collecting / analyzed),
  methodology markdown, criteria JSON, module, locked_at, locked hash
- `study_videos`: role, group, reason
- `study_results`: code version, results JSON, figures
- `users`: username, password hash

Built-in analyzers:

- **Outlier score**: views divided by the median of the channel's 10 nearest uploads,
  compared at equal video age. Shorts and long-form have **separate baselines**.
- **Views at age N days**, interpolated from snapshots.

## Data collection

- YouTube Data API v3 (`YOUTUBE_API_KEY`) for metadata and stats. A channel is added by
  URL, handle or id. Backfill walks the uploads playlist with `playlistItems` plus
  batched `videos.list`. Never use `search` (100 units a call).
- yt-dlp for media and captions.
- Shorts are 180 seconds or shorter and vertical. Live streams and premieres are flagged.
- Snapshots: daily for videos under 30 days old, weekly after that; channels daily.
  The intervals are settings.
- **Media: never store 4K.** Downloads are capped at `MAX_VIDEO_HEIGHT` (default 1080;
  720 is fine). Prefer H.264 + AAC mp4, one file used by both the browser and TRIBE, with
  no proxy. AV1 or VP9 is re-encoded to H.264 with ffmpeg at the capped height. Downloads
  are CPU-runner jobs, issued only for videos Evan pulls (or new uploads of a channel
  with auto-track on).
- Captions: prefer uploaded over auto-generated, and save both the VTT and parsed text.
  Thumbnails: the largest available. Titles: `title.txt`.
- The YouTube Analytics API only works for channels Evan owns, so it is unused for now.
- `DATA_ROOT` defaults to `D:\VideoAnalysis\data`. D: has about 76 GB free, so disk use
  is shown per channel.

```
data/
  db/creator_lab.sqlite
  media/{channel}/{video}/      video.mp4, audio.wav, thumb.jpg, captions.en.vtt, title.txt
  analyzers/{analyzer}/{version}/{video}/
  runner_cache/tribe/
  exports/
```

## Manual video selection (Discover)

Evan picks what gets pulled. Nothing is downloaded just because a channel was opened.

- **Discover page**, the main way data gets added:
  - **Find a channel** by name with `search.list type=channel` (100 units, only on an
    explicit search), or by URL, handle or id. Results show avatar, subscribers and
    video count.
  - **Open a channel** to list all its uploads from the uploads playlist (cheap:
    `playlistItems` plus batched `videos.list`). Each upload shows thumbnail, title,
    published date, duration, format, views and outlier score. This metadata is stored
    in `videos` with selection `discovered`, so browsing never downloads media.
  - **Filters**:
    - time frame: last 7, 30 or 90 days, last year, all time, or a custom date range
    - most popular: top N by outlier score (the default, since raw views favour older
      videos) or by views
    - least popular: bottom N, same options
    - format: long-form, Shorts or both; live streams and premieres excluded by
      default; minimum duration
    - videos needed for study X: runs that study's criteria against this channel's
      videos and shows the ones it would include, with their role (e.g. pair 3 hit).
      Videos already pulled are badged. Study modules therefore expose a criteria
      function that runs against one channel's candidates.
  - **Selection**: checkboxes plus "select all filtered". "Pull selected" marks the
    videos tracked, takes a stats snapshot, and queues download, captions and thumbnail
    jobs (1080p cap). An optional toggle also adds them to a study. A cost preview
    shows the number of videos, estimated GB at the download cap and quota units.
- **Model**:
  - Videos have a selection state: `discovered` (metadata only) or `tracked`
    (snapshots plus media).
  - Scheduled snapshots run for tracked videos only. Discovered videos are refreshed
    when their channel is reopened.
  - A channel can be followed (daily channel stats, new-upload checks). Its
    "auto-track new uploads" toggle defaults to off.
  - Listing a channel's uploads never downloads anything.
- The seed `channels.txt` import is dropped. Channels are added through Discover.

## Runner API (Bearer `RUNNER_TOKEN`)

| call | purpose |
|---|---|
| `POST /api/runners/register` | register or re-register a runner |
| `POST /api/runners/{id}/heartbeat` | liveness; a job whose runner is silent for 10 minutes is requeued |
| `POST /api/jobs/claim` | atomic claim of the next job of an advertised type |
| `GET /api/jobs/{id}` | params, per-video local input paths and output dir |
| `POST /api/jobs/{id}/progress` | **exact live state** every few seconds |
| `POST /api/jobs/{id}/items/{video}/result` | per-video result |
| `POST /api/jobs/{id}/complete` | job finished |
| `POST /api/jobs/{id}/fail` | job failed, with a log tail |

Progress fields: stage name, stage i/n, video i/n, video id, stage percent, overall
percent and a message, shown in the UI as e.g. "Whisper on video 5 of 10, 1%".

The platform shows every job it issued: which runner holds it, the live stage, video
and percent, per-stage timings, logs, and retry and cancel buttons. Updates arrive
over SSE, with polling as a fallback.

## TRIBE runner (BrainThing venv)

Each stage runs across the **whole batch**, with one model on the GPU at a time:

1. Audio extraction plus WhisperX large-v3 fp16
2. Llama-3.2-3B fp16, then unload and empty the CUDA cache
3. w2v-bert-2.0, then unload
4. V-JEPA2 (`facebook/vjepa2-vitg-fpc64-256`) fp16, then unload
5. TRIBE predict from the feature cache

The runner resumes from the feature cache. neuralset keeps `self._model` forever (about
15 GB peak VRAM), so the runner frees it at runtime. tqdm is hooked for progress.

Outputs per video:

- `preds.npy` as float16 (timesteps x 20,484 fsaverage5 vertices)
- `timing.json` with the start second of each timestep. Resolve why video gave about
  2.8 timesteps per second versus about 1 for text.
- `regions.parquet`: network and region means over time
- pooled pre-brain embeddings

Benchmark on `BrainThing\samples\roomba\roomba_h264.mp4`. The old run took about 2h10m
on the 5070 Ti (V-JEPA2 89 min, Llama 33 min). There is also a TRIBE-on-title job type
with text input.

## CPU runner

- download (capped at 1080p)
- captions
- outlier score
- visual features from arXiv 1510.06223: cuts/pacing, edge density, faces, color,
  resolution, length
- audio features: speech/music ratio, loudness
- ResMem thumbnail memorability

## Frontend

React, Vite and TS, served by Flask. TanStack Router, Query and Table; Tailwind with
shadcn/ui; Observable Plot; react-three-fiber. TS types are generated from the OpenAPI spec.

Pages:

- **Dashboard**: counts, disk, jobs, runners, errors
- **Channels**: add by URL
- **Channel detail**: upload grid; views-versus-date scatter with rolling median and
  outliers; subscriber history; coverage
- **Video detail**: player with brain view; stats over time; synced transcript; one tab per analyzer
- **Data coverage**: videos x analyzers grid; select cells to issue jobs
- **Compare creators**
- **Jobs**: live
- **Studies** and **Study detail**: summary, criteria, methodology with a lock badge,
  members with missing data, a fill-gaps button that issues jobs, results
- **Settings**: key status only, change password, data folder, schedules

Brain player:

- Inflated fsaverage5 cortex for both hemispheres in three.js, with per-vertex colors
  at the current time
- The backend serves uint8-quantized preds as a chunked binary stream
- Sync with `requestVideoFrameCallback` and `timing.json`
- Network curves (visual, auditory, language, default mode, attention) under the
  player: click to seek, peaks marked
- Colormap, threshold and lateral/medial controls

## Studies

A study is a module in `studies/` plus a DB row. The module defines:

- `criteria(store)`: members with roles, plus a plain-language rendering of the rules
- `requires`: `analyzer@version` list; drives completeness and gap filling
- `measures`: pre-registered
- `analyze`: test plus figures, written to `study_results` and `results/{study}/`

Lock flow: draft → locked (methodology, criteria and measures frozen; module source
hash and time stored) → collecting → analyzed. If the module changes after locking,
the app warns and requires a new version. Results record the code version. Members
export as CSV or Parquet.

### First study: "Does TribeV2 predict long-form virality?"

- **Criteria**: Evan's channels, long-form only, no live streams or premieres, at
  least 3 minutes. Pairs within a channel: a hit at 2x or more outlier score and a flop
  at 0.5x or less, uploaded within 30 days of each other. 30 to 50 pairs, 2 per channel
  by default.
- **Requires**: media, TRIBE video, TRIBE title, visual and audio features, ResMem,
  pooled embeddings.
- **Measures**: drafted (network means, first 30 seconds, and so on) but left
  **unlocked** for Evan to approve.
- **Analysis**: paired Wilcoxon or permutation test. Brain measures must beat a
  cheap-features model and a pre-brain-embeddings model. Side analyses: title TRIBE
  and thumbnail ResMem on the same pairs.

## Auth and config

- Admin credentials are never hardcoded (the repo is public). `ADMIN_PASSWORD` is read
  from `backend/.env` on first start, stored as a hash, and changed in Settings.
- Flask-Login with a rate-limited login. Runners use `RUNNER_TOKEN`.
- `.env.example`: `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `SECRET_KEY` (generated by the
  setup command), `YOUTUBE_API_KEY`, `DATA_ROOT`, `RUNNER_TOKEN`, `BRAINTHING_DIR`,
  `MAX_VIDEO_HEIGHT=1080`.

## Milestones (one PR each, stacked)

1. **Foundation**: skeleton, gitignore, app factory, models and migration, login,
   settings, SPA, CI. Done when Evan can log in to an empty dashboard.
2. **Channels and videos**: ingest, Discover (search, browse, filter, pull), snapshots,
   thumbnails, pages, outlier score. Done when his channels are browsable with stats history.
3. **Jobs and runners**: API, client, CPU runner downloads, staged fp16 TRIBE runner
   benchmarked, live Jobs page, coverage grid. Done when he can issue a TRIBE job and watch it.
4. **Brain player**. Done when roomba plays with its brain response.
5. **Studies** framework plus the first study and its analyzers.
6. **Compare creators**, Shorts views, exports, SQLite backups, Dockerfile and compose.

## Risks and open items

- **Disk space**: about 76 GB free on D:. Media is capped at 1080p (720p is enough),
  4K is never stored, and disk use is shown per channel.
- **TRIBE speed**: measure it on the roomba benchmark.
- **Timesteps versus seconds**: resolve the ~2.8/s (video) versus ~1/s (text) rate.
- **yt-dlp breakage and throttling**: pin the version, retry jobs, pace downloads.
- **Never commit copyrighted media.**
- **BrainThing venv path**: its `.venv` still points at `D:\BrainThing`.
- **Data API quota**: 10,000 units a day.
- **GPU runner visibility**: the platform does not manage the GPU runner process, but
  it still shows every job it pushed to a runner, and the runner reports its exact
  stage and progress back.

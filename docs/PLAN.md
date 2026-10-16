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

## YouTube owner sync ("Sync data from YT")

Evan's own channel (https://www.youtube.com/@evanwitulski) gets the data only the owner can see.

- **Sign in with YouTube**: the Google OAuth 2.0 web flow with the scopes
  `yt-analytics.readonly` and `youtube.readonly`, redirecting to a backend callback.
  The refresh token is stored encrypted in the DB (Fernet, key derived from
  `SECRET_KEY`) and never appears in logs or the repo. Settings:
  `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`.
  Setup steps: [docs/youtube-oauth.md](youtube-oauth.md).
- **Owned channel**: found with `channels.list mine=true` and marked owned (with a
  credential row). Every upload is pulled, including private and unlisted ones:
  - Data API: full snippet, contentDetails, status, statistics, topicDetails
  - Analytics, lifetime per video: views, estimatedMinutesWatched, averageViewDuration,
    averageViewPercentage, likes, dislikes, comments, shares, subscribersGained/Lost,
    card metrics; impressions and CTR only if the API accepts them (it degrades
    silently and does not show them as available otherwise)
  - daily time series per video (dimension `day`)
  - audience retention: `elapsedVideoTimeRatio` with `audienceWatchRatio` and
    `relativeRetentionPerformance`, split by `audienceType` where available, stored
    with the video duration so ratios convert to seconds (`retention_curves` table,
    versioned per sync)
  - breakdowns: traffic source, device, country, age group and gender, subscribed status
  - every report is kept as versioned raw JSON plus parsed tables, and every call is logged
- The owned channel's videos are downloaded through the normal download jobs (1080p
  cap). Private videos may fail without cookies: they are marked, not crashed on.
- There is no scheduled refresh. Signing in again offers **Re-pull everything** (a new
  retention snapshot version; old ones are kept) or **Pull new only**, and shows the
  last sync time and counts.
- UI: an owned badge, and a **Real engagement** tab on the video page with the retention
  curve. When a TRIBE result exists, the TRIBE engagement curve is overlaid on the same
  time axis.

## "Most replayed" heatmap (public, yt-dlp)

- Downloads save the full yt-dlp info dict as `info.json` next to the video (sanitized:
  no cookies or request headers) and register it as an asset.
- Its `heatmap` (about 100 segments with start, end and value, only on videos with enough
  views), `chapters` and a stats snapshot (view, like and comment counts at capture) go
  into `video_info_captures`, `video_heatmaps` and `video_chapters`, versioned per capture.
- A lightweight **`heatmap@1`** job does the same **without downloading** (metadata
  only), for any public video: discovered ones and other creators' too.
- UI: the Most Replayed curve sits under the player on the video's time axis, and is its
  own column in the coverage grid.
- **Kept strictly separate from the owner's real Analytics data.** The public heatmap
  (`video_heatmaps`, source `yt_dlp_heatmap`) and real retention (`retention_curves`,
  source `youtube_analytics`) are separate tables and are never merged, backfilled or
  overwritten from each other. For owned videos that have both, the video page overlays
  them on one time axis, labelled as public heatmap versus real retention.
- Studies: the heatmap is a target signal in the retention study (the public proxy for
  engagement over time, so the TRIBE-versus-engagement test can run on other creators'
  channels). A "heatmap versus real retention" analysis (per-video correlation,
  aggregated across owned videos) measures how good a proxy it is. The methodology stays
  unlocked.

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

Outputs per video: **save everything, especially timestamps** (nothing TRIBE returns is dropped):

- `preds.npy` as float16, one row per second of the input (timesteps x 20,484 fsaverage5)
- `raw/preds_rows.npy` and `rows.parquet`: every prediction row exactly as TRIBE returned
  it, with segment start, duration, window/batch index, kept or dropped (dropped rows
  are NaN in the full-length array) and which modalities had events
- `timing.json`: the start second of each row in `preds.npy` and how rows were aligned
- `events.parquet` (the events TRIBE built), `words.parquet` and `transcript.txt` (WhisperX)
- `features/`: per-modality pre-brain features over time (Llama, w2v-BERT, V-JEPA2
  layer groups, fp16) with their timestamps and frequency, plus pooled versions
- `regions.parquet`: network and region means over time
- `meta.json`: TR and frequencies, window settings, the full data and model config,
  model and checkpoint ids and hashes, library versions, GPU, dtype and patch settings,
  per-stage wall times
- `manifest.json`: every file written

Timesteps versus seconds is resolved: see [docs/tribe-timing.md](tribe-timing.md).

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

### Engagement analyzer and the retention study

- **`tribe_engagement@1`** (depends on TRIBE video preds) maps vertices to Yeo 7
  networks (fsaverage5, left then right) and to HCP-MMP1 areas. For each region it
  z-scores every vertex over time, averages them, and regresses out the whole-cortex
  average signal. Curves per timestep:
  - stimulus drive: whole-cortex magnitude, an ISC analog
  - draft engagement index: mean(dorsal attention, salience/ventral attention) minus
    default mode
  - anterior insula, and NAcc if the tribev2 subcortical model is available
- **Study "Does TRIBE predict real audience retention?"** on owned-channel videos:
  - the target is drop-off per 1% bin (change in `audienceWatchRatio`) plus
    `relativeRetentionPerformance`, excluding the first and last 5%
  - the control is the generic retention decay shape (a smooth decay fit), so TRIBE has
    to explain the residual dips and bumps, not the overall decline
  - tests: per-video Spearman correlation of the TRIBE curve (and its derivative) with
    the residuals; across videos, a sign or permutation test with circular-shift nulls
    within each video; and a leave-one-video-out ridge over network and parcel curves
  - controls: the same tests on pre-brain features and on cheap features (shot cuts,
    loudness, speech rate). Brain curves must beat them.
  - the anterior insula is expected to be negative (Tong et al. 2020)
  - curve definitions and lags are pre-registered but left **unlocked** for Evan to approve
  - the Discover "videos needed for study X" filter works for this study too
- Notes from the build: NAcc is not available (no subcortical TRIBE v2 checkpoint has been
  released). Yeo-7 comes from the FreeSurfer fsaverage5 annotation when it can be fetched,
  otherwise from an HCP-MMP1 approximation (recorded per result). ResMem needs the
  runner's optional esmem extra. See [docs/studies.md](studies.md).

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
   **3b. YouTube owner sync**: OAuth, owned channel, Analytics reports, retention
   curves, the Real engagement tab.
4. **Brain player**. Done when roomba plays with its brain response.
5. **Studies** framework plus the first study and its analyzers, `tribe_engagement`,
   and the retention study.
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

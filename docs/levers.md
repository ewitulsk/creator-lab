# Viral levers

Six levers, as data points per video. Each data point is a **lever x method** pair:

| System 1 | System 2 |
|---|---|
| attention | valuable |
| emotion | memorable |
| comprehension | shareable |

Methods are open-ended text (`gemini_agentic` today; `cut_rate`, `voice_arousal`,
`tribe_*` and others later), stored on every row, so adding a method needs no migration.
The tables (`lever_scores`, `lever_subscores`, `lever_moments`, `lever_pairwise`,
`lever_probes`, `lever_calls`) are separate from retention and heatmap data.

## Method 1: Gemini as a judge (`levers_gemini`)

* **Models** (Settings > Lever scoring): Gemini 4 Argon (`gemini-4-argon`, default) or
  Gemini 3.8 Flash (`gemini-3.8-flash`). Both ids are in google-genai 2.31's model list and
  Google's docs, and both support agentic video (`"processing": "agentic"` on the video
  part of `client.interactions.create`). "Test connection" checks the key and the id with
  one metadata call. Ids live in `backend/src/creator_lab/levers/config.py`.
* **Key**: `GEMINI_API_KEY` in `backend/.env`. Never committed, logged or returned (the API
  only says whether it is set; error text is scrubbed). Without it the analyzer is
  unavailable and jobs refuse to start.
* **Blinded input (default `upload`)**: an ffmpeg copy with all metadata stripped
  (`-map_metadata -1`, no chapters, no encoder tag), a random file name, 360p (setting), 2
  frames/s, low bitrate and mono audio; checked against the 2 GB Files API limit and deleted
  from Gemini after scoring. The prompt is built from the rubric only, so it never contains
  the title, channel, description, views or upload date.
* **`youtube_url` mode** is **not blinded** and is only for the URL-vs-upload A/B
  (`POST /api/levers/ab`, up to 5 videos, needs Evan's go-ahead because it costs real
  calls). If Gemini rejects a URL, that video falls back to the upload and is flagged.
  The mode actually used is stored on every row.
* **Contamination probe**: one extra call per video asks whether the model can identify the
  video or creator. If it names the creator or the title, every score for that video is
  flagged `possibly_contaminated`.
* **Rubric**: `rubrics/levers_v1.yaml`, 1 to 5 anchors per lever and sub-items. Its
  SHA-256 is stored with every result, so an edit is a new rubric; bump `version` when a
  score's meaning changes.
* **Repeats**: 3 runs per video (setting), temperature 0.7. Every run is stored; the
  aggregate is the median per lever and sub-item, spread = max - min, flagged when >= 2.
* **Structured output** via the Interactions API `response_format` JSON schema; responses
  are validated and retried once on bad JSON. Raw responses are saved under
  `DATA_ROOT/analyzers/levers_gemini/1/<video>/raw/`.
* **Resume**: a video already scored at the same analyzer version + rubric hash + model +
  input mode is skipped; a new model or rubric scores it again.
* **Cost**: every call's input, output and thinking tokens and an estimated cost are in
  `lever_calls`. Batches show an estimate before they are queued (Coverage, video page,
  studies, Compare) and the job page shows estimated vs actual.

Jobs run in an in-app worker (`api@<host>`), so there is no extra runner to start. The
Jobs page shows "Gemini levers: video 3 of 12, run 2 of 3".

## Pairwise (`levers_gemini_pairwise`)

Both videos uploaded blinded; per lever Gemini says which is stronger (A, B or tie). Every
pair is judged in both orders to cancel position bias, and both are stored. Start it from
a study (every matched pair: a group with exactly two members) or from the Compare page.

## Reading the scores

* Video page: the Levers panel (median, spread band, flags, reasons, clickable evidence),
  and emotional moments / evidence spans as markers on the "Most replayed" and retention
  charts.
* Coverage: one column per lever x method.
* Exports: `/api/exports/levers.csv` (or `.parquet`): aggregates and runs, sub-scores
  flattened.
* Study modules: `store.lever_scores(video_ids, kind="aggregate" | "run" | "all")`.

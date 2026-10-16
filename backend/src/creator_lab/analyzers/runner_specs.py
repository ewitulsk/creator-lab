"""Analyzers computed by runners. The platform only issues their jobs."""

from __future__ import annotations

from . import Spec, register

MEDIA = register(
    Spec(
        name="media",
        version="1",
        title="Video download",
        runner_type="cpu",
        description="H.264/AAC mp4 at or below MAX_VIDEO_HEIGHT (never 4K), downloaded with yt-dlp.",
        inputs=[],
        output_format="mp4",
    )
)
CAPTIONS = register(
    Spec(
        name="captions",
        version="1",
        title="Captions",
        runner_type="cpu",
        description="Uploaded captions (auto-generated as a fallback) as VTT, plain text and timed cues.",
        inputs=[],
        output_format="vtt+json",
    )
)
TRIBE_VIDEO = register(
    Spec(
        name="tribe_video",
        version="1",
        title="TRIBE v2 (video)",
        runner_type="gpu",
        description="Predicted fMRI response (fsaverage5, 1 s steps) to the full video: WhisperX, Llama-3.2-3B, "
        "w2v-BERT 2.0 and V-JEPA2 features, then TRIBE. Also saves pooled pre-brain embeddings.",
        inputs=["media"],
        output_format="preds.npy+timing.json+regions.parquet+embeddings.npz",
        applies_to=["long", "short"],
    )
)
TRIBE_TITLE = register(
    Spec(
        name="tribe_title",
        version="1",
        title="TRIBE v2 (title)",
        runner_type="gpu",
        description="Predicted fMRI response to the title read aloud (text input via TTS).",
        inputs=["title_text"],
        output_format="preds.npy+timing.json+regions.parquet",
    )
)

HEATMAP = register(
    Spec(
        name="heatmap",
        version="1",
        title="Most replayed",
        runner_type="cpu",
        description="YouTube's 'Most replayed' heatmap, chapters and a stats snapshot from yt-dlp metadata. "
        "No download; works on any public video. Downloads capture it too.",
        inputs=[],
        output_format="info.json + tables",
    )
)

VISUAL_FEATURES = register(
    Spec(
        name="visual_features",
        version="1",
        title="Visual features",
        runner_type="cpu",
        description="Per-second shot cuts, motion, edge density, brightness, saturation, colorfulness and faces; "
        "summary cuts per minute, shot length, resolution, length.",
        inputs=["media"],
        output_format="visual_features.json",
    )
)
AUDIO_FEATURES = register(
    Spec(
        name="audio_features",
        version="1",
        title="Audio features",
        runner_type="cpu",
        description="Per-second loudness (LUFS, dBFS), spectral flatness, music score (heuristic) and speech "
        "(from captions); summary integrated loudness, speech ratio and speech rate.",
        inputs=["media"],
        output_format="audio_features.json",
    )
)
RESMEM = register(
    Spec(
        name="resmem",
        version="1",
        title="Thumbnail memorability",
        runner_type="cpu",
        description="ResMem memorability score of the thumbnail (needs the runner's resmem extra).",
        inputs=["thumbnail"],
        output_format="resmem.json",
    )
)
TRIBE_ENGAGEMENT = register(
    Spec(
        name="tribe_engagement",
        version="1",
        title="TRIBE engagement",
        runner_type="cpu",
        description="From TRIBE preds: per-region curves (vertex z-scored over time, averaged, global signal "
        "regressed out) on Yeo-7 networks and HCP-MMP1 areas; stimulus drive, engagement index "
        "(attention minus default mode, draft) and anterior insula.",
        inputs=["tribe_video"],
        output_format="engagement.json",
        applies_to=["long", "short"],
    )
)

LEVERS_GEMINI = register(
    Spec(
        name="levers_gemini",
        version="1",
        title="Levers (Gemini)",
        runner_type="api",
        description="Six viral levers (attention, emotion, comprehension, valuable, memorable, shareable) scored "
        "1-5 by Gemini watching a blinded low-res copy, against rubrics/levers_v1.yaml. Runs 3 times; stores every "
        "run, the median and spread, emotional moments, evidence and a contamination probe.",
        inputs=["media"],
        output_format="lever_scores tables + raw JSON",
    )
)
LEVERS_GEMINI_PAIRWISE = register(
    Spec(
        name="levers_gemini_pairwise",
        version="1",
        title="Levers pairwise (Gemini)",
        runner_type="api",
        description="Which of two videos is stronger on each lever, judged in both orders to cancel position bias.",
        inputs=["media"],
        output_format="lever_pairwise table + raw JSON",
    )
)

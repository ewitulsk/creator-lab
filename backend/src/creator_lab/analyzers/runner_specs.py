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

# Which asset kinds each analyzer's job needs on disk, and which job makes them.
INPUT_PRODUCERS = {"media": "media", "title_text": None}

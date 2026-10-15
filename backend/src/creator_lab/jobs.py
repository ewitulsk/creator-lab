"""Issuing jobs to runners. (The job queue itself arrives with the runners.)"""

from __future__ import annotations

from .models import Job


def queue_media_jobs(video_ids: list[int], created_by: str = "system") -> list[Job]:
    """Queue download and captions jobs for pulled videos. A no-op until runners exist."""
    return []

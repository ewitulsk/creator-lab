"""Settings read from the environment (and backend/.env).

Everything secret lives in backend/.env, which is gitignored. The repo is public,
so nothing here may carry a real default for a secret.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
ENV_FILE = BACKEND_DIR / ".env"

# Values in .env.example that mean "not filled in yet".
PLACEHOLDERS = {"", "change-me", "CHANGE_ME", "paste-your-key-here", "generate-with-setup"}


def is_set(value: str | None) -> bool:
    return value is not None and value.strip() not in PLACEHOLDERS


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw and raw.strip() else default


@dataclass
class Config:
    data_root: Path
    secret_key: str
    admin_username: str
    admin_password: str | None
    youtube_api_key: str | None
    runner_token: str | None
    brainthing_dir: Path | None
    max_video_height: int
    frontend_dist: Path
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None
    testing: bool = False
    start_scheduler: bool = True
    extra: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_root / "db" / "creator_lab.sqlite"

    @property
    def database_uri(self) -> str:
        return "sqlite:///" + self.db_path.as_posix()

    @classmethod
    def from_env(cls, env_file: Path | None = ENV_FILE) -> Config:
        if env_file and env_file.exists():
            load_dotenv(env_file, override=False)
        data_root = Path(os.environ.get("DATA_ROOT") or REPO_ROOT.parent / "data")
        brainthing = os.environ.get("BRAINTHING_DIR")
        height = _int("MAX_VIDEO_HEIGHT", 1080)
        return cls(
            data_root=data_root,
            secret_key=os.environ.get("SECRET_KEY", ""),
            admin_username=os.environ.get("ADMIN_USERNAME") or "admin",
            admin_password=os.environ.get("ADMIN_PASSWORD"),
            youtube_api_key=os.environ.get("YOUTUBE_API_KEY"),
            runner_token=os.environ.get("RUNNER_TOKEN"),
            brainthing_dir=Path(brainthing) if is_set(brainthing) else None,
            # Never store 4K: anything above 1080 is clamped.
            max_video_height=min(max(height, 144), 1080),
            frontend_dist=Path(os.environ.get("FRONTEND_DIST") or REPO_ROOT / "frontend" / "dist"),
            start_scheduler=os.environ.get("DISABLE_SCHEDULER", "") not in ("1", "true", "yes"),
            google_client_id=os.environ.get("GOOGLE_OAUTH_CLIENT_ID"),
            google_client_secret=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"),
            google_redirect_uri=os.environ.get("GOOGLE_OAUTH_REDIRECT_URI"),
        )

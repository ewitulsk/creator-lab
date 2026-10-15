"""Runner settings: environment variables, falling back to backend/.env on the same machine."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ENV = REPO_ROOT / "backend" / ".env"


def read_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser (KEY=value lines, # comments, optional quotes)."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


@dataclass
class RunnerConfig:
    url: str
    token: str
    name: str
    brainthing_dir: Path | None
    poll_seconds: float = 5.0
    heartbeat_seconds: float = 30.0

    @classmethod
    def load(cls, default_name: str) -> RunnerConfig:
        env = {**read_env_file(BACKEND_ENV), **os.environ}
        token = env.get("RUNNER_TOKEN", "")
        if not token or token in ("generate-with-setup", "change-me"):
            raise SystemExit(
                "RUNNER_TOKEN is not set (env or backend/.env). Run `uv run creator-lab setup` in backend/."
            )
        bt = env.get("BRAINTHING_DIR")
        return cls(
            url=env.get("CREATOR_LAB_URL", "http://127.0.0.1:5050").rstrip("/"),
            token=token,
            name=env.get("RUNNER_NAME") or f"{default_name}@{socket.gethostname()}",
            brainthing_dir=Path(bt) if bt else None,
            poll_seconds=float(env.get("RUNNER_POLL_SECONDS", 5)),
        )

# syntax=docker/dockerfile:1
# Two targets from one file:
#   docker build --target app .         Flask API + built frontend + scheduler
#   docker build --target cpu-runner .  CPU runner (downloads, captions, heatmaps, features)
# The TRIBE runner stays on the Windows/GPU host with BrainThing's venv (see README).
# Secrets are never baked in: .dockerignore drops backend/.env, compose passes it at run time.

FROM node:24-slim AS frontend
WORKDIR /app/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY backend/src/creator_lab/openapi.yaml /app/backend/src/creator_lab/openapi.yaml
COPY frontend/ ./
RUN pnpm build

FROM python:3.12-slim AS python-base
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1 DATA_ROOT=/data
WORKDIR /app
RUN useradd --create-home --uid 1000 lab && mkdir -p /data && chown lab:lab /data

FROM python-base AS app
COPY backend/pyproject.toml backend/uv.lock backend/
RUN mkdir -p backend/src/creator_lab && touch backend/src/creator_lab/__init__.py \
    && uv sync --project backend --locked --no-dev --no-install-project
COPY backend/ backend/
COPY studies/ studies/
COPY rubrics/ rubrics/
COPY results/README.md results/README.md
RUN uv sync --project backend --locked --no-dev
COPY --from=frontend /app/frontend/dist frontend/dist
ENV CREATOR_LAB_RESULTS_DIR=/data/results
RUN chown -R lab:lab /app
USER lab
EXPOSE 5050
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5050/api/health', timeout=4)"
CMD ["uv", "run", "--project", "backend", "--no-sync", "creator-lab", "serve", "--host", "0.0.0.0", "--port", "5050"]

FROM python-base AS cpu-runner
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# yt-dlp uses an external JavaScript runtime for YouTube's player challenges.
COPY --from=denoland/deno:bin-2.5.6 /deno /usr/local/bin/deno
COPY runners/pyproject.toml runners/uv.lock runners/
RUN uv sync --project runners --locked --no-dev
COPY runners/ runners/
RUN chown -R lab:lab /app
USER lab
CMD ["uv", "run", "--project", "runners", "--no-sync", "python", "-m", "runners.cpu"]

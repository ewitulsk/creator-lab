# creator-lab

A creator-analytics platform: track YouTube channels and videos, collect stats over
time, run analyzers (including TRIBE v2 brain-response prediction) through pull-based
runners, and run reproducible studies on the results.

See [docs/PLAN.md](docs/PLAN.md) for the full plan.

## Requirements

- [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed by uv)
- Node 24 (pnpm comes from corepack: `corepack pnpm ...`)
- ffmpeg on PATH (for downloads)

## First run (Windows, PowerShell)

```powershell
cd backend
uv run creator-lab setup        # creates backend/.env, generates SECRET_KEY + RUNNER_TOKEN, migrates
notepad .env                    # set ADMIN_PASSWORD (read once, then stored as a hash) and YOUTUBE_API_KEY

cd ..\frontend
corepack pnpm install
corepack pnpm build             # Flask serves frontend/dist

cd ..\backend
uv run creator-lab serve        # http://127.0.0.1:5050
```

`backend/.env` is gitignored. Never commit it: the repo is public.

## Development

```powershell
# terminal 1: API on :5050
cd backend; uv run creator-lab serve

# terminal 2: Vite on :5173, proxying /api to :5050
cd frontend; corepack pnpm dev
```

Checks (the same ones CI runs):

```powershell
cd backend;  uv run ruff check .; uv run ruff format --check .; uv run pytest
cd frontend; corepack pnpm gen:api; corepack pnpm typecheck; corepack pnpm lint; corepack pnpm test; corepack pnpm build
```

The OpenAPI spec lives in `backend/src/creator_lab/openapi.yaml`; a test fails if an
`/api` route is missing from it. After changing it, run `corepack pnpm gen:api` to
regenerate `frontend/src/api/schema.d.ts`.

Database migrations (Alembic via Flask-Migrate):

```powershell
cd backend
uv run flask --app creator_lab:create_app db migrate -m "describe the change"
uv run creator-lab migrate
```

## Data

Everything lives under `DATA_ROOT` (default `D:\VideoAnalysis\data`), never in the repo:

```
data/
  db/creator_lab.sqlite
  media/{channel}/{video}/      video.mp4, thumb.jpg, captions.en.vtt, title.txt
  analyzers/{analyzer}/{version}/{video}/
  runner_cache/tribe/
  exports/
  backups/
```

Downloads are capped at `MAX_VIDEO_HEIGHT` (default 1080). 4K is never stored.

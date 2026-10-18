# Wallora AI — Backend

FastAPI API and generation worker. See [../docs/02-architecture.md](../docs/02-architecture.md).

## Setup (Windows, native PostgreSQL + Redis)

```powershell
py -m pip install --user uv          # once
cd backend
py -m uv sync                        # creates .venv and installs locked dependencies
Copy-Item .env.example .env          # then edit local values (never commit .env)
py -m uv run python scripts/dev_check.py   # checks PostgreSQL/Redis are reachable
py -m uv run uvicorn --factory app.main:create_app --reload
```

`py -m uv` works even when the `uv` launcher isn't on `PATH`.

## Checks

```powershell
py -m uv run ruff format --check .
py -m uv run ruff check .
py -m uv run mypy
py -m uv run pytest
```

## Current state
Phase 0: there's only a `/health/live` endpoint and minimal settings. Database, auth and all domain modules arrive in Phase 1+.

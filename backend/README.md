# Wallora AI — Backend

FastAPI API and generation worker on Cloud Firestore. See [../docs/02-architecture.md](../docs/02-architecture.md) and [../docs/04-database.md](../docs/04-database.md).

## Setup (Windows)

```powershell
py -m pip install --user uv          # once
cd backend
py -m uv sync                        # creates .venv and installs locked dependencies
Copy-Item .env.example .env          # then edit local values (never commit .env)

# In another terminal, from the repo root: the local Firestore emulator
firebase emulators:start --only firestore --project demo-wallora

py -m uv run python scripts/dev_check.py                       # checks the emulator is reachable
py -m uv run uvicorn --factory app.main:create_app --reload    # API on http://localhost:8000
py -m uv run python -m app.worker                              # generation worker
```

`py -m uv` works even when the `uv` launcher isn't on `PATH`. The default `IMAGE_PROVIDER=fake` and `MODERATION_PROVIDER=fake` make no network calls. Switching to `openai` requires an API key and the owner's approval.

## Checks

```powershell
py -m uv run ruff format --check .
py -m uv run ruff check .
py -m uv run mypy
firebase emulators:exec --only firestore --project demo-wallora "py -m uv run pytest"
```

Without the emulator, `py -m uv run pytest` runs only the unit tests and reports the Firestore tests as skipped.

## Layout
| Path | Contents |
|------|----------|
| `app/db/` | Firestore client and transaction helpers, typed documents |
| `app/modules/credits/` | `CreditBook` (lots, reserve/capture/release, ledger), credits API |
| `app/modules/spend/` | Spend caps (`SpendBook`) |
| `app/modules/moderation/` | Prompt moderation pipeline, block records, strikes |
| `app/modules/studio/` | Tiers/aspects/cost catalog, prompts, job service, studio API |
| `app/providers/` | Image providers (fake, OpenAI) and moderation providers (fake, OpenAI) |
| `app/integrations/` | Firebase token verification, object storage (S3/R2, local, memory) |
| `app/worker/` | Generation worker (claim, leases, retries, reaper, upload purge) |

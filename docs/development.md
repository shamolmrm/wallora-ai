# Local Development Guide (Windows)

## 1. Prerequisites

| Tool | Version | Status on owner machine (2026-10-18) |
|------|---------|--------------------------------------|
| Git | 2.54+ | ✅ installed |
| Python | 3.13+ (`py` launcher) | ✅ 3.14.6 |
| uv | 0.13.1 | ✅ installed via `py -m pip install --user uv==0.13.1` |
| Flutter / Dart | 3.44.1 / 3.12.1 | ✅ installed |
| Node.js / npm | 24 LTS+ / npm 11 | ✅ Node 26.3.0 |
| PostgreSQL | 16+ | ❌ to install (native) |
| Redis | 7+ (Memurai or WSL2) | ❌ to install (optional until rate limiting lands) |
| Android Studio + JDK 17 | — | ❌ to install (needed for Android builds) |
| macOS + Xcode | — | ❌ not available (needed for iOS builds) |

> The Windows `python` command is the Microsoft Store stub on this machine. Use `py` or `py -m uv`.

## 2. PostgreSQL (native)
1. Install PostgreSQL 16 or 17 with the official Windows installer from postgresql.org. Keep the default port 5432.
2. In `psql` (as the `postgres` superuser), create a role and databases. Choose your own password and **don't share it**:
   ```sql
   CREATE ROLE wallora LOGIN PASSWORD '<choose-a-local-password>';
   CREATE DATABASE wallora OWNER wallora;
   CREATE DATABASE wallora_test OWNER wallora;
   \c wallora
   CREATE EXTENSION IF NOT EXISTS pg_trgm;
   CREATE EXTENSION IF NOT EXISTS citext;
   \c wallora_test
   CREATE EXTENSION IF NOT EXISTS pg_trgm;
   CREATE EXTENSION IF NOT EXISTS citext;
   ```
3. Put the connection URL in `backend/.env` (copied from `.env.example`).

## 3. Redis (native)
Redis doesn't publish native Windows builds. Use either:
- **Memurai Developer edition** (Redis-compatible Windows service, port 6379), or
- Redis inside **WSL2** (`sudo apt install redis-server`), reachable at `localhost:6379`.

Until Phase 1 rate limiting lands, Redis is optional. Setting `REDIS_URL=` (empty) selects the in-memory fallback.

## 4. Verify services
```powershell
cd backend
py -m uv run python scripts/dev_check.py
```

## 5. Run each package
See [backend/README.md](../backend/README.md), [mobile/README.md](../mobile/README.md) and the admin section below.

```powershell
cd admin
Copy-Item .env.example .env.local
npm ci
npm run dev        # http://localhost:3000
```

## 6. Quality checks
The per-package commands are listed in [AGENTS.md](../AGENTS.md). CI runs the same commands (`.github/workflows/`).

Optional pre-commit hooks:
```powershell
py -m pip install --user pre-commit
pre-commit install
```

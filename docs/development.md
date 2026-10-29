# Local Development Guide (Windows)

## 1. Prerequisites

| Tool | Version | Status on owner machine (2026-10-18) |
|------|---------|--------------------------------------|
| Git | 2.54+ | ✅ installed |
| Python | 3.13+ (`py` launcher) | ✅ 3.14.6 |
| uv | 0.13.1 | ✅ installed via `py -m pip install --user uv==0.13.1` |
| Flutter / Dart | 3.44.1 / 3.12.1 | ✅ installed |
| Node.js / npm | 24 LTS+ / npm 11 | ✅ Node 26.3.0 |
| Java JDK (for the Firestore emulator) | 11+ | ✅ Temurin 21.0.12 (`winget install EclipseAdoptium.Temurin.21.JDK`) |
| Firebase CLI | 8.14+ | ✅ 15.32.1 (`npm install -g firebase-tools@15.32.1`) |
| Redis | 7+ (Memurai or WSL2) | ❌ optional until Redis-backed rate limiting lands |
| Android Studio + JDK 17 | — | ❌ to install (needed for Android builds) |
| macOS + Xcode | — | ❌ not available (needed for iOS builds) |

> The Windows `python` command is the Microsoft Store stub on this machine. Use `py` or `py -m uv`.
> Open a new terminal after installing the JDK so `java` is on `PATH`.

## 2. Database: Cloud Firestore (emulator locally)
The backend stores everything in Cloud Firestore ([ADR 0002](adr/0002-firestore-instead-of-postgresql.md)). Locally, use the emulator with a `demo-*` project ID. Demo projects have no real backend, cost nothing and can't touch production data.

```powershell
# from the repository root (firebase.json lives there)
firebase emulators:start --only firestore --project demo-wallora   # listens on 127.0.0.1:8085
```

In `backend/.env` set `FIRESTORE_EMULATOR_HOST=127.0.0.1:8085` and `FIRESTORE_PROJECT_ID=demo-wallora`. Emulator data is in memory and disappears when it stops.

Never set `FIRESTORE_EMULATOR_HOST` in staging or prod; the app refuses to start if you do.

## 3. Verify services
```powershell
cd backend
py -m uv run python scripts/dev_check.py
```

## 4. Run each package
See [backend/README.md](../backend/README.md), [mobile/README.md](../mobile/README.md) and the admin section below.

```powershell
cd admin
Copy-Item .env.example .env.local
npm ci
npm run dev        # http://localhost:3000
```

## 5. Quality checks
The per-package commands are listed in [AGENTS.md](../AGENTS.md). CI runs the same commands (`.github/workflows/`).

Backend integration tests need the emulator. `emulators:exec` starts it, runs the tests and stops it:
```powershell
cd backend
firebase emulators:exec --only firestore --project demo-wallora "py -m uv run pytest"
```

Optional pre-commit hooks:
```powershell
py -m pip install --user pre-commit
pre-commit install
```

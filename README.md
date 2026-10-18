# Wallora AI

AI wallpaper discovery, generation and personalisation for Android and iOS, with a secure Python backend and a web admin dashboard.

> **Status: Phase 0 (repository foundation).** No product features are implemented yet. See [docs/TASKS.md](docs/TASKS.md) for the honest, per-item status.

## Repository layout

| Path | What | Stack |
|------|------|-------|
| [mobile/](mobile/) | Android and iOS app | Flutter 3.44, Dart 3.12 |
| [backend/](backend/) | REST API and generation worker | Python 3.13+, FastAPI, SQLAlchemy, Alembic, PostgreSQL |
| [admin/](admin/) | Admin dashboard | Next.js 16, TypeScript, Tailwind |
| [infrastructure/](infrastructure/) | Deployment assets (Docker, Nginx) | Docker, Nginx |
| [docs/](docs/) | Architecture, API, threat model, plan | Markdown |

## Quick start

Prerequisites and step-by-step setup: **[docs/development.md](docs/development.md)**.

```powershell
# Backend
cd backend; py -m uv sync; py -m uv run pytest

# Mobile
cd mobile; flutter pub get; flutter test

# Admin
cd admin; npm ci; npm test
```

## Documentation
- Planning set: [docs/README.md](docs/README.md) (requirements, architecture, screens, database, API, generation & credits, threat model, monetization, plan, external setup)
- Contributor and agent rules: [AGENTS.md](AGENTS.md)
- Architecture decisions: [docs/adr/](docs/adr/)

## Security
Never commit credentials. Every package ships a `.env.example` (or `config/*.example.json`) containing placeholders only. Real values live in gitignored local files or the hosting secret manager. AI provider keys exist **only** on the backend.

# Infrastructure

Local development uses **native PostgreSQL + Redis** (owner decision, 2026-10-18). Docker isn't required on developer machines.

| Path | Purpose | Status |
|------|---------|--------|
| `compose.yaml` | Optional container stack (Postgres, Redis, MinIO) for deployment parity and Linux/macOS contributors | Provided, **not run locally** (no Docker on the owner's machine) |
| `docker/` | Production Dockerfiles for backend and admin | Phase 9 |
| `nginx/` | Reverse-proxy config (TLS, body limits, timeouts, security headers) | Phase 9 |
| `scripts/` | Backup/restore, smoke tests | Phase 9 |

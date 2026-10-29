# Infrastructure

The database is **Cloud Firestore** (ADR 0002). Local development uses the Firestore emulator, so Docker isn't required on developer machines.

| Path | Purpose | Status |
|------|---------|--------|
| `compose.yaml` | Optional container stack (Redis, MinIO) | Provided, **not run locally** (no Docker on the owner's machine) |
| `firebase/firestore.rules` | Denies all direct client access; only the backend (Admin SDK) reads and writes | Deployed with `firebase deploy --only firestore` (not deployed yet) |
| `firebase/firestore.indexes.json` | Composite indexes the backend's queries need | **Untested**: the emulator doesn't enforce indexes |
| `docker/` | Production Dockerfiles for backend and admin | Phase 9 |
| `nginx/` | Reverse-proxy config (TLS, body limits, timeouts, security headers) | Phase 9 |
| `scripts/` | Backup/restore, smoke tests | Phase 9 |

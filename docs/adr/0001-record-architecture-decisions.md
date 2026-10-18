# ADR 0001 — Record architecture decisions

- Status: Accepted
- Date: 2026-10-18

## Context
The project spans mobile, backend, admin and infrastructure. Decisions need to be traceable.

## Decision
Significant decisions are recorded as numbered ADRs in `docs/adr/` (context, decision, consequences). Superseded ADRs are kept and marked as superseded.

## Initial decisions (made during planning, see docs/02-architecture.md)
| # | Decision |
|---|----------|
| 1 | Monorepo: `mobile/`, `backend/`, `admin/`, `infrastructure/`, `docs/` |
| 2 | Flutter + Riverpod + go_router; FastAPI + SQLAlchemy 2 async + Alembic; Next.js App Router |
| 3 | PostgreSQL-backed job queue (`FOR UPDATE SKIP LOCKED`) so credit reservation and enqueueing are atomic |
| 4 | Credit lots + reservations + append-only ledger |
| 5 | Native PostgreSQL + Redis for local development, Docker for deployment only (owner decision) |
| 6 | Nginx reverse proxy in front of the API and admin (owner-confirmed stack) |
| 7 | App identifier `ai.wallora.app` (default, changeable before first store upload) |
| 8 | Image provider and model names are configuration, behind an `ImageProvider` abstraction |

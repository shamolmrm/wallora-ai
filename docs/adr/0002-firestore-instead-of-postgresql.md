# ADR 0002 — Cloud Firestore instead of PostgreSQL (for now)

- Status: Accepted
- Date: 2026-10-18
- Decided by: project owner ("FastAPI + Firestore"). The PostgreSQL implementation was committed and tagged `postgres-reference` first.

## Context
The generation backend was first built and tested on PostgreSQL (row locks, `SKIP LOCKED` queue, database-enforced append-only ledger). The owner prefers to stay on Firebase for now: one vendor for Auth, push and data, no database server to run, and a free tier to start.

## Decision
Keep the FastAPI backend and every security rule (server-side credits, idempotency, moderation, spend caps). Replace the persistence layer with Cloud Firestore via the Python Admin/Cloud client:

- Per-user credit state (lots, held, ledger sequence, rate and strike windows) lives on the user document, so each credit transaction reads one document and runs no queries.
- Idempotency, user provisioning and grants use deterministic document IDs created inside transactions.
- The job queue is the `generation_jobs` collection. The worker queries candidates, then claims each one with a transactional status check. Leases, reaper, bounded retries and lost-lease protection are unchanged.
- Client access is fully denied by security rules. Only the backend reads and writes.
- Transactions get an outer retry loop with jittered exponential backoff, because the library retries contention immediately.

## Consequences
- **Same guarantees, now tested on the emulator:** 122 tests pass (unit and integration), including concurrent same-key requests, no overspend under parallel requests, refunds, retries, lease loss and the reaper.
- **Weaker in places:** append-only ledger is enforced by code, not a database trigger. Global spend counters are single hot documents. Emulator contention is slow, so tests use 5-way concurrency, and production contention needs a staging load test.
- **Library defect found:** google-cloud-firestore 2.34.1 `AsyncTransaction.get(doc_ref)` / `.get_all()` raise `TypeError`. We use the public `ref.get(transaction=...)` / `query.stream(transaction=...)` instead.
- **New dev prerequisites:** Java JDK 11+ and Firebase CLI for the emulator.
- **Reversible:** the tagged PostgreSQL version remains available. Service-level APIs and HTTP contracts didn't change, so mobile and admin work isn't affected.

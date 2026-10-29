# Wallora AI — Central Task Checklist

Legend: `[ ]` not started · `[~]` partially implemented · `[M]` mocked/fake only · `[B]` blocked · `[U]` implemented but untested · `[x]` implemented **and** tested (with evidence)

Last updated: 2026-10-18 — Phase 4 moved to Cloud Firestore, awaiting review.

## Planning
- [x] Workspace inspected (empty, not a git repo; toolchain recorded in docs/README.md)
- [x] Architecture & plan documents drafted (docs/01–10)
- [x] Technology stack confirmed by owner (2026-10-18; Nginx added as reverse proxy)
- [x] Plan approved by owner (implicitly, via the Phase 0 implementation request)

## Phase 0 — Repository foundation (2026-10-18) ← awaiting owner review
- [x] git init (branch `main`) + .gitignore/.gitattributes/.editorconfig. Verified with `git check-ignore`: .env, .env.local, mobile config JSON, Firebase files, service-account JSON, .venv, .next, node_modules are ignored; every `.env.example`, `*.example.json` and lockfile is trackable. No commit made yet.
- [x] README.md, AGENTS.md, docs/development.md, docs/adr/0001
- [x] backend skeleton: uv 0.13.1, FastAPI app with `/health/live`, minimal settings, ruff (format + lint), mypy strict, pytest. Evidence: ruff format 8 files OK, ruff check passed, mypy 0 issues (7 files), pytest 2 passed (Python 3.14.6).
- [x] backend `scripts/dev_check.py` runs. It reports PostgreSQL/Redis **unreachable** (expected: not installed yet).
- [x] mobile skeleton: Flutter 3.44.1, app ID `ai.wallora.app` (Android + iOS), strict analyzer options, build-time env via `--dart-define-from-file`, config templates. Evidence: dart format 0 changed, flutter analyze no issues, flutter test 2 passed.
- [x] admin skeleton: Next.js 16.3.8, TS strict, ESLint, Prettier, Vitest + Testing Library, baseline security headers. Evidence: format:check OK, lint OK, typecheck OK, vitest 1 passed, `next build` succeeded.
- [x] Dependency audit: pip-audit no known vulnerabilities. npm prod deps 0 vulnerabilities. npm dev deps 5 high (single advisory GHSA-vfj7-8cjw-p6xm in `braces` via eslint-config-next, no non-breaking fix; dev-time only; CI reports without blocking).
- [U] infrastructure/compose.yaml: written, **not run** (no Docker locally, by decision)
- [U] CI workflows (backend, mobile, admin, security/gitleaks): written, **not run** (no remote repository yet)
- [U] .pre-commit-config.yaml: written, **not run** (pre-commit and gitleaks not installed locally)
- [B] Local secret scan with gitleaks: not installed. A regex scan for common key formats over all trackable files found no matches.
- [x] ~~Native PostgreSQL~~ superseded: database is Cloud Firestore (ADR 0002). JDK 21 + Firebase CLI 15.32.1 installed for the emulator.
- [B] Native Redis install (Memurai or WSL2) (owner) — optional early; in-memory fallback

## Phase 1 — Backend core
- [ ] Settings + prod safety checks
- [ ] Logging, redaction, request IDs, problem+json
- [ ] DB + Alembic baseline + all core tables
- [ ] Append-only triggers
- [ ] Firebase token verification + App Check (monitor)
- [ ] /auth/session, /me, /me/preferences
- [ ] Admin role guard + audit log service
- [ ] Rate limiter, idempotency, pagination utilities

## Phase 2 — Catalog, storage, admin content API
- [ ] Storage adapter (MinIO/R2), signed URLs
- [ ] Image pipeline (validate, re-encode, thumbnails, blurhash)
- [ ] Admin content endpoints
- [ ] Public catalog + discover + search
- [ ] Favorites, user collections, downloads, reports
- [ ] Seed script (original procedural art)

## Phase 3 — Mobile foundation (started 2026-10-18)
Evidence: flutter analyze 0 issues, flutter test 7 passed. Debug build installed and running on the owner's phone (CPH2365, Android 13) against the local backend (Firestore emulator + fake provider, `adb reverse tcp:8000`). End-to-end generation verified via the API (job succeeded, image served 200 image/webp).
- [x] Design tokens + light/dark theme, 5-tab shell (go_router), Riverpod providers, dio API client with problem-code error mapping
- [x] Studio: prompt, style presets, tier/shape picker filtered by model limits, cost + balance, idempotent submit (key reused on network retry)
- [x] Generation screen: polling with backoff, cancel while queued, retry, result image, failure/refund messages
- [x] Library (generation history grid), Profile (credit balance + ledger)
- [x] Discover/Search show an honest "coming with the catalog" empty state (no mock data)
- [~] Dev-only sign-in (`dev:<uid>` token, refused outside local/test). **Firebase Auth sign-in screens not built yet** (needs a Firebase project).
- [ ] Onboarding, save to gallery / set wallpaper (Android), share, favorites/collections, offline caching
- [ ] Localization: strings are in code, not ARB files yet
- [ ] Golden tests, accessibility pass (TalkBack, 200% text)
- [B] iOS build: no macOS

## Phase 4 — Generation backend on Cloud Firestore (2026-10-18)
Owner decision: FastAPI + Firestore (ADR 0002). The earlier PostgreSQL implementation (122 tests passing) is commit `42dc987`, tag `postgres-reference`.

Evidence for the Firestore version: **122 passed on 2 consecutive full runs** against the Firestore emulator (`firebase emulators:exec --only firestore --project demo-wallora`). ruff format/lint clean. mypy strict 0 issues (59 files). API (`uvicorn --factory`) and worker smoke-tested against the emulator. **No paid calls made.**
- [x] Provider abstraction + `FakeImageProvider`. `OpenAIImageProvider` (generate/edit, safety_identifier, usage-based cost) tested through the real SDK with a mocked HTTP transport only.
- [x] Moderation: local blocklist + OpenAI Moderation adapter (mocked) + fake. Fails closed. Strikes on the user document.
- [x] Prompt validation/normalisation. Tier/aspect/size rules from the official docs.
- [x] Upload sanitisation (magic bytes, bomb guards, re-encode, EXIF strip). Private storage (S3/R2, local HMAC-signed, memory).
- [x] Idempotent job creation (deterministic idempotency docs in-transaction). Atomic credit reserve/capture/release on the user document. Append-only ledger (`create()` only).
- [x] Spend caps (global day/month, user day cost/count), per-user rate limits, kill switch.
- [x] Worker: transactional claim + lease, reaper, bounded retries with jittered backoff, timeouts, output moderation, lost-lease protection, upload purge.
- [x] Cancel (queued only), retry, delete (erases prompt + objects), history pagination.
- [x] Firestore security rules deny all client access (backend-only). Rules file written; **not deployed**.
- [U] Composite indexes (`infrastructure/firebase/firestore.indexes.json`): written, **untested**. The emulator doesn't enforce indexes.
- [U] Production contention behaviour: emulator locks can take up to 30 s (documented limitation), so tests use 5-way concurrency. **Needs a staging load test (Phase 9).**
- [U] Real OpenAI calls: **never executed** (requires owner approval and a capped test batch).
- [U] S3/R2 storage adapter: written, not exercised against a real bucket.
- [U] Firebase token verification: written, not exercised against real Firebase (tests use a fake verifier).
- [U] CI workflow updated for the emulator (Java + firebase-tools): written, **not run** (no remote repository).
- [ ] Studio/wallet mobile UI (depends on the mobile foundation phase).
- [ ] **Open decision before the catalog phase:** Firestore has no full-text search (tags/filters only vs. a search service).
- Known workaround: google-cloud-firestore 2.34.1 `AsyncTransaction.get(doc_ref)`/`get_all()` raise `TypeError`. The code uses `ref.get(transaction=…)` / `query.stream(transaction=…)` instead.

## Phase 5 💲 — OpenAI integration
- [x] Docs checked 2026-10-18 (models, params, edits, moderation, pricing)
- [ ] Test batch approved (budget: ___)
- [ ] Real-call verification of adapters and usage/cost fields
- [ ] Cost/latency report → tier config (estimates are currently UNVERIFIED placeholders)

## Phase 6 — Purchases & ads (sandbox)
- [ ] RevenueCat SDK, paywall, restore, status
- [ ] Entitlement sync + webhook + consumable grants + refunds
- [ ] AdMob rewarded (test units) + UMP/ATT + SSV

## Phase 7 — Admin dashboard
- [ ] Auth + MFA + roles
- [ ] Content, taxonomy, collections, presets
- [ ] Reports/moderation, users, credits, jobs
- [ ] Config editor, metrics, audit, admins

## Phase 8 — Notifications, deletion, privacy
- [ ] FCM + preferences + inbox
- [ ] Account deletion + purge
- [ ] Retention jobs
- [ ] Privacy policy / terms drafts (legal review pending)

## Phase 9 💲 — Hardening & release
- [ ] Security review, dependency audit
- [ ] Load test, backup/restore drill
- [ ] Deployment guide + staging deploy (approval required)
- [ ] Store release checklist

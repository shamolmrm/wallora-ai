# Wallora AI — Central Task Checklist

Legend: `[ ]` not started · `[~]` partially implemented · `[M]` mocked/fake only · `[B]` blocked · `[U]` implemented but untested · `[x]` implemented **and** tested (with evidence)

Last updated: 2026-10-18 — Phase 0 implemented, awaiting review.

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
- [B] Native PostgreSQL 16+ install (owner) — local dev decision: native, no Docker
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

## Phase 3 — Mobile foundation & catalog UX
- [ ] Design system + themes
- [ ] Router shell, API client, auth screens, onboarding
- [ ] Discover, categories, collections, search/filters
- [ ] Detail, info, crop/mock-up preview, save/set (Android), iOS guide
- [ ] Library (favorites, collections, downloads)
- [ ] All UI states + l10n + semantics
- [B] Android build — Android SDK/JDK not installed
- [B] iOS build — no macOS environment

## Phase 4 — Generation backend (PostgreSQL implementation, 2026-10-18)
Evidence: 122 tests passed on 3 consecutive runs against a throwaway local PostgreSQL 18.4 cluster (since deleted); ruff format/lint clean; mypy strict 0 issues (67 files). No paid calls made.
- [x] Provider abstraction + `FakeImageProvider`; `OpenAIImageProvider` (generate/edit, safety_identifier, usage-based cost) tested through the real SDK with a mocked HTTP transport only
- [x] Moderation: local blocklist + OpenAI Moderation adapter (mocked) + fake; fails closed; strikes
- [x] Prompt validation/normalisation; tier/aspect/size rules from the official docs
- [x] Upload sanitisation (magic bytes, bomb guards, re-encode, EXIF strip); private storage (S3/R2, local HMAC-signed, memory)
- [x] Idempotent job creation; atomic credit reserve/capture/release with lots + append-only ledger
- [x] Spend caps (global day/month, user day cost/count), rate limits, kill switch
- [x] Worker: SKIP LOCKED claim, leases, reaper, bounded retries with jittered backoff, timeouts, output moderation, lost-lease protection
- [x] Cancel (queued only), retry, delete (erases prompt + objects), history pagination
- [U] Real OpenAI calls: **never executed** (requires owner approval and a capped test batch)
- [U] S3/R2 storage adapter: written, not exercised against a real bucket
- [U] Firebase token verification: written, not exercised against real Firebase (tests use a fake verifier)
- [ ] Studio/wallet mobile UI (depends on mobile foundation phase)
- [~] **Owner decision 2026-10-18: switch persistence to Cloud Firestore (keep FastAPI).** PostgreSQL version committed for reference before the switch.

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

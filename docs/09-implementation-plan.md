# 9. Phased Implementation Plan

Each phase ends with formatters, linters, tests, available builds, an updated [TASKS.md](TASKS.md), and a **stop for your review**. Phases marked 💲 need your explicit permission for paid calls or resources.

| Phase | Name | Depends on |
|-------|------|------------|
| 0 | Repository foundation | approval of this plan |
| 1 | Backend core (config, DB, auth, users) | 0, Postgres available |
| 2 | Catalog, storage and admin content API | 1 |
| 3 | Mobile foundation and catalog UX | 2 (fake API in tests) |
| 4 | Credits engine, generation pipeline (fake provider), Studio UI | 1–3 |
| 5 💲 | OpenAI provider and moderation integration, reference images | 4 + approval for a capped test batch |
| 6 | Purchases, entitlements, rewarded ads (sandbox) | 4, store/RevenueCat/AdMob accounts |
| 7 | Admin dashboard | 2, 4, 6 APIs |
| 8 | Notifications, account deletion, privacy, settings, localization | 1–6 |
| 9 💲 | Hardening, deployment, release readiness | all (paid hosting only with approval) |
| 5b | Post-MVP: masked edit, variations UI, outpainting (experimental) | 5 |

---

### Phase 0 — Repository foundation
**Deliverables**: `git init` (with your approval), `.gitignore` / `.gitattributes` / `.editorconfig`, README.md, AGENTS.md, monorepo skeleton, `backend/` (uv project, ruff, mypy strict, pytest), `mobile/` (`flutter create` with org ID, very_good_analysis or flutter_lints strict, flavors dev/prod), `admin/` (Next.js + TS strict + ESLint + Prettier + Vitest), `infrastructure/compose.yaml` (Postgres, Redis, MinIO; optional, for deployment parity), local storage adapter option (filesystem-backed S3-compatible stub or a native MinIO binary, since there's no Docker locally), `.env.example` files (placeholders only), GitHub Actions workflows (lint + test per package, gitleaks), pre-commit hooks, ADR template.
**Acceptance criteria**
- `uv run pytest` (smoke test), `flutter analyze && flutter test`, and `npm run lint && npm test` all run green locally.
- gitleaks scan passes. No secrets present.
- Local dev runs against **native PostgreSQL + Redis** (owner decision). A `scripts/dev-check` command verifies connectivity, extensions and the test database. `infrastructure/compose.yaml` is still provided for deployment parity but isn't required locally.

### Phase 1 — Backend core
**Deliverables**: settings with prod safety checks, structured logging + redaction, request ID middleware, problem+json errors, health endpoints, async SQLAlchemy + Alembic baseline, **all core tables from §4** (migrations), Firebase token verification dependency (with a fake verifier for tests), App Check verification (monitor mode), `POST /auth/session`, `/me`, `/me/preferences`, admin role guard, audit log service, Redis rate limiter (with in-memory fallback), idempotency utility, cursor pagination, OpenAPI.
**Acceptance criteria**
- Migrations: upgrade → downgrade → upgrade passes on a clean DB.
- Append-only triggers reject UPDATE/DELETE on `credit_ledger` and `audit_logs` (tested).
- Tests: valid/invalid/expired/wrong-audience/anonymous tokens. Suspended user → 403. Admin endpoints → 403 for non-admins. Rate limit → 429 with Retry-After.
- Log redaction test proves tokens, emails and prompts never appear.
- Coverage ≥ 85% for `core/` and `modules/users`.

### Phase 2 — Catalog, storage, admin content API
**Deliverables**: S3 storage adapter (MinIO local / R2 prod), image pipeline (validate, decode limit, re-encode, thumbnails at 3 widths, blurhash, dominant colour, sha256 dedupe), admin wallpaper upload (presigned PUT + finalize), publish workflow, categories/tags/collections CRUD, public catalog endpoints, discover sections, FTS + trigram search, trending score job, signed download URLs + premium gate, favorites, user collections, downloads, reports endpoint, **dev seed script** with locally generated original placeholder art (procedural; no third-party images).
**Acceptance criteria**
- Upload tests: wrong magic bytes, oversized, decompression bomb, EXIF stripped, duplicate sha256 → 409.
- Search: relevance, tag filter, aspect filter, pagination stability (no duplicates or skips across pages).
- Premium download as free user → 403. Signed URL expires (tested with a clock).
- Cross-user collection access → 404. Favorite PUT is idempotent.
- p95 < 300 ms for `/discover` and `/wallpapers` on the seeded 5k-row dataset (local benchmark, reported with actual numbers).

### Phase 3 — Mobile foundation and catalog UX
**Deliverables**: design tokens + light/dark themes, typography, core components, go_router shell with 5 tabs, API client (dio) with problem-details mapping, Firebase Auth (Apple, Google, email) + sign-in screens, onboarding + preferences, Discover, categories, collections, search + filters, wallpaper detail pager, info sheet, crop/mock-up preview, save to gallery (`gal` or equivalent, verified for current platform rules), **Android set-wallpaper** via platform channel (`WallpaperManager`), iOS guide, share, favorites, collections, downloads, offline caching, all UI states, l10n ARB, semantics.
**Acceptance criteria**
- Widget tests for loading/empty/error/offline/permission-denied on every screen listed in §3. Golden tests for key components in both themes.
- Unit tests for repositories using a fake API.
- `flutter analyze` with zero issues. `flutter test` green.
- Android debug APK builds (**requires Android SDK + JDK install**). iOS build is **untested until a macOS environment is available** and gets reported as such.
- Manual check on an emulator: text scale 200%, TalkBack labels, small screen (360×640 dp).

### Phase 4 — Credits engine, generation pipeline (fake provider), Studio UI
**Deliverables**: credit lots/reservations/ledger service, welcome + daily grants, expiry job, reconciliation job, spend counters + caps + kill switch, `ImageProvider` interface + `FakeImageProvider`, moderation interface + blocklist + fake moderation, generation endpoints, worker with SKIP LOCKED claim, leases, reaper, retries with backoff, cancellation, output pipeline, generation history. Mobile: Studio composer, presets, tier/aspect selection with cost, template prompt enhancer, progress screen (polling), result screen, history, wallet screen (balance, lots, ledger).
**Acceptance criteria (tests on real Postgres)**
- Concurrency: 20 parallel requests with balance for 6 → exactly 6 jobs, 14 × 402, balances consistent.
- Same Idempotency-Key ×10 in parallel → 1 job, 1 reservation. Same key with a different body → 422.
- Insufficient credits → 402, no job. Prompt blocked → 422, no reservation.
- Provider failure → credits released (ledger: reserve + release). Retryable error → retried ≤ max_attempts with backoff. Worker crash simulation → reaper recovers.
- Cancel while queued → released. Cancel while processing → 409.
- Global cap reached → 503, no reservation.
- Ledger invariants hold after a randomized property-based test (Hypothesis).
- Mobile widget tests for every Studio error state (402, 422, 429, 503, offline).

### Phase 5 💲 — OpenAI integration and reference images
**Before starting**: re-read the official OpenAI docs (models, params, usage fields, error codes, moderation API). Show you the exact test plan: number of calls, models, sizes, and **maximum spend (e.g. ≤ $3)**. Wait for approval.
**Deliverables**: `OpenAIImageProvider` (generate + edit with references), OpenAI moderation adapter (text + image), capability reporting, usage-based actual cost, error classification, timeouts, contract tests against recorded fixtures (no network in CI), reference-image upload UX with consent, measured cost/latency report that updates the tier configs.
**Acceptance criteria**
- CI never calls OpenAI (enforced: network-blocking fixture + `IMAGE_PROVIDER=fake`).
- Recorded-fixture tests for success, 400, 429, 500, timeout and safety refusal map to the right job state and credit outcome.
- Manual, approved test batch executed, with results (cost, latency, quality notes) documented in `docs/reports/`.

### Phase 6 — Purchases, entitlements, rewarded ads (sandbox only)
**Before starting**: re-verify the RevenueCat webhook and REST API docs, AdMob SSV docs, and store policies.
**Deliverables**: RevenueCat SDK (`purchases_flutter`) with app user ID = our user UUID, paywall from offerings, restore, subscription status, billing issue banner, cancellation/manage guidance, `/me/entitlements/sync`, RevenueCat webhook handler, consumable credit grants, subscription period credit grants, refund revocation, AdMob rewarded with **test ad units**, UMP consent + iOS ATT, SSV endpoint, ad-free enforcement for premium.
**Acceptance criteria**
- Webhook: bad secret → 401. Replayed event → no double grant. Out-of-order events resolved by REST re-fetch. Refund → revocation tested.
- SSV: invalid signature → rejected. Replayed transaction_id → no double grant. Daily cap enforced.
- Forged client entitlement claims ignored (test).
- Sandbox purchase and restore verified manually on a device, with status reported honestly (Android first, iOS when a Mac is available).

### Phase 7 — Admin dashboard
**Deliverables**: Firebase web sign-in with MFA, role-aware navigation, wallpapers (upload, edit, publish, bulk), categories/tags (merge), collections (drag-reorder), presets, reports and moderation queue, users (search, suspend, credit adjust), jobs explorer, config editor (schema-validated forms with a diff preview), metrics (usage, cost by day/model/tier, error rates, latency), audit log viewer, admin management.
**Acceptance criteria**
- Unauthorized/non-admin users can't see data (Playwright). The server enforces every permission independently of the UI.
- Vitest component tests. Playwright e2e against a seeded backend with the fake provider.
- No secrets or full emails rendered (masked). CSP enforced. Responsive down to 375 px. Keyboard-navigable. axe accessibility checks pass.

### Phase 8 — Notifications, deletion, privacy, settings, localization
**Deliverables**: FCM token registration, "generation ready" push, notification preferences + inbox, OS-permission handling, account deletion flow + purge worker (Firebase user, storage, PII) with a grace window, data-retention jobs (reference uploads, webhook payloads), privacy policy and terms drafts (**need legal review**), settings screens, theme switching, l10n pipeline ready for more languages.
**Acceptance criteria**
- Deletion integration test: after purge, there's no PII in the DB, storage objects are gone, the Firebase delete was called (mocked), and the ledger is pseudonymised.
- Opted-out users never receive that notification type (test).
- Retention jobs delete only eligible rows (tests with a frozen clock).

### Phase 9 💲 — Hardening and release readiness
**Deliverables**: security review against §7, dependency audit, load test (k6 or Locust) on staging, backup/restore drill, Dockerfiles (non-root, pinned digests), Nginx reverse-proxy config (body limits, timeouts, security headers, `/metrics` blocked publicly), deployment guide for the chosen host, staging deployment (**only with your approval**), monitoring/alerts, store listing assets, data-safety and privacy-nutrition answers, store release checklist, final honest status report.
**Acceptance criteria**
- No high/critical vulnerabilities open (or documented with justification).
- Restore from backup verified, with RTO/RPO measured.
- Load test meets the documented targets. Results recorded.
- Release checklist completed, with any remaining blockers listed.

# 7. Security Threat Model

## 7.1 Assets
1. AI provider credentials and other server secrets (OpenAI, RevenueCat secret key, R2 keys, Firebase service account, webhook secrets).
2. AI spend (directly converts to money).
3. Credit balances and entitlements (fungible value).
4. User data: emails, prompts, reference photos (may contain faces or personal content), generated images, device tokens.
5. Catalog integrity and licensed content.
6. Admin capabilities.
7. Platform reputation and store standing (harmful content, policy violations).

## 7.2 Trust boundaries
```
[Mobile app / Admin browser]  (untrusted)
   │ HTTPS + Firebase ID token (+ App Check)
[API]  ─── trusted zone ───  [Worker] [Scheduler] [Postgres] [Redis]
   │                              │
[Webhooks in: RevenueCat, AdMob SSV] (untrusted until verified)
[Out: OpenAI, Firebase, RevenueCat REST, R2] (trusted TLS endpoints, responses still validated)
```

## 7.3 STRIDE analysis

| # | Threat | STRIDE | Scenario | Mitigations | Tests |
|---|--------|--------|----------|-------------|-------|
| T1 | Forged/expired token | S | Attacker sends a crafted JWT | Firebase Admin `verify_id_token` (sig, aud=project, iss, exp). Revocation check on sensitive ops. Reject anonymous provider for protected writes. | invalid sig, wrong aud, expired, revoked, anonymous → 401/403 |
| T2 | IDOR | I/E | Access another user's job/image/collection by ID | Every query is scoped by `user_id` in the repository layer. Return 404 (not 403) for non-owned resources. UUIDv7 IDs aren't enumerable but are **not** relied on. | cross-user access tests per resource |
| T3 | Privilege escalation to admin | E | Client sets claims or calls admin API | Roles live only in the `admin_roles` table and are checked per endpoint. Admin accounts require Firebase **MFA** (enforced server-side via the token's `firebase.sign_in_second_factor`). Optional IP allowlist. | non-admin → 403, role matrix tests, no-MFA admin → 403 |
| T4 | Credit double-spend / race | T | Parallel requests to spend the same credits | Row lock on `credit_accounts`, lot allocation in a transaction, CHECK constraints, UQ(job_id) on reservations | concurrency tests with asyncio + real Postgres |
| T5 | Duplicate jobs/charges | T | Client retries on flaky network | Required Idempotency-Key + UQ(user_id, key) + request hash | duplicate/concurrent same-key tests |
| T6 | Fake purchase claims | S/T | Client claims "I bought 1000 credits" | Client claims are ignored. Grants only from RevenueCat REST state or verified webhooks. UQ(store, transaction_id). | forged sync body → no grant |
| T7 | Webhook forgery/replay | S/R | Replay old RevenueCat events or forge AdMob callbacks | RevenueCat: constant-time `Authorization` secret compare + re-fetch state from REST API (webhook body is never trusted alone) + UQ(event id). AdMob SSV: ECDSA verification with Google verifier keys + UQ(transaction_id) + nonce bound to user + nonce expiry. | bad secret/sig → 401, replay → 200 no-op, unknown nonce → ignored |
| T8 | AI cost exhaustion | D | Bots farm free credits or spam generation | Generation requires a non-anonymous account + App Check. Per-user/IP rate limits. Welcome/daily lots are small. Disposable-email heuristics + email verification for email sign-ups. Global and per-user daily spend caps. Kill switch. OpenAI project-level budget limit set in the dashboard as a last line of defence. | cap reached → 503, rate limit → 429 |
| T9 | Harmful content generation | — (abuse) | Jailbreak prompts for sexual, violent, hateful or illegal imagery; real-person deepfakes | Layered: blocklist, then OpenAI Moderation API on the prompt, then provider safety (`moderation=auto`), then output image moderation. Strikes and temporary bans. User reports with an admin queue. Reference images of real people need a consent checkbox and moderation. **Zero tolerance for sexual content involving minors**: block, preserve minimal evidence, follow legal reporting obligations (e.g. NCMEC in the US). Legal review needed before launch. | moderation fake provider tests for each decision path |
| T10 | Malicious upload | T/D/E | Polyglot files, decompression bombs, EXIF GPS leak, huge files | Max body size at the proxy and in the app, magic-byte sniffing (not the client MIME), Pillow `MAX_IMAGE_PIXELS` cap, full decode + re-encode (drops payloads and EXIF), dimension limits, objects stored with server-generated keys and fixed Content-Type, never served from the API origin | bomb, wrong-magic, oversized, EXIF-stripped tests |
| T11 | SSRF | E | User supplies a URL to fetch | **No** user-supplied URL is ever fetched server-side. Uploads are multipart or presigned-PUT only. | — |
| T12 | Storage data exposure | I | Guessing or leaking private object URLs | Private bucket for user content; public/CDN bucket **only** for published catalog previews. Signed GET URLs with a short TTL (5–15 min). Unguessable keys (`u/{uuid}/g/{uuid}.webp`). Full-resolution premium originals are private. | signed URL expiry test |
| T13 | Secret leakage | I | Keys in repo, logs, crash reports, mobile binary | Secrets only in env/secret manager. `.env*` gitignored. **gitleaks** in CI + pre-commit. structlog redaction processor (Authorization, tokens, keys, emails, prompts). Mobile binaries contain only public identifiers (Firebase config, RevenueCat *public* SDK key, AdMob IDs). | redaction unit test, gitleaks CI |
| T14 | Injection | T | SQL injection, header injection | SQLAlchemy bound params only, no string SQL. Pydantic strict models, length limits (prompt ≤ 1000 chars). | fuzz-ish validation tests |
| T15 | Admin XSS/CSRF | T/E | Malicious tag names render in the dashboard | React escaping, strict CSP, no `dangerouslySetInnerHTML`. Bearer token auth (no cookie CSRF surface) or, if session cookies are used, SameSite=strict + CSRF token. | Playwright CSP check |
| T16 | Repudiation of admin actions | R | Admin denies changing config or credits | Append-only `audit_logs` with actor, request ID, diff. DB trigger blocks updates. | audit entry per mutation test |
| T17 | Account takeover consequences | S | Stolen session deletes the account | Recent-login requirement for deletion, 14-day cancellable grace, email notification of deletion request | auth_time test |
| T18 | Dependency compromise | T | Malicious package | Lockfiles (uv.lock, pubspec.lock, package-lock), Dependabot, `pip-audit`, `npm audit`, minimal deps, pinned Docker base images by digest | CI security job |
| T19 | DoS on API | D | Request floods | Rate limits (Redis), request size limits, timeouts on all outbound calls, DB pool limits, CDN for images, WAF/edge rate limits (Cloudflare) in prod | load test in Phase 9 |
| T20 | Privacy/compliance | I | Prompts or photos used beyond purpose | Private by default, no use for training by us, documented retention, deletion, consent for ads personalisation (UMP SDK / ATT on iOS), data-safety forms accurate | deletion purge integration test |

## 7.4 Security controls checklist (tracked in TASKS.md)
- [ ] HTTPS only (HSTS) in prod. TLS terminated at the edge.
- [ ] CORS: admin origin only. Mobile doesn't need CORS.
- [ ] Security headers on admin (CSP, X-Frame-Options DENY, Referrer-Policy).
- [ ] Safe error responses (problem+json, no stack traces). Request IDs for support.
- [ ] Least-privilege DB roles (`app_rw` without DDL, no UPDATE/DELETE on append-only tables, `migrator` separate).
- [ ] R2 credentials scoped per bucket.
- [ ] Backups encrypted. Restore tested (Phase 9).
- [ ] Threat model reviewed at the end of each phase that touches money, auth, uploads or AI.

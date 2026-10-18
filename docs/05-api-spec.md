# 5. API Endpoint Specification (v1)

The generated OpenAPI 3.1 document at `/openapi.json` (with docs at `/docs`, **disabled in prod** or put behind admin auth) is the source of truth once implemented. This file is the design contract.

## 5.1 Conventions
- Base path `/v1`. JSON bodies, `snake_case` fields, ISO-8601 UTC timestamps.
- **Auth**: `Authorization: Bearer <Firebase ID token>`, verified with the Firebase Admin SDK (signature, `aud`, `iss`, `exp`). Revocation is checked (`check_revoked=True`) on sensitive operations (deletion, purchases sync, admin).
- **App Check**: `X-Firebase-AppCheck` is required on mobile write endpoints and generation. Enforcement is controlled by a flag so it can roll out in monitor-only mode first.
- **Idempotency**: `Idempotency-Key: <uuid>` is **required** on `POST /generations`, `POST /rewards/ad-sessions` and `POST /me/entitlements/sync`, and optional on other POSTs. Same key + same body returns the original response. Same key + a different body returns `422 idempotency_key_reused`.
- **Errors**: RFC 9457 `application/problem+json`: `{type, title, status, code, detail, request_id, errors?}`. `detail` never includes internals.
- **Pagination**: cursor-based: `?limit=20&cursor=<opaque>` → `{items, next_cursor}`. Max limit 50.
- **Rate limits**: `429` with `Retry-After`. `X-RateLimit-*` headers are included.
- **Auth levels**: 🌐 public · 👤 signed-in user · 🛡 admin role (listed) · 🔗 webhook (signature/secret).

## 5.2 System
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health/live` | 🌐 | Process alive |
| GET | `/health/ready` | 🌐 | DB + Redis + storage reachable (no details leaked) |
| GET | `/metrics` | internal network only | Prometheus |
| GET | `/v1/config` | 🌐 | Public client config: min app version, maintenance flag, feature flags, studio tiers and credit costs, allowed sizes, RevenueCat offering ID, AdMob reward config, legal URLs. **Never secrets.** |

## 5.3 Catalog (guest-accessible)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/discover` | Sections: featured, trending, latest, collections, categories (personalised by preferences if signed in) |
| GET | `/v1/wallpapers` | Query: `q, category, tags[], aspect (portrait/landscape/square), color, premium (true/false), sort (latest/trending/popular/relevance), cursor, limit` |
| GET | `/v1/wallpapers/{id}` | Detail with preview URL (CDN), tags, licence/attribution, `is_favorite` if signed in |
| POST | `/v1/wallpapers/{id}/download` | Returns a short-lived signed URL for the full-resolution original. Records a download. **Premium items: 403 `premium_required`** unless the server-side entitlement is active. Guests get rate limits. |
| GET | `/v1/categories` · `/v1/categories/{slug}` | |
| GET | `/v1/collections` · `/v1/collections/{id_or_slug}` | Editorial collections and items |
| GET | `/v1/tags?q=` | Autocomplete (trigram) |
| GET | `/v1/search/suggestions?q=` | Tags + categories + popular queries |

## 5.4 Account & profile 👤
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/auth/session` | Upsert the user from a verified token (first sign-in creates user, preferences, credit account and the welcome credit lot). Anonymous Firebase users are rejected. |
| GET / PATCH | `/v1/me` | Profile (display name, avatar upload via `/uploads?purpose=avatar`) |
| GET / PUT | `/v1/me/preferences` | Theme, notification toggles, consents, preferred categories |
| DELETE | `/v1/me` | Schedules deletion (requires a recent sign-in, `auth_time` < 5 min) → `202 {scheduled_for}` |
| POST | `/v1/me/deletion/cancel` | Cancels during the grace window (only callable before the purge) |
| POST | `/v1/me/devices` · DELETE `/v1/me/devices/{token}` | Register or unregister FCM tokens |
| GET | `/v1/me/notifications` · POST `/v1/me/notifications/{id}/read` | Inbox |

## 5.5 Library 👤
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/me/favorites` | Paginated |
| PUT / DELETE | `/v1/me/favorites/{wallpaper_id}` | Idempotent add/remove |
| GET / POST | `/v1/me/collections` | List / create (limit per user, configurable) |
| GET / PATCH / DELETE | `/v1/me/collections/{id}` | Owner only; others get **404** (no existence leak) |
| POST | `/v1/me/collections/{id}/items` | `{wallpaper_id}` or `{generated_image_id}` (must be owned) |
| PATCH / DELETE | `/v1/me/collections/{id}/items/{item_id}` | Reorder / remove |
| GET | `/v1/me/downloads` | Recent downloads |

## 5.6 Studio 👤
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/studio/presets` | Active style presets (premium flags) |
| GET | `/v1/studio/capabilities` | Provider capabilities filtered by user tier: sizes, tiers, edit/reference support, max references |
| POST | `/v1/studio/prompt-enhance` | MVP: deterministic template enhancer (free, rate-limited). Returns a suggestion and **never** consumes credits. |
| POST | `/v1/uploads` | Multipart reference image. Validates size (≤ 10 MB, configurable), magic-byte MIME (PNG/JPEG/WebP), decodes with a pixel limit, re-encodes, strips metadata, moderates. → `{id, width, height, moderation_status}` |
| DELETE | `/v1/uploads/{id}` | Owner only |
| POST | `/v1/generations` | **Idempotency-Key required.** Body: `{kind, prompt, style_preset_id?, tier, aspect_ratio or size, reference_upload_ids?[], source_generated_image_id? (variation)}` → `202 {job}`. Errors: `402 insufficient_credits`, `422 prompt_rejected`, `403 tier_not_allowed`, `429 rate_limited`, `503 generation_paused` (cost cap / kill switch) |
| GET | `/v1/generations` | History (paginated, filter by status) |
| GET | `/v1/generations/{id}` | Poll status: `{status, progress_hint, images[], credit_cost, error_code}`. Images have short-lived signed URLs. |
| POST | `/v1/generations/{id}/cancel` | `200` if it was queued (credits released). `409 not_cancellable` if processing or finished. |
| POST | `/v1/generations/{id}/retry` | Creates a **new** job from a failed or cancelled one (new Idempotency-Key required, new reservation) |
| DELETE | `/v1/generations/{id}` | Deletes the job record and its images from history and storage |
| POST | `/v1/generated-images/{id}/download` | Signed URL and download record |
| DELETE | `/v1/generated-images/{id}` | |

## 5.7 Credits & billing
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/me/credits` | 👤 | `{available, held, lots:[{source, remaining, expires_at}], daily_free:{granted_today, resets_at}, ad_rewards:{remaining_today}}`. Lazily grants today's daily lot. |
| GET | `/v1/me/credits/ledger` | 👤 | Paginated ledger (user-safe view) |
| GET | `/v1/me/entitlements` | 👤 | Server-verified entitlement state: `{is_premium, plan, status, expires_at, will_renew, manage_url_hint}` |
| POST | `/v1/me/entitlements/sync` | 👤 | After purchase or restore, the client asks the server to **fetch from the RevenueCat REST API** (the client's claim is ignored). Grants consumable credits for unseen transactions. Rate-limited. |
| POST | `/v1/rewards/ad-sessions` | 👤 | Issues a nonce passed to AdMob as SSV `custom_data`. Enforces the daily ad cap before showing an ad. |
| POST | `/v1/webhooks/revenuecat` | 🔗 | Verifies the `Authorization` header against the configured secret (constant-time compare), dedupes by `event.id`, stores the event, then re-fetches subscriber state from RevenueCat before acting |
| GET | `/v1/webhooks/admob-ssv` | 🔗 | Verifies the ECDSA signature with Google's published verifier keys (cached, rotated by `key_id`). Dedupes by `transaction_id` and grants the reward lot for a valid nonce. |

## 5.8 Moderation (user side) 👤
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/reports` | `{target_type, target_id, reason, details?}`. One report per user per target. |

## 5.9 Admin API 🛡 (`/v1/admin/*`; every mutation writes `audit_logs`)
| Area | Endpoints | Roles |
|------|-----------|-------|
| Wallpapers | `GET/POST /wallpapers`, `GET/PATCH/DELETE /wallpapers/{id}`, `POST /wallpapers/uploads` (presigned PUT + finalize → processing), `POST /wallpapers/{id}/publish`, `/unpublish`, `POST /wallpapers/bulk` (tag/category/status) | content_admin |
| Categories / tags | CRUD, `POST /tags/merge`, reorder | content_admin |
| Collections | CRUD editorial collections, items, reorder, feature | content_admin |
| Style presets | CRUD | content_admin |
| Reports & moderation | `GET /reports`, `PATCH /reports/{id}` (resolve with action: remove content, dismiss, suspend user), `GET /moderation/flags` | moderator |
| Users | `GET /users?q=` (limited fields: id, masked email, status, created, premium flag), `GET /users/{id}`, `POST /users/{id}/suspend` / `unsuspend` | support (read), moderator (suspend) |
| Credits | `GET /users/{id}/credits`, `POST /users/{id}/credits/adjust` `{amount, reason}` (reason required, capped per action) | support (cap), super_admin |
| Jobs | `GET /generations?status=&user=&from=`, `GET /generations/{id}` (prompt shown only to `moderator`; viewing is audit-logged) | analyst, moderator |
| Config | `GET/PUT /settings/{key}` (credit costs, allowances, caps, product mapping, kill switch) with schema validation and a diff in the audit log | super_admin |
| Metrics | `GET /metrics/overview`, `/metrics/costs?group_by=day,model,tier`, `/metrics/errors`, `/metrics/jobs` | analyst |
| Audit | `GET /audit-logs` | super_admin |
| Admins | `GET/POST/DELETE /admins` (grant/revoke roles; cannot revoke your own super_admin) | super_admin |
| Notifications | `POST /notifications/broadcast` (post-MVP) | super_admin |

## 5.10 Example: create generation
```http
POST /v1/generations
Authorization: Bearer eyJ...
X-Firebase-AppCheck: eyJ...
Idempotency-Key: 6b0d9a1e-3b7e-4e4b-9b1a-0f6e2b9c7d11
Content-Type: application/json

{"kind":"generate","prompt":"misty pine forest at dawn, volumetric light",
 "style_preset_id":"0192...","tier":"standard","aspect_ratio":"9:19.5"}
```
```http
HTTP/1.1 202 Accepted
{"id":"0192...","status":"queued","credit_cost":3,"tier":"standard",
 "width":1072,"height":2320,"created_at":"2026-10-18T10:00:00Z",
 "credits":{"available":7,"held":3}}
```
Resolution presets are **config values verified against the provider's documented constraints** (multiples of 16, aspect 1:3–3:1, pixel budget), not hard-coded in clients.

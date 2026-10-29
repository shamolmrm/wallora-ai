# 10. External Accounts, Environment Variables, Manual Setup

**I will not create accounts or paid resources or enter credentials.** You do these steps. Secrets go into local `.env` files (gitignored) or the host's secret manager, never into chat or the repository.

## 10.1 Accounts and services
| Service | Needed for | Cost (verify current pricing) | When |
|---------|------------|------------------------------|------|
| GitHub (or other Git host) | Repo, CI | Free tier OK | Phase 0 |
| Firebase project ×2 (`wallora-dev`, `wallora-prod`) | Auth, App Check, FCM | Spark (free) is likely enough to start | Phase 1/3 |
| Google Cloud service account for the Firebase Admin SDK | Backend token verification, FCM send | — | Phase 1 |
| OpenAI API platform account, **separate project for Wallora** with a **monthly budget limit** and usage alerts | Image generation, moderation | Pay-as-you-go | Phase 5 💲 |
| Cloudflare account + R2 buckets (`wallora-user-private`, `wallora-catalog-public`) + scoped API tokens | Object storage + CDN | Free tier, then usage-based | Phase 2 (prod) — MinIO locally |
| RevenueCat project | Purchases/entitlements | Free tier up to a revenue threshold | Phase 6 |
| Apple Developer Program | iOS builds, IAP, Sign in with Apple, APNs key for FCM | $99/year | Phase 3 (iOS) / 6 |
| Google Play Console | Android release, billing | $25 one-time | Phase 6 |
| Google AdMob | Rewarded ads (test IDs during dev) | Free | Phase 6 |
| Hosting for API/worker (e.g. Fly.io, Render, Railway, Google Cloud Run) + Firestore on the Firebase project (Blaze plan once free quotas are exceeded) + Redis (e.g. Upstash) | Staging/prod | Usage-based | Phase 9 💲 |
| Domain + Cloudflare DNS | API, admin, legal pages, account deletion web page | ~$10–15/year | Phase 8/9 |
| Sentry (optional) | Error monitoring | Free tier | Phase 9 |
| macOS machine **or** cloud macOS CI (Codemagic / GitHub macOS runners) | iOS builds and signing | Varies | Before any iOS testing |

## 10.2 Local machine setup (Windows)
- [x] **Java JDK 21 + Firebase CLI 15.32.1** for the Firestore emulator (installed 2026-10-18). Cloud Firestore replaced PostgreSQL; see ADR 0002.
- [ ] **Redis (native)**: Redis has no official native Windows build. Options: **Memurai Developer** (Redis-compatible Windows service) or Redis inside **WSL2**. The backend falls back to an in-memory rate limiter when `REDIS_URL` is empty, so Redis is optional for early local development.
- [ ] Docker is **not used for local development** (owner decision 2026-10-18). Docker remains the packaging/deployment format (Phase 9) and is built in CI or on the server.
- [ ] **Android Studio** (Android SDK, platform tools, emulator) + **JDK 17**. Then run `flutter doctor --android-licenses`.
- [ ] **uv** (Python package manager): `py -m pip install uv` or the official installer.
- [ ] Disable the Microsoft Store `python` alias, or always use `py`/`uv`.
- [ ] Firebase CLI + FlutterFire CLI (`dart pub global activate flutterfire_cli`) for `flutterfire configure`.
- [ ] Optional: gitleaks, pre-commit.

## 10.3 Backend environment variables (`backend/.env.example`, placeholders only)
```dotenv
# --- App ---
APP_ENV=local                         # local | test | staging | prod
APP_DEBUG=false
APP_BASE_URL=http://localhost:8000
CORS_ALLOWED_ORIGINS=http://localhost:3000
LOG_LEVEL=INFO

# --- Database / cache ---
FIRESTORE_PROJECT_ID=                 # defaults to FIREBASE_PROJECT_ID
FIRESTORE_DATABASE=(default)
FIRESTORE_EMULATOR_HOST=127.0.0.1:8085   # local only; never in staging/prod
DATABASE_POOL_SIZE=10
REDIS_URL=redis://localhost:6379/0

# --- Firebase ---
FIREBASE_PROJECT_ID=your-firebase-project-id
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/firebase-service-account.json   # path, never inline JSON in repo
APP_CHECK_ENFORCE=false              # monitor-only until clients ship tokens

# --- Storage (S3-compatible) ---
STORAGE_ENDPOINT_URL=http://localhost:9000            # R2: https://<account-id>.r2.cloudflarestorage.com
STORAGE_REGION=auto
STORAGE_ACCESS_KEY_ID=CHANGE_ME
STORAGE_SECRET_ACCESS_KEY=CHANGE_ME
STORAGE_BUCKET_PRIVATE=wallora-user-private
STORAGE_BUCKET_PUBLIC=wallora-catalog-public
STORAGE_PUBLIC_BASE_URL=http://localhost:9000/wallora-catalog-public
SIGNED_URL_TTL_SECONDS=600

# --- AI provider ---
IMAGE_PROVIDER=fake                   # fake | openai
OPENAI_API_KEY=                       # set only in staging/prod secret store
OPENAI_ORG_ID=
OPENAI_PROJECT_ID=
OPENAI_TIMEOUT_SECONDS=120
MODERATION_PROVIDER=fake              # fake | openai

# --- Cost controls (defaults; runtime values live in app_settings) ---
GLOBAL_DAILY_COST_CAP_USD=5
GLOBAL_MONTHLY_COST_CAP_USD=50
WORKER_MAX_CONCURRENCY=4

# --- RevenueCat ---
REVENUECAT_SECRET_API_KEY=            # server-side secret key
REVENUECAT_WEBHOOK_AUTH_SECRET=       # value configured in RevenueCat webhook Authorization header
REVENUECAT_PROJECT_ID=

# --- AdMob SSV ---
ADMOB_SSV_KEYS_URL=https://www.gstatic.com/admob/reward/verifier-keys.json
ADMOB_REWARDED_AD_UNIT_IDS=           # comma separated, to validate callbacks

# --- Observability ---
SENTRY_DSN=
METRICS_ENABLED=true
```

## 10.4 Mobile configuration (not secrets, but per-environment; injected via `--dart-define-from-file`)
```json
{ "API_BASE_URL": "http://10.0.2.2:8000",
  "REVENUECAT_PUBLIC_SDK_KEY_ANDROID": "goog_xxx",
  "REVENUECAT_PUBLIC_SDK_KEY_IOS": "appl_xxx",
  "ADMOB_REWARDED_UNIT_ANDROID": "ca-app-pub-3940256099942544/5224354917",
  "ADMOB_REWARDED_UNIT_IOS": "ca-app-pub-3940256099942544/1712485313",
  "ENABLE_FAKE_PROVIDER_BADGE": true }
```
(The AdMob IDs shown are Google's **public test** rewarded units, to be re-verified in Phase 6.) `google-services.json` / `GoogleService-Info.plist` / `firebase_options.dart` are generated by `flutterfire configure` per flavor and **gitignored**. The AdMob *app IDs* go in `AndroidManifest.xml` / `Info.plist` per flavor.

## 10.5 Admin configuration (`admin/.env.example`)
```dotenv
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_FIREBASE_API_KEY=your-web-api-key        # public web config, not a secret
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=your-project.firebaseapp.com
NEXT_PUBLIC_FIREBASE_PROJECT_ID=your-firebase-project-id
NEXT_PUBLIC_FIREBASE_APP_ID=
```

## 10.6 Manual setup tasks by phase
| Phase | Task (you) |
|-------|------------|
| 0 | Approve `git init`. Optionally create an empty remote repo. |
| 1 | Create the Firebase dev project, enable Email/Google/Apple providers, create a service account key and store it outside the repo. Tell me the **project ID** only. |
| 3 | Install Android Studio + JDK 17. Run `flutterfire configure`. Decide the bundle/app ID (e.g. `ai.wallora.app`). |
| 5 | Create the OpenAI project, set a **hard budget**, put the key in local `.env`, and approve the test batch budget. |
| 6 | Create store apps, IAP products (subscriptions + consumables), RevenueCat project/offerings/entitlement `premium`, webhook URL + auth secret, AdMob app + rewarded unit with SSV callback URL, sandbox testers. |
| 8 | Provide a support email, legal entity name and jurisdiction for the privacy policy/terms (legal review strongly recommended). |
| 9 | Pick a hosting provider and approve costs. Configure DNS. Create production Firebase/R2/RevenueCat/AdMob configs. |

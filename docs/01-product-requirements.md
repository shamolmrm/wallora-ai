# 1. Product Requirements and MVP Scope

## 1.1 Vision
Wallora AI is a premium wallpaper app for Android and iOS. Users can discover curated, properly licensed wallpapers or create their own with AI. Revenue comes from subscriptions, credit packs and rewarded ads, and AI spending stays bounded and predictable.

## 1.2 Personas
| Persona | Need | Key features |
|---------|------|--------------|
| Browser | Quickly find a beautiful wallpaper | Discover, categories, search, preview, download |
| Creator | A unique wallpaper made to order | AI Studio, presets, reference images, history |
| Collector | Organise favourites | Favorites, collections, downloads |
| Premium user | Ad-free, more and better generations | Subscription, monthly credits, HD quality |
| Admin / moderator | Curate content, keep platform safe, watch costs | Admin dashboard |

## 1.3 Functional requirements (mapped to brief A–O)

| ID | Requirement | MVP? | Notes |
|----|-------------|------|-------|
| A | Onboarding, Firebase auth (Apple, Google, email/password), guest browsing, profile, preferences, account deletion | MVP | Guests can browse and download free wallpapers. Favorites, collections and generation require a signed-in (non-anonymous) account, which is an anti-abuse measure. Sign in with Apple is mandatory on iOS whenever Google sign-in is offered. |
| B | Discover: featured, trending, latest, curated collections, categories | MVP | Server-driven sections |
| C | Search: full-text, tags, filters (category, aspect, colour, premium), sort, cursor pagination | MVP | PostgreSQL FTS + `pg_trgm` |
| D | Categories (nature, anime, fantasy, cars, space, architecture, minimal, abstract, AMOLED, animals, seasonal, …) | MVP | Admin-managed, never hard-coded |
| E | Wallpaper details: full-screen preview, resolution, aspect, tags, favorite, share, download, add to collection | MVP | Downloads use short-lived signed URLs |
| F | AI Studio: prompt, prompt enhancer, style presets, aspect ratio, resolution, progress, cancel (while queued), retry, history, credit balance | MVP | Cancellation is **only guaranteed while a job is queued**. An in-flight provider call cannot be reliably cancelled, and the UI says so. |
| G | Reference-image workflow (upload validation, privacy consent) | MVP | The OpenAI edits endpoint accepts multiple input images |
| H | AI editing (masked edit), variations | Post-MVP (Phase 5b) | Masks are supported, but the docs say masking is prompt-guided and inexact. Background extension (outpainting via a transparent canvas plus mask) is **experimental** and is labelled that way, never as a guaranteed feature. |
| I | Crop/reposition, phone mock-up preview (home/lock), save to gallery, set wallpaper | MVP | **Android**: sets the wallpaper directly via `WallpaperManager` (home/lock/both). **iOS**: no public API exists, so the app saves to Photos and shows a step-by-step guide. |
| J | Favorites, custom collections, recent downloads, generation history | MVP | |
| K | Credit ledger, daily free allowance, rewarded ads, credit packs, subscription credits | MVP | All credit logic runs on the server |
| L | Premium paywall, purchase, restore, status, billing errors, cancellation guidance | MVP | RevenueCat with server-side verification |
| M | Push notifications with preferences and opt-out | MVP (transactional) / Post-MVP (marketing campaigns) | "Generation ready" ships in the MVP |
| N | Settings: theme, language-ready, privacy, terms, support, delete account | MVP | English first; every string in ARB files |
| O | Native capabilities via plugins or platform channels | MVP | Wallpaper setting (Android), gallery save, share sheet |
| — | Report content (wallpapers and generated images) | MVP | Required by Apple guideline 1.2 and the Google Play AI-generated content policy |

## 1.4 Non-functional requirements
- **Performance**: Discover first contentful paint under 1.5 s on mid-range Android over 4G, with cached thumbnails and blurhash placeholders. API p95 under 300 ms for catalog reads.
- **Availability**: target 99.5% for the API. Generation depends on the provider, so retries are bounded and errors are clear.
- **Security/privacy**: see [07-threat-model.md](07-threat-model.md). Prompts and uploads are private by default.
- **Accessibility**: WCAG AA contrast, semantic labels, dynamic text scaling up to 200% without clipping, 48dp touch targets, reduced-motion support.
- **Localization**: all user-visible strings in ARB (mobile) or message catalogs (admin). RTL-safe layouts.
- **Cost**: hard global and per-user AI spend caps that the server enforces.

## 1.5 MVP scope summary
**In**: everything marked MVP above, plus the admin dashboard (content, categories/tags, collections, reports/moderation, users (limited), credit adjustments, config, jobs, cost/usage metrics, audit logs).

**Out of the MVP (post-MVP backlog)**: masked editing UI, outpainting, live/animated wallpapers, public sharing/community feed, social login beyond Apple/Google/email, marketing push campaigns, more AI providers, web app for end users, LLM-based prompt enhancement (the MVP uses a deterministic template enhancer at zero cost), and streaming partial previews (`partial_images`).

## 1.6 Explicit non-goals
- Unlimited generation in any plan.
- Scraping or redistributing copyrighted wallpapers.
- Storing payment card data (the stores handle payments).
- Client-side AI keys or client-trusted credit/entitlement state.

## 1.7 Global acceptance criteria (apply to every feature)
1. Loading, empty, error (with retry), offline and permission-denied states exist and are covered by widget tests.
2. All strings are localizable. Semantics labels exist on interactive and image elements.
3. Every protected endpoint has an unauthorized test (401) and a forbidden or other-user test (403/404).
4. No secrets in the repository or logs. The secret-scanning check passes.
5. Formatters, linters and tests pass in CI. Results are reported honestly in [TASKS.md](TASKS.md).

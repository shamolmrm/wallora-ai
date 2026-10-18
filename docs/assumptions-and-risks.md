# Assumptions, Risks and Open Decisions

## Assumptions
1. Solo or small team. The MVP launches English-only with i18n-ready code.
2. Guests may browse and download free wallpapers. Signing in is required for favorites, collections, generation and purchases.
3. Generation is asynchronous (job + polling + push), not a blocking HTTP call.
4. One AI provider (OpenAI) at launch, behind an abstraction.
5. The Postgres-backed queue is enough for launch scale (it can move to a dedicated broker if sustained throughput exceeds ~50 jobs/s).
6. Credit costs, allowances, caps, product IDs and prices are runtime configuration, not code.
7. UTC day boundaries for daily allowances (simpler and abuse-resistant; documented in the app copy).
8. Catalog content will be original (in-house AI or commissioned) or properly licensed. Admins upload it.
9. A Mac or cloud macOS CI will be available before iOS testing.

## Risks
| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| AI cost overrun (abuse, mispriced tiers) | High | Medium | Layered caps, measured costs, kill switch, OpenAI budget limit |
| Model/API churn (new image models appear often) | Medium | High | Model names in config, capability reporting, docs re-check each phase |
| Unclear per-image cost (token-based pricing) | Medium | High | Measure with a small approved batch, use rolling actual-usage estimates |
| Catalog sourcing and copyright | High | Medium | No scraping. Licence fields per wallpaper. DMCA/report flow. Unsplash/Pexels API terms likely **disallow** replicating a wallpaper-app experience with their content, so they're not used unless their terms are reviewed and confirmed. |
| Store rejection (IAP rules, UGC moderation, account deletion, AI content policies) | High | Medium | Policy checklist (§8.6), reporting, in-app deletion, sign in with Apple |
| Harmful/illegal generated content | High | Medium | Layered moderation, strikes, reports, legal review |
| iOS cannot set wallpapers programmatically | Low | Certain | Save to Photos + clear guide. No false claims. |
| No Docker/Android SDK/macOS locally | Medium | Certain | Manual setup tasks. Status reported as blocked until resolved. |
| Python 3.14 wheel availability | Low | Low–Medium | Docker targets 3.13. Verified in Phase 0. |
| Third-party pricing/terms changes (RevenueCat, AdMob, R2) | Medium | Medium | Re-verify official docs at the start of each relevant phase |
| Privacy law obligations (GDPR, CCPA, children) | High | Medium | Age rating 13+ (not designed for children), consent flows, retention policy. Legal review before launch. |

## Open decisions (need your answer before or during Phase 0)
1. **Approve this plan** (or list changes).
2. **Git**: may I run `git init` here? Is there a remote repo?
3. ~~**Local services**~~ → **Decided 2026-10-18: native PostgreSQL + Redis** (no Docker locally).
4. **App identifiers**: bundle/application ID (e.g. `ai.wallora.app`) and organisation name.
5. **Guest policy**: confirm generation requires a non-anonymous account.
6. **Default tier models**: Draft = `gpt-image-1-mini`, Standard/HD = `gpt-image-2.5-flare`, Edit = `gpt-image-2.5-sunburst`. OK?
7. **Pricing defaults** in [08](08-monetization-and-cost-control.md) (pack size, subscription price, monthly credits, welcome/daily credits). OK as starting placeholders?
8. **Catalog content source**: in-house AI generation (costs money, needs approval), commissioned artists, or your own artwork?
9. **Hosting preference** for staging/prod (decide by Phase 9).
10. **Admin MFA**: OK to require MFA for all admin accounts? (Recommended.)

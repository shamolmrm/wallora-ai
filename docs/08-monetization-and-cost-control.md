# 8. Monetization and Cost-Control Design

> All numbers below are **illustrative defaults for discussion**. Every one is a server-side config value (`app_settings`), editable in the admin dashboard and audit-logged. Clients render whatever `/v1/config` returns.

## 8.1 Revenue streams
| Stream | Mechanism | Notes |
|--------|-----------|-------|
| Premium subscription (monthly/yearly) | RevenueCat entitlement `premium` → App Store / Play Billing | Ad-free, premium wallpapers, HD tier, premium presets, **monthly credit allotment** (not unlimited), higher daily caps |
| Credit packs | Consumable IAP via RevenueCat (required by store rules for digital goods) | Non-expiring credits |
| Rewarded ads | AdMob rewarded, **server-side verified** | Free users only, user-initiated, daily cap |

## 8.2 Generation tiers (config)
| Tier | Default model (config) | Quality | Default size | Credits | Who |
|------|------------------------|---------|--------------|---------|-----|
| Draft | `gpt-image-1-mini` | low | 1024×1536 | 1 | Everyone |
| Standard | `gpt-image-2.5-flare` | medium | 1072×2320 | 3 | Everyone |
| HD | `gpt-image-2.5-flare` | high | 1072×2320 (experimental 1440×3120 behind a flag) | 8 | Premium only |
| Edit / variation | `gpt-image-2.5-sunburst` | medium | source size | 4 | Post-MVP |

Credit costs are **derived** from measured provider cost so that every tier keeps a target gross margin (§8.4). They're recalculated by an admin report from real `actual_cost_usd_micros` data.

## 8.3 Cost estimation
- **Before the call (estimate)**: `estimate = text_tokens(prompt) × text_rate + Σ image_input_tokens × image_in_rate + expected_output_tokens(model, quality, size) × image_out_rate`. `expected_output_tokens` comes from a config table seeded with values measured in the Phase 5 test batch (and the official calculator), then **auto-updated as a rolling p90 of real usage per (model, quality, size)**.
- **After the call (actual)**: computed from the response `usage` fields using the rate table in config (`pricing.openai.<model>.{text_in, image_in, image_out}` in micro-USD per 1M tokens). If usage is missing, the estimate is recorded and flagged.
- Rates come from the official pricing page as of the last review, with a `rates_reviewed_at` date shown in the admin dashboard, which **warns if older than 30 days**.

## 8.4 Illustrative unit economics (to validate with real usage)
Assumptions: pack of 100 credits at $4.99. After store commission (15% small-business programme to 30%) and tax, net is ≈ $0.033–0.040 per credit.

| Tier | Credits | Net revenue (@ $0.035) | Provider cost | Target |
|------|---------|------------------------|---------------|--------|
| Draft | 1 | $0.035 | **unknown until measured** (mini model, low quality, expected to be cents or less) | ≥ 50% margin |
| Standard | 3 | $0.105 | **unknown until measured** | ≥ 50% margin |
| HD | 8 | $0.28 | **unknown until measured** | ≥ 40% margin |

If measured cost breaks a target, the admin report flags the tier and an admin raises its credit cost. Clients pick this up from `/v1/config` with no app release.

**Free usage budget**: welcome 3 + daily 1 Draft per user. Worst-case free cost = `DAU_free × daily_cost(Draft)`, bounded by the global daily cap below. Rewarded ad = 1 credit, which only makes sense if eCPM per completed view ≥ Draft cost. The admin metrics page compares ad revenue (entered manually or via the AdMob API post-MVP) with reward-funded spend.

**Subscription** (example): $6.99/month or $39.99/year, with 150 credits/month. Worst case, all 150 used on Standard = 50 generations. That must stay well under net subscription revenue; verify after measuring.

## 8.5 Spend controls (defence in depth)
| Layer | Control | Default |
|-------|---------|---------|
| 1 | Credits (reserved before the provider call) | — |
| 2 | Per-user rate limit on `POST /generations` | 6/min, 60/hour |
| 3 | Per-user daily generation cap | free 10/day, premium 60/day |
| 4 | Per-user daily **cost** cap (`spend_counters`) | free $0.25, premium $3.00 |
| 5 | Per-IP rate limit (shared across accounts) | 20/hour |
| 6 | **Global daily cost cap** → `503 generation_paused` | staging $5, prod launch $50 (admin adjustable, super_admin only) |
| 7 | Global monthly cost cap | prod $1,000 |
| 8 | Kill switch (`studio.enabled=false`) | — |
| 9 | Max concurrent provider calls per worker / overall | 4 / 16 |
| 10 | OpenAI dashboard project budget + usage alerts (manual setup) | slightly above layer 7 |
| 11 | Alerts at 50/80/100% of global caps (log + email/webhook) | — |

Caps are checked **before** reservation (estimate) and updated **after** completion (actual). The reconciliation job compares our recorded spend with OpenAI's usage dashboard numbers (manual check, or the Usage API if available and approved).

## 8.6 Store-policy notes (re-verify before submission)
- Digital credits and subscriptions must use the platform's IAP (Apple guideline 3.1.1, Google Play Payments policy). No external payment links unless a jurisdiction-specific programme is deliberately adopted.
- Subscription paywalls must show price, period, renewal terms and links to the privacy policy and terms (Apple 3.1.2). Restore purchases must be visible.
- Rewarded ads must be optional and clearly labelled. iOS needs the ATT prompt before personalised ads. Google's UMP consent applies in the EEA/UK.
- AI-generated content (Google Play AI-Generated Content policy; Apple 1.2 UGC): in-app reporting, moderation, and blocking of objectionable content. All covered by `moderation_reports` + filters.
- Account deletion must be possible in-app (Apple 5.1.1(v), Google Play account deletion requirement + web deletion URL).
- Never advertise "unlimited".

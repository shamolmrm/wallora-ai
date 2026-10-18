# Wallora AI — Planning Documents

Status: plan approved. **Phase 0 (repository foundation) implemented** on 2026-10-18 and awaiting review. See [TASKS.md](TASKS.md).

| # | Document | Contents |
|---|----------|----------|
| 1 | [01-product-requirements.md](01-product-requirements.md) | Product requirements, MVP scope, non-goals |
| 2 | [02-architecture.md](02-architecture.md) | System architecture, tech choices, repository structure |
| 3 | [03-screens-and-navigation.md](03-screens-and-navigation.md) | Screen inventory, navigation flow, UI states |
| 4 | [04-database.md](04-database.md) | Database design, ERD, indexes, retention |
| 5 | [05-api-spec.md](05-api-spec.md) | REST API endpoint specification |
| 6 | [06-generation-and-credits.md](06-generation-and-credits.md) | AI generation job lifecycle and credit ledger lifecycle |
| 7 | [07-threat-model.md](07-threat-model.md) | Security threat model (STRIDE) and mitigations |
| 8 | [08-monetization-and-cost-control.md](08-monetization-and-cost-control.md) | Pricing model, credit economics, AI spend controls |
| 9 | [09-implementation-plan.md](09-implementation-plan.md) | Phased plan with deliverables and acceptance criteria |
| 10 | [10-external-setup.md](10-external-setup.md) | External accounts, environment variables, manual setup |
| — | [TASKS.md](TASKS.md) | Central task checklist (honest status tracking) |
| — | [assumptions-and-risks.md](assumptions-and-risks.md) | Assumptions, risks, open decisions for approval |

## Workspace inspection (2026-10-18)

- `D:\devloper\app\Wallora AI` was **empty** and is **not a Git repository**.
- Toolchain detected on this Windows 11 machine:

| Tool | Status |
|------|--------|
| Flutter 3.44.1 (stable) / Dart 3.12.1 | Installed |
| Python 3.14.6 (via `py` launcher; `python` alias is the MS Store stub) | Installed |
| Node.js 26.3.0 / npm 11.16.0 | Installed |
| Git 2.54.0 | Installed |
| Docker | **Not installed** |
| PostgreSQL (`psql`) | **Not installed** |
| Java JDK / Android SDK (`ANDROID_HOME`) | **Not found** — Android builds blocked until installed |
| macOS / Xcode | **Not available** — iOS builds require a Mac or a cloud macOS CI runner |

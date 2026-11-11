# AGENTS.md — Muzqaymoq Procurement & Warehouse ERP

Read this file fully before every task. These rules apply to every change in this repository.

## 1. Project summary
Procurement & Warehouse ERP for "Muzqaymoq", an ice cream factory in Uzbekistan.
Primary user: Procurement (Ta'minot) specialist, often working from a phone.
Goal: production never stops for lack of materials, and every som spent is traceable.

Modules: Master data, Inventory (ledger + batches + FEFO), Procurement (request -> approval -> RFQ -> PO -> receipt -> invoice -> payment), BOM/MRP, Payables, Telegram notifications/approvals, Dashboard & Reports, Audit/Security.

## 2. Tech stack (do not deviate without an ADR)
- Python 3.12, Django 5, Django REST Framework, PostgreSQL 16
- Templates + HTMX + Tailwind, mobile-first PWA
- Celery + Redis, aiogram 3 (Telegram bot)
- Docker Compose, pytest + pytest-django + factory_boy, ruff, mypy (django-stubs), pre-commit, GitHub Actions
- Audit history: django-simple-history; Excel: openpyxl; PDF: WeasyPrint

## 3. Commands (run and report results; never claim success untested)
```
docker compose up -d            # start services
make test                       # pytest with coverage
make lint                       # ruff check + ruff format --check
make typecheck                  # mypy
python manage.py makemigrations --check --dry-run
python manage.py migrate
python manage.py seed_demo      # realistic Uzbek demo data
python manage.py reconcile_stock  # ledger vs balance check
```
If a command does not exist yet, create it as part of the current phase.

## 4. Non-negotiable rules
1. **Decimal only** for money and quantities. Money: 2 dp (UZS). Quantity: 3 dp. Rounding: ROUND_HALF_UP. Never use float.
2. **Time**: store UTC, display Asia/Tashkent.
3. **Inventory is a ledger**: StockMovement is append-only. Correct mistakes with reversing movements. Never edit or delete movements or posted documents.
4. **Transactions + locking**: multi-row business operations run in `transaction.atomic()` with `select_for_update()`. Stock must never go negative (service check AND DB constraint).
5. **Idempotency**: any retryable action (Telegram callbacks, double-clicks, task retries) must be safe to repeat.
6. **Services layer**: business logic lives in `services/`. Views, serializers and bot handlers stay thin.
7. **Least privilege**: enforce permissions server-side on every view, API endpoint and bot action. Hiding UI elements is not security.
8. **No secrets in git**. Use environment variables via a validated settings module. Keep `.env.example` current.
9. **Soft delete** master data; hard delete of documents is forbidden.
10. **VAT and other tax rates are configurable settings**, never hardcoded in logic.

## 5. Domain rules
- Issue stock by **FEFO**; only batches with status `approved` and not expired are issuable. Override requires permission and a reason.
- Items may only be stored in locations compatible with their storage condition (dry / chilled / frozen).
- Costing: moving weighted average (per-movement unit cost stored).
- Three-way match (PO vs Receipt vs Invoice) with tolerance; mismatches block payment until resolved.
- Requester cannot approve their own purchase request. Approval rules depend on amount and category.
- Handle partial deliveries, over-delivery tolerance, returns, cancellations and PO versioning.
- MRP: net requirement = plan x BOM (with scrap) - usable on-hand - open PO quantity arriving in time; respect lead time and min order quantity.
- Multi-currency: store exchange rate and its date on each document.

## 6. Code standards
- Type hints on all new code; mypy must pass.
- ruff clean; no unused code, no commented-out blocks, no stray prints.
- Small functions, explicit names, docstrings for services describing business rules and edge cases.
- Database: constraints (CHECK/UNIQUE), FK with `PROTECT`, indexes on foreign keys and common filters, reversible migrations.
- No N+1 queries: use `select_related` / `prefetch_related`; add query-count tests for key pages.
- User-facing text goes through i18n (Uzbek Latin default; keep Russian/English ready). No hardcoded UI strings.
- Format: numbers `1 234 567,00`, dates `DD.MM.YYYY`.
- Pin dependency versions; avoid deprecated or unmaintained libraries.

## 7. Security checklist (apply to every feature)
- Validate and sanitize all input; validate uploads (type, size, content) and keep them outside the web root.
- CSRF, XSS, SQL injection protections intact; secure headers; HTTPS-only cookies in production.
- Rate-limit login and bot linking; Telegram linking uses one-time tokens.
- Log access to sensitive data; audit every business change (who, when, what, before/after).
- Never log secrets, tokens or personal data.

## 8. Testing requirements
- Every business rule gets a test. Required coverage: FEFO ordering, expiry exclusion, negative-stock prevention, UoM rounding, partial receipts, over-delivery tolerance, three-way match, approval thresholds, self-approval block, MRP net requirement, aging buckets, multi-currency allocation, Telegram idempotency, audit entry creation.
- Permission tests for each role x critical action (matrix in `docs/roles.md`).
- Concurrency test: two simultaneous issues of the last unit -> exactly one succeeds.
- Migration test: apply from scratch and roll back.
- CI gates: ruff, mypy, pytest, `makemigrations --check`. Coverage >= 85% for `services/`, >= 70% overall.

## 9. Workflow
1. Before coding a phase: state assumptions, risks, and the plan. Update `docs/erd.md` if models change.
2. Work in small, focused commits with clear messages. Do not rewrite history.
3. Ambiguity: choose the safest conventional behavior, record it in `docs/decisions/ADR-XXXX.md` (context, decision, consequences), and continue. Ask the user only if truly blocked.
4. Conflicting requirements: list the conflict, pick the safest option, write an ADR.
5. Do not add features outside the spec; put ideas in `docs/ideas.md`.
6. Never leave placeholders, fake logic or silent TODOs. List genuine gaps in `docs/known-gaps.md`.

## 10. Definition of Done (per phase)
- [ ] All features implemented end to end: model, service, UI, permissions, audit, tests.
- [ ] Tests, lint, type checks and CI green; coverage gates met.
- [ ] Migrations work from scratch and from the previous phase.
- [ ] README and docs updated; ADRs recorded.
- [ ] Self-review against the spec completed; unmet items listed.
- [ ] Phase report written: what was built, exact verification commands, risks, next step.

## 11. Phases
0. Scaffold, Docker, settings validation, CI, auth, roles, i18n, health check
1. Master data + inventory ledger + FEFO + stocktake + alerts
2. Procurement flow, approvals, attachments, three-way match
3. Payables, supplier ledger, aging, akt sverka export
4. BOM + MRP + seasonal forecast suggestion
5. Telegram bot, Celery notifications, preferences
6. Dashboard, reports, async exports
7. Hardening: OWASP review, performance, backup/restore test, demo data, docs

Finish and verify each phase before starting the next.

## 12. Repository layout
```
apps/        Django apps (one per module), each with models/, services/, views/, tests/
bot/         Telegram bot
config/      settings, celery, urls
docs/        erd.md, roles.md, decisions/, known-gaps.md, ideas.md
scripts/     backup/restore, maintenance
```

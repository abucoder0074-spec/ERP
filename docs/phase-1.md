# Phase 1 report

Verified on 11.11.2026 with Python 3.12.16, PostgreSQL 16.15 and Redis 8.10.3. The Windows host ran the Django checks against disposable PostgreSQL and Redis instances. Compose verification used an isolated Alpine Linux guest with Docker Engine 29.9.3 and Compose 5.6.1.

## Delivered

- Seven audited master-data catalogs with search, status filters, pagination and archive/reactivate actions. Item and supplier imports accept UTF-8 CSV or single-sheet XLSX. Opening balances post through the receipt service.
- An append-only stock ledger with PostgreSQL guards, per-lot physical balances and per-item/warehouse valuation. Receipts, FEFO issues, transfers, write-offs, supplier returns, reversals and stocktake adjustments use locked transactions and retry-safe posting keys.
- Batch quarantine and quality decisions, two-person stocktakes with variance and stale-snapshot protection, and deduplicated stock and expiry alerts shown on the dashboard. Celery beat is configured to refresh conditions every five minutes.
- Uzbek mobile workflows and native barcode/QR scanning where supported, with manual entry available on all browsers. The [role matrix](roles.md) documents the seven roles and server-side permissions.

## Native verification

| Command | Observed result |
| --- | --- |
| `python manage.py migrate` against the Phase 0 database and a new empty database | Applied `masterdata.0001_initial` and `inventory.0001_initial` through `0003_role_grants`, each with `OK`. |
| `python manage.py migrate inventory zero`, `python manage.py migrate masterdata zero`, then `python manage.py migrate` on the empty verification database | Unapplied all Phase 1 migrations, then reapplied all four with `OK`. The test suite also verifies role grants and PostgreSQL guards after reapplication. |
| `python manage.py makemigrations --check --dry-run` | `No changes detected`. |
| `make lint` | `All checks passed!` and `75 files already formatted`. |
| `make typecheck` | `Success: no issues found in 75 source files`. |
| `make test` | `78 passed in 28.63s`. Overall branch-aware coverage was **90.45%** and services coverage was **92%**. Both gates passed. |
| `python manage.py seed_demo` twice | `Phase 1 demo ready: 4 items, 3 suppliers, 5 batches, 3 new alerts.` The retry reported `0 new alerts`. Tests confirmed the five original receipt movements were retained without duplication. |
| `python manage.py reconcile_stock` | `Stock balances and carrying values reconcile.` With a one-unit discrepancy injected into the verification database, it reported `ledger=48.000 balance=49.000` and exited **1**. After restoring the original balance it exited **0**. |
| `make translations`, `npm run build`, `node --check static/workspace.js` | Uzbek catalog, Tailwind CSS, HTMX and scanner JavaScript passed. Chrome at 390px simulated a barcode scan, populated the field, stopped the stream and accepted manual input. |

The tests exercised FEFO splitting and overrides, expiry and quarantine exclusion, exact conversion, weighted average, negative-stock prevention and database immutability. They also checked posting retries, reversal, import dry-run and commit rollback, the import-to-stocktake HTTP workflow, all seven roles, and two simultaneous PostgreSQL issues of the last unit. One issue succeeded and one was rejected. Balance and movement pages used seven and six queries respectively with both one and four lots.

## Compose verification

The guest ran `docker compose config --quiet`, `docker compose build web`, `docker compose up -d`, `docker compose up -d --wait --wait-timeout 180`, and `docker compose ps` with fresh PostgreSQL and Redis volumes.

```text
docker compose ps (service and status columns)
beat      Up About a minute (healthy)
bot       Up About a minute (healthy)
db        Up About a minute (healthy)
redis     Up About a minute (healthy)
web       Up About a minute (healthy)
worker    Up About a minute (healthy)

curl -fsS -w ' HTTP %{http_code}\n' http://127.0.0.1:8000/health/
{"status": "ok"} HTTP 200
```

Web startup logged all four Phase 1 migrations as `OK`. The following commands were also run in the final stack.

| Command | Observed result |
| --- | --- |
| `docker compose exec -T web python manage.py migrate` | `No migrations to apply.` |
| `docker compose exec -T web python manage.py makemigrations --check --dry-run` | `No changes detected`. |
| `docker compose exec -T web python manage.py seed_demo` | `Phase 1 demo ready: 4 items, 3 suppliers, 5 batches, 3 new alerts.` |
| `docker compose exec -T web python manage.py reconcile_stock` | `Stock balances and carrying values reconcile.` |
| `docker compose exec -T web celery -A config.celery call apps.inventory.tasks.refresh_stock_alerts` | The worker received the queued UUID and logged `succeeded` with result `0`, confirming there were no duplicate alerts after seeding. This check dispatched the task directly through the broker. |

## Decisions and operational risks

- Valuation pools stock by item and warehouse. The posting locks serialize concurrent work on the same item. Reconciliation takes shared table locks to read a stable ledger and its materializations. Reversals can be refused if later movements would leave negative stock or unsupported carrying value. [ADR 0004](decisions/ADR-0004.md)
- Import previews reflect the data at validation time. Commit revalidates within the posting transaction. The warehouse keeper write-off threshold is configurable, and stocktake approval and posting require a separate account from the counter. [ADR 0004](decisions/ADR-0004.md), [ADR 0005](decisions/ADR-0005.md)
- Camera capture requires HTTPS or localhost and a compatible browser. The scan workflow passed with a simulated stream. Physical phone camera capture remains unverified. [ADR 0005](decisions/ADR-0005.md)
- Database roles with DDL privileges can change guards. Limit database-owner access and run rollbacks against a verified backup or disposable database.

## Definition of Done self-review

| AGENTS.md criterion | Result |
| --- | --- |
| In-scope model, service, UI, permission, audit and tests | Implemented. The import, quality approval, count, approval and adjustment were exercised through the HTTP views. Physical phone capture remains unverified. |
| Tests, lint, type checks, CI and coverage gates | Local gates passed. Hosted CI remains pending because this checkout has no Git remote. |
| Migration from scratch and from Phase 0 | Passed on PostgreSQL 16, including rollback and reapplication. |
| README, ERD, role matrix and ADRs | Updated and committed. |
| Self-review and unmet items | Hosted CI, physical camera verification and scheduled work are listed in [known gaps](known-gaps.md). |
| Phase report and next step | Hold Phase 1 at the hosted CI gate and wait for confirmation before Phase 2. |

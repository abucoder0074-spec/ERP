# Phase 2 verification and handoff

Verified on 11.11.2026 at implementation commit `9e615f1`. Native checks used Python 3.12.16, PostgreSQL 16.15 and Redis 8.10.3. Local Compose ran in an isolated Linux guest with Docker Engine 29.9.3 and Compose 5.6.1.

## Delivered

- Purchase requests from manual entry or low-stock alerts. Configurable category and amount rules snapshot ordered approval levels at submission. Decisions enforce requester separation and a different approver per level, including dated delegation. Rejections require a reason. Decisions and retries use the same service entry point available to a future Telegram integration.
- RFQ invitations, dated multi-currency quotes, comparison by converted cost, lead time and supplier performance, and a recorded reason for selecting above the cheapest eligible total. Supplier-item price history supports selection by currency and validity date.
- Purchase orders from approved requests or manual entry, with approval, VAT, FX, delivery dates, immutable versions, readable diffs and reasoned cancellation. Activating an amendment rechecks receipts posted while the amendment was pending.
- Partial and unplanned receiving under explicit permissions and tolerances, temperature-aware batch quality, and defective supplier returns with a photo and reason. Receipts and returns post through the Phase 1 stock ledger.
- Supplier invoices with unique supplier-number pairs, receipt line reservations, a three-way match, a reasoned and audited exception path, and a `ready_invoices()` service for Phase 3.
- Expected and actual delivery tracking, supplier performance, scheduled overdue alerts, private validated evidence downloads and mobile-first Uzbek screens with scanner and manual entry. The [role matrix](roles.md) records the seven roles and server-side permissions.

## Verification

| Command | Observed result |
| --- | --- |
| `python manage.py migrate` against the Phase 1 database | Applied all four procurement migrations. |
| `python manage.py migrate` on empty PostgreSQL | Applied the full graph, including `procurement.0001_initial` through `0004_alter_purchaseorder_options_supplierinvoice_tax_and_more`, each with `OK`. |
| `python manage.py migrate procurement zero` and `python manage.py migrate` on the verification database | Unapplied and reapplied all four procurement migrations, each with `OK`. The suite also verifies the grants after the migration round trip. |
| `python manage.py makemigrations --check --dry-run` | `No changes detected`. |
| `make lint` | `All checks passed!` and `103 files already formatted`. |
| `make typecheck` | `Success: no issues found in 103 source files`. |
| `make test` | `101 passed in 67.50s (0:01:07)`. Overall coverage **89.30%** and services coverage **91%**. Required gates are 70% and 85%, respectively. |
| `make translations`, `npm.cmd run build`, `node --check static/workspace.js` | Uzbek catalog, Tailwind assets and scanner JavaScript verified. |
| `python manage.py seed_demo` twice, then `python manage.py reconcile_stock` | See command output below. |

```text
Phase 1 demo ready: 4 items, 3 suppliers, 5 batches, 4 new alerts.
Phase 2 demo ready: request, approval, RFQ, PO, partial receipt, invoice DEMO-HF-001 (mismatch).
Phase 1 demo ready: 4 items, 3 suppliers, 5 batches, 0 new alerts.
Phase 2 demo ready: request, approval, RFQ, PO, partial receipt, invoice DEMO-HF-001 (mismatch).
Stock balances and carrying values reconcile.
```

The tests enumerate allowed and forbidden document transitions and exercise multi-level approval, delegation, retries, valid price selection, PO version changes, quantity and price tolerances, batch quality, duplicate invoice prevention, readiness and audit. In PostgreSQL, two simultaneous receipts of six units competed against an order for ten units with 5% over-delivery tolerance. One receipt succeeded and one was rejected. Critical HTTP action checks cover all seven roles. Query counts stayed constant as rows were added to all five document lists, the approval inbox and delivery tracking. The order list issued six queries.

### Compose and hosted CI

The local Linux guest ran `docker compose config --quiet`, `docker compose build web`, `docker compose up -d`, `docker compose up -d --wait --wait-timeout 180` and `docker compose ps`. A parallel scratch stack delayed web startup beyond the healthcheck window. After stopping that stack, the retry showed all six services healthy. `curl -fsS -w ' HTTP %{http_code}\n' http://127.0.0.1:8000/health/` returned `{"status": "ok"} HTTP 200`. The container runs of `migrate` and `makemigrations --check --dry-run` printed `No migrations to apply.` and `No changes detected`.

Phase 1 was checked before starting Phase 2. The `checks` and `compose` jobs passed on `origin/main` at `33a98b0`. [Phase 1 GitHub Actions run](https://github.com/abucoder0074-spec/ERP/actions/runs/43014611021)

The `checks` and `compose` jobs passed at `9e615f1` on the `phase-2-procurement` verification branch. Hosted pytest reported `101 passed in 50.01s` and **89.30%** overall coverage. Compose verified HTTP 503 during separate PostgreSQL and Redis outages and recovery after each restart. Its `docker compose exec -T web python manage.py seed_demo` and `docker compose exec -T web python manage.py reconcile_stock` steps reproduced the sample mismatch and successful reconciliation. [Phase 2 GitHub Actions run](https://github.com/abucoder0074-spec/ERP/actions/runs/43019418880)

```text
docker compose ps (hosted service and status columns)
beat      Up 20 seconds (healthy)
bot       Up 20 seconds (healthy)
db        Up 26 seconds (healthy)
redis     Up 26 seconds (healthy)
web       Up 20 seconds (healthy)
worker    Up 20 seconds (healthy)
```

## Decisions and risks

- Estimate request value in UZS and approve POs against their VAT-inclusive UZS total. Configure approval coverage before opening the workflow to users. Amendments retain the terms of net received quantities and require separate approval. [ADR 0006](decisions/ADR-0006.md)
- Match each invoice to specific receipt lines and retain their reservations until cancellation. Splitting billing within a single receipt line requires a documented match exception or cancellation and reissue. The upload service validates file formats. The default malware scan hook is a no-op. Set `PROCUREMENT_SCAN_HOOK` to a production scanner before accepting untrusted files. Database backups include attachment bytes. [ADR 0007](decisions/ADR-0007.md)
- Physical phone camera capture remains unverified. Manual item entry and photo upload are available. RFQ dispatch is manual. Payment execution and the supplier ledger are scheduled for Phase 3. [Known gaps](known-gaps.md)

## Definition of Done self-review

| AGENTS.md criterion | Result |
| --- | --- |
| End-to-end models, services, UI, permissions, audit and tests | Implemented through invoice readiness. The demo exercises the full flow and the HTTP suite covers creation, submission, receiving, photo upload, invoice entry, role access and approval configuration. |
| Tests, lint, type checks and CI green with coverage gates met | Passed locally and on GitHub Actions at `9e615f1`. |
| Migrations work from scratch and from the prior phase | Passed against PostgreSQL 16, including rollback and reapplication. |
| README, ERD, roles and ADRs updated | Updated [README](../README.md), [ERD](erd.md), [role matrix](roles.md) and ADRs 0006–0007. |
| Self-review and unmet items recorded | Physical camera verification and production malware-scanner deployment remain open. [Known gaps](known-gaps.md) |
| Phase report and next step | Phase 2 is verified. Phase 3 requires user confirmation. |

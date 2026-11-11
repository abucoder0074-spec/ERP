# Phase 0 report

Verified on 11.11.2026 with Python 3.12.16, PostgreSQL 16.15 and Redis 8.10.3. The Windows host had no Docker daemon, so the Compose checks used Docker Engine 29.9.3 and Compose 5.6.1 inside an isolated Alpine Linux guest.

## Delivered

- Atomic Redis login limits by client IP and case-folded username. The throttle checks both keys before form construction or authentication. Failure increments both counters, a successful credential check clears both, and expiry is automatic. A blocked request returns a generic form-free 429 and Retry-After. Cache failure and concurrent requests that cannot obtain the lease receive a generic 503.
- Mobile-first Uzbek workspace with login, logout confirmation, an empty dashboard, error pages, a language selector, local Tailwind and HTMX, and a PWA public offline page.
- Seven role groups created by migration. The dashboard checks workspace permission server-side and leaves roleless accounts able to sign out.
- Six non-root Compose services, production environment validation, generated local secrets, request IDs and redacted JSON request logs, PostgreSQL and Redis readiness, and GitHub Actions checks.
- Bootstrap command contracts, setup instructions, [ERD](erd.md), [role matrix](roles.md), and [known gaps](known-gaps.md).

## Verification

| Command | Observed result |
| --- | --- |
| `python manage.py migrate` on empty PostgreSQL 16 | All Django migrations and `core.0001_initial`, `0002_workspace_roles`, `0003_workspace_permission` applied with `OK`. |
| `python manage.py migrate core 0002` followed by `python manage.py migrate` | `Unapplying core.0003_workspace_permission... OK` then `Applying core.0003_workspace_permission... OK`. The migration test also rolls the core app back to zero and checks all seven grants on reapplication. |
| `python manage.py makemigrations --check --dry-run` | `No changes detected`. |
| `python manage.py check` | `System check identified no issues (0 silenced).` |
| `make lint` | `All checks passed!` and `39 files already formatted`. |
| `make typecheck` | `Success: no issues found in 39 source files`. |
| `make test` | `47 passed`, 86.99% overall branch-aware coverage and 100% services coverage. All seven roles, roleless and anonymous accounts, CSRF, and login limiter behavior were tested. |
| `npm run build` and `make translations` | Tailwind v4.3.5 built, HTMX copied locally, and the Uzbek Django catalog compiled. Chrome at 390px confirmed the Uzbek sign-in page, a 44px submit control, local CSS and HTMX, and the public-only offline fallback. |
| `python manage.py seed_demo` | Exit 1 with a message listing the required business modules. The Phase 0 command left the database untouched. |
| `python manage.py reconcile_stock` | Exit 1 with a message that the Phase 1 inventory ledger is required. |

The Windows CLI returned a missing Docker engine named pipe. The Linux guest ran `docker compose config --quiet`, `docker compose build web`, `docker compose up -d`, `docker compose up -d --wait --wait-timeout 180`, and `docker compose ps`. The initial guest run exposed health-probe startup timeouts. I removed Django startup from the lightweight probes and adjusted their time budgets while retaining the web readiness, Redis and Celery checks. The final run used fresh PostgreSQL and Redis volumes.

```text
docker compose ps (service and status columns)
beat      Up About a minute (healthy)
bot       Up About a minute (healthy)
db        Up 15 seconds (healthy)
redis     Up 8 seconds (healthy)
web       Up About a minute (healthy)
worker    Up About a minute (healthy)

docker compose exec -T <service> id -u
web uid=10001
worker uid=10001
beat uid=10001
bot uid=10001
db uid=999
redis uid=999
```

The guest probe ran `curl -sS -o /tmp/erp-health.json -w '%{http_code}' http://127.0.0.1:8000/health/` and printed the HTTP status and body. Each dependency was stopped and started separately with `docker compose stop <service>` and `docker compose start <service>`.

```text
HTTP 200 {"status": "ok"}
HTTP 503 {"status": "unavailable"}
HTTP 200 {"status": "ok"}
HTTP 503 {"status": "unavailable"}
HTTP 200 {"status": "ok"}
```

The first 503 followed the database stop. The second followed the Redis stop. The final `docker compose ps` showed all six services healthy after recovery.

## Decisions and risks

- [ADR 0001](decisions/ADR-0001.md) records the two Redis limits and how concurrent sign-in requests are handled. A successful login clears the shared IP counter, and losing Redis state clears active lockouts. A proxy needs an explicit allowlist entry and must overwrite the forwarded headers.
- [ADR 0002](decisions/ADR-0002.md) records the public-only offline cache and the explicit Uzbek default. Business read views need separate offline access rules when introduced.
- [ADR 0003](decisions/ADR-0003.md) records the idle bot service and the explicit dependency errors from the demo and stock reconciliation commands.

## Definition of Done self-review

| AGENTS.md criterion | Result |
| --- | --- |
| In-scope model, service, UI, permissions, audit and tests | Phase 0 behavior verified end to end. Request logs contain route, status and request ID. Business audit history begins with the business models. |
| Test, lint, type, CI and coverage gates | All local gates passed. Hosted GitHub Actions remains pending because this checkout has no remote. |
| Migrations from scratch and from the previous state | Passed on PostgreSQL 16, including rollback and reapplication with permission checks. |
| README, docs and ADRs | Updated and committed. |
| Self-review and unmet items | Hosted CI and scheduled scope are documented in [known gaps](known-gaps.md). |
| Phase report and next step | Stop at Phase 0 and wait for confirmation before Phase 1. |

# Muzqaymoq ERP

Procurement and warehouse ERP for Muzqaymoq. Phase 1 adds the catalog, warehouse ledger, FEFO issues, stocktakes, alerts and file imports to the Uzbek workspace.

## Start locally

With Docker Compose and Python 3.12 installed, generate the local environment and start the six services. The first build downloads images and dependencies.

```sh
python scripts/init_env.py
docker compose up -d --build --wait
docker compose ps
docker compose exec web python manage.py createsuperuser
```

Open [the ERP workspace](http://localhost:8000/) and sign in. Open the catalog and inventory from the dashboard navigation. The dashboard shows open stock alerts. Use [readiness](http://localhost:8000/health/) to check PostgreSQL and Redis. To stop the stack while retaining database volumes, run `docker compose down`.

The initial superuser is for bootstrapping. For routine access, provision a regular Django user and assign one of the [ERP role groups](docs/roles.md). The migrations create the groups and grants.

## Development checks

For native development, point the environment variables in [.env.example](.env.example) at a disposable PostgreSQL 16 database and Redis instance. Export the values in your shell. The PostgreSQL test user needs permission to create a test database. Use `APP_ENV=development` with local services.

```sh
python -m pip install -r requirements-dev.txt
npm ci --ignore-scripts
npm run build
make translations
make lint
make typecheck
python manage.py makemigrations --check --dry-run
python manage.py migrate
python manage.py collectstatic --noinput
make test
```

`make test` enforces 70% overall and 85% services coverage. Run `pre-commit install` to enable the local Ruff hooks. Django stores UTC timestamps and displays Asia/Tashkent time. Uzbek numbers use a space for grouping and a comma for decimals.

## Production configuration

Set `APP_ENV=production`, use unique secrets, and set `DJANGO_ALLOWED_HOSTS` to the hostnames served. Terminate HTTPS at your trusted reverse proxy and preserve the external Host. When the proxy connects over HTTP, set `TRUST_PROXY_HTTPS=1` and list its immediate peer addresses in `TRUSTED_PROXY_IPS`. The proxy must overwrite `X-Forwarded-Proto` and `X-Real-IP` with trusted values and be the only public route to Gunicorn. Django ignores those headers from peers outside the allowlist.

The readiness endpoint returns only `{"status": "ok"}` or `{"status": "unavailable"}` and stays reachable via HTTP for the container probe. The browser session expires after `SESSION_MAX_AGE` seconds. Compose publishes web on the host loopback and keeps PostgreSQL and Redis inside its private network.

The default VAT percentage is `12.00`, configurable via `VAT_RATE_PERCENT`. The Uzbekistan Tax Committee identifies 12% as the standard rate and describes a separate simplified regime for eligible businesses. [Uzbekistan Tax Committee](https://gov.uz/ru/soliq/news/view/182190).

For a disposable development database, run:

```sh
docker compose exec web python manage.py seed_demo
docker compose exec web python manage.py reconcile_stock
```

The demo creates milk, sugar, cups and wafer cones, three suppliers and five batches. Receive a lot into a location with matching storage, then have a manager approve it before issuing it. For stocktakes, use separate accounts for counting and approval. Browser scanning requires HTTPS or localhost. The same forms accept manual codes.

Catalog and opening-stock imports accept UTF-8 CSV and single-sheet XLSX files. The import screen lists required headers. Use ISO dates (`YYYY-MM-DD`) and a decimal point. Preview the file to see row errors, then select it again to commit. Imports limit files to 2 MiB and 500 rows. Opening stock quantities and prices are in base units and UZS.

See [the ERD](docs/erd.md), [known gaps](docs/known-gaps.md), and the [Phase 1 report](docs/phase-1.md).

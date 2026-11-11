# Known gaps

## Phase 0 verification

- The GitHub Actions workflow needs its first hosted run. This checkout has no Git remote. The phase report records the local checks.

## Scheduled scope

- `seed_demo` exits with an explicit dependency error until the inventory, production and payables models are available. Full demo data is scheduled for Phase 7.
- `reconcile_stock` exits until the Phase 1 ledger exists.
- The bot process is a Phase 0 service probe. Telegram linking, approvals and notifications are scheduled for Phase 5.
- The PWA caches its public offline screen. Offline business read views depend on the modules that introduce the data.
- The ERP catalog includes Uzbek and uses English source strings as fallback. Russian ERP translations are pending.
- Business audit history, optional privileged-account TOTP, backup and restore, and performance gates belong to the phases that introduce those capabilities.

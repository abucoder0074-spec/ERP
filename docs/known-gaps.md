# Known gaps

## Verification

- The GitHub Actions workflow needs its first hosted run. This checkout has no Git remote. The phase reports record the local checks.

## Scheduled scope

- `seed_demo` includes the Phase 1 catalog and stock. Recipes, sample debts and the full cross-module dataset are scheduled for later phases.
- The bot process is a Phase 0 service probe. Telegram linking, approvals and notifications are scheduled for Phase 5.
- The PWA caches its public offline screen. Offline business read views depend on the modules that introduce the data.
- The ERP catalog includes Uzbek and uses English source strings as fallback. Russian ERP translations are pending.
- Phase 1 audits catalog and inventory changes. Additional module audit history, optional privileged-account TOTP, backup and restore, and the 100,000-movement performance target remain scheduled.
- Physical phone camera capture has not been verified on a device. Manual entry remains available on unsupported browsers.

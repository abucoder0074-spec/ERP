# Known gaps

## Verification

- Phase 1 hosted CI passed on origin/main at 33a98b0. Phase 2 verification is recorded in its phase report.

## Scheduled scope

- `seed_demo` includes the Phase 1 catalog and stock and a Phase 2 procurement example with a mismatched invoice. Recipes, sample debts and the full cross-module dataset are scheduled for later phases.
- The bot process is a Phase 0 service probe. Telegram linking, approvals and notifications are scheduled for Phase 5.
- The PWA caches its public offline screen. Offline business read views depend on the modules that introduce the data.
- The ERP catalog includes Uzbek and uses English source strings as fallback. Russian ERP translations are pending.
- Phase 1 audits catalog and inventory changes. Additional module audit history, optional privileged-account TOTP, backup and restore, and the 100,000-movement performance target remain scheduled.
- Physical phone camera capture has not been verified on a device. Manual entry remains available on unsupported browsers.

## Procurement deployment

- The configured default attachment scan hook accepts files after format validation. Production scanning requires an organization-approved scanner hooked up through `PROCUREMENT_SCAN_HOOK`.
- RFQ dispatch is recorded for manual sending. Payment execution and supplier ledgers remain Phase 3 scope.
- An invoice reserves the receipt lines it references until cancellation. A partial invoice that differs from net receipt quantity requires a match exception.

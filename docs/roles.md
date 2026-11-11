# Phase 0 permissions

Every named role receives `core.access_workspace` through the role migration. Django checks this permission on the dashboard request before rendering navigation. Assign group membership to active, regular accounts.

| Actor | Dashboard GET/HEAD | Login GET/POST | Logout POST | Language POST | Health GET/HEAD | Manifest, service worker and offline GET/HEAD |
| --- | --- | --- | --- | --- | --- | --- |
| Admin | Allow | Public | Allow | Allow | Public | Public |
| Procurement Manager | Allow | Public | Allow | Allow | Public | Public |
| Procurement Specialist | Allow | Public | Allow | Allow | Public | Public |
| Warehouse Keeper | Allow | Public | Allow | Allow | Public | Public |
| Accountant | Allow | Public | Allow | Allow | Public | Public |
| Production Planner | Allow | Public | Allow | Allow | Public | Public |
| Viewer | Allow | Public | Allow | Allow | Public | Public |
| Active user without the grant | 403 | Public | Allow | Allow | Public | Public |
| Anonymous | Redirect to login | Public | Public confirmation | Allow | Public | Public |

Login POST requires credentials, CSRF and throttle clearance. Logout POST and language POST require CSRF. Logout clears the session and shows a confirmation page. Logout GET returns 405. Public health checks return generic readiness. There are no business actions in Phase 0.

The Admin group receives the same Phase 0 workspace grant as the other six groups. Django superuser status is a separate bootstrap privilege. Reserve it for provisioning and recovery. Subsequent phases add per-model and per-action permissions to this matrix.

## Phase 1 catalog and inventory

| Role | Read catalogs, balances, movements, stocktakes and alerts | Edit items and suppliers, including imports | Edit remaining catalogs | Receive, issue, transfer, return, count and write off within limit | Approve or post counts, change lot quality, override FEFO, reverse, import opening stock, large write-off | Audit history |
| --- | --- | --- | --- | --- | --- | --- |
| Admin | Yes | Yes | Yes | Yes | Yes | Yes |
| Procurement Manager | Yes | Yes | Yes | Yes | Yes | Yes |
| Procurement Specialist | Yes | Yes | No | No | No | No |
| Warehouse Keeper | Yes | No | No | Yes | No | No |
| Accountant | Yes | No | No | No | No | No |
| Production Planner | Yes | No | No | No | No | No |
| Viewer | Yes | No | No | No | No | No |
| Active account without a grant | 403 | 403 | 403 | 403 | 403 | 403 |
| Anonymous | Sign in | Sign in | Sign in | Sign in | Sign in | Sign in |

Catalog edits include archive and reactivate actions. A count can be changed or submitted by its counter only. Approval and posting require a different account from the counter. Managers can cancel pending stocktakes. Write-offs above `WRITE_OFF_LIMIT_UZS` require the large write-off grant, and overriding FEFO requires a reason. Stock balances and movement history are Phase 1 read reports.

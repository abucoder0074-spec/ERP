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

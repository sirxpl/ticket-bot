# Ticket bot roadmap

These documents capture the next dashboard and moderation features discussed for
the ticket bot:

- [`moderation-dashboard-plan.pdf`](moderation-dashboard-plan.pdf) describes the
  role-based dashboard, warning workflow, warning expiry worker, blacklist
  management, audit logging, configurable message templates, and required
  Discord permissions.
- [`dashboard-search-and-roles-plan.pdf`](dashboard-search-and-roles-plan.pdf)
  describes collapsible role assignment controls and live search for transcripts
  and blacklist entries.

## Planned work

### Dashboard and access control

- Add separate dashboard areas for overview, analytics, ticket configuration,
  trial schedules, transcripts, warnings, expired warnings, blacklist, staffing
  viewing, and the admin panel.
- Keep ordinary dashboard access separate from admin-panel access.
- Support environment-configured superadmins and explicitly granted admin-panel
  users.
- Add audit records for moderation and configuration changes.

### Warnings

- Add `/w1`, `/w2`, and `/w3` commands with a selected member and required
  nonblank reason.
- Apply the matching role and native Discord timeout:
  - W1: 6-hour timeout, 14-day warning duration
  - W2: 12-hour timeout, 21-day warning duration
  - W3: 1-day timeout, 28-day warning duration
- Persist active warning records, move expired records to history, remove
  matching roles, and attempt a warning DM.
- Use a restart-safe periodic expiry worker rather than a high-frequency loop.
- Make success, failure, DM-unavailable, warning-DM, and warning-log templates
  editable, including message, embed, and Components V2 renderers.

### Dashboard search and role controls

- Keep role assignment at the top of the page and collapse the configured-role
  list by default.
- Add live search with a clear button to transcripts and blacklist entries.
- Search transcripts by ticket number, username, and optionally Discord user ID.
- Search blacklist entries by username or exact Discord user ID.
- Treat Discord IDs as authoritative while displaying usernames as readable,
  changeable labels.
- Show useful zero-result messages instead of empty tables.

## Implementation note

The PDFs are source planning documents. The items above are not implemented by
adding the PDFs; they should be delivered as separate, reviewable code changes
with tests and permission checks.

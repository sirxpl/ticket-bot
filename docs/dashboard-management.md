# Dashboard management

## Bulk controls

Access Control provides Select All, per-entry checkboxes, a selected count, and Remove Selected for its configurable role/user lists. These actions edit the selected configuration list and preserve its other entries and unrelated settings. Administrator IDs configured through the environment remain separate.

Carry settings support selected category removal, selected per-category role/badge removal, and selected global blocked-badge removal. Keep at least one labeled category. Category edits use the existing background-save flow; global badge and access-list changes submit explicitly. Bulk selection itself is not treated as an unsaved configuration edit.

Bulk endpoints require their existing page-management permission and a session CSRF token. Access list names are allow-listed, selections are bounded to 100 IDs, and MongoDB writes update the relevant field. A configured database outage reports failure rather than claiming a temporary local change was saved.

## Ticket Logging

`/dashboard/ticket-logs` provides dashboard-authorized activity history, literal text/event filters, 50-record pagination, actor/time details, and ticket/transcript links. `/tickets/logs` redirects here. Diagnostic history also requires dashboard access.

Admins configure Discord ticket delivery, channel targets, and opened/closed/renamed/work-started/work-finished events. Work commands retain their saved activity records and can also post to the selected Discord channel. Disabling this delivery leaves history intact. Blacklist and warning logging keep their separate channels.

## Unsaved changes

The notice compares configuration controls against their loaded/saved values. Search/filter fields, immediate actions, and bulk selections are excluded. It has no click targets or overlay, uses `pointer-events: none`, leaves controls enabled, and never registers a blocking navigation/unload prompt.

Background category saves are queued. A successful response advances the saved snapshot for that request, so newer edits remain dirty. Failed, rejected, partially accepted, or expired-session saves leave the warning visible. Explicit page navigation can discard unsaved edits; the notice does not create a local draft.

No database migration or additional Render environment variables are required.

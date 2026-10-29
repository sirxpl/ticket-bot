# Dashboard management

## Bulk controls

Access Control provides Select All, per-entry checkboxes, a selected count, and Remove Selected for its configurable role/user lists. These actions edit the selected configuration list and preserve its other entries and unrelated settings. Administrator IDs configured through the environment remain separate.

Carry settings support selected category removal, selected per-category role/badge removal, and selected global blocked-badge removal. Keep at least one labeled category. Category edits use the existing background-save flow; global badge and access-list changes submit explicitly. Bulk selection itself is not treated as an unsaved configuration edit.

Bulk endpoints require their existing page-management permission and a session CSRF token. Access list names are allow-listed, selections are bounded to 100 IDs, and MongoDB writes update the relevant field. A configured database outage reports failure rather than claiming a temporary local change was saved.

## Ticket Logging

Admins can run `/ticket-logging channel:#ticket-logs` in Discord to select a text channel and enable delivery. The bot must have View Channel and Send Messages there. This preserves the blacklist/warning channel selections. A saved channel missing from cache is fetched through Discord before delivery.

`/dashboard/ticket-logs` provides dashboard-authorized activity history, literal text/event filters, 50-record pagination, actor/time details, and ticket/transcript links. `/tickets/logs` redirects here. Diagnostic history also requires dashboard access.

Admins configure Discord ticket delivery in Access Control → **Loggings** (also available on Ticket Logging). They choose channel targets, and opened/closed/renamed/work-started/work-finished events. Work commands retain their saved activity records and can also post to the selected Discord channel. Disabling this delivery leaves history intact. Blacklist and warning logging keep their separate channels.

## Unsaved changes

The notice compares configuration controls against their loaded/saved values. Search/filter fields, immediate actions, and bulk selections are excluded. It has no click targets or overlay, uses `pointer-events: none`, leaves controls enabled, and never registers a blocking navigation/unload prompt.

Background category saves are queued. A successful response advances the saved snapshot for that request, so newer edits remain dirty. Failed, rejected, partially accepted, or expired-session saves leave the warning visible. Explicit page navigation can discard unsaved edits; the notice does not create a local draft.

## Blacklist Roles

Access Control → **Blacklist Roles** is the single editor for a name, Discord role, default reason and restriction scope. `/addticketblacklist` autocompletes those configured roles; Regular and Voidcore presets are removed. Existing flat blacklist roles appear in this editor automatically. Reasons can be overridden per command. Up to 25 roles are supported, with one saved blacklist per member; remove an existing blacklist before replacing it.

For Voidcore/category restrictions, select **Categories listing this role** and add the assigned role to each affected category's blacklist roles in Carry Manager Settings. Select **All ticket types** for a global ticket restriction. Category restrictions are also enforced from saved records if role assignment fails. All blacklist checks run before badge/ad gates and form presentation, with a fresh check on submission. Editing/removing a type does not change existing member records. `/removeticketblacklist` always uses the saved role; it no longer asks for a blacklist type.

The bot needs Manage Roles and must be above each assigned role. Failed removals keep the record for retry. Timed role cleanup runs every minute; expired records remain listed while cleanup is pending. Dashboard unblacklisting requires admin access, an online bot and a CSRF token, and removes the saved role too.

Existing local blacklist data migrates automatically into MongoDB on first read when MongoDB is configured. A configured database outage stops blacklist operations with an error. No additional Render environment variables are required.

## Ticket work and closing

New tickets use a pinned Components V2 welcome with one persistent Close Ticket button. `/start-ticket` marks it still ongoing and blocks the opener's close routes; `/end-ticket` updates the status and enables owner closing through the same button. Authorized staff can still close. Existing tickets retain their original buttons. Status edits never add another close button. Restarted bots dispatch existing buttons using the same persistent custom ID.

Ticket, blacklist and warning logs use Components V2. Transcript archives retain their text and nested buttons.


# Dashboard management

## Bulk controls

Access Control provides Select All, per-entry checkboxes, a selected count, and Remove Selected for its configurable role/user lists. These actions edit the selected configuration list and preserve its other entries and unrelated settings. Administrator IDs configured through the environment remain separate.

Carry settings support selected category removal, selected per-category role/badge removal, and selected global blocked-badge removal. Keep at least one labeled category. Category edits use the existing background-save flow; global badge and access-list changes submit explicitly. Bulk selection itself is not treated as an unsaved configuration edit.

Bulk endpoints require their existing page-management permission and a session CSRF token. Access list names are allow-listed, selections are bounded to 100 IDs, and MongoDB writes update the relevant field. A configured database outage reports failure rather than claiming a temporary local change was saved.

## Ticket Logging

Admins can run `/ticket-logging channel:#ticket-logs` in Discord to select a text channel and enable delivery. The bot must have View Channel and Send Messages there. This preserves the blacklist/warning channel selections. A saved channel missing from cache is fetched through Discord before delivery.

`/dashboard/ticket-logs` provides dashboard-authorized activity history, literal text/event filters, 50-record pagination, actor/time details, and ticket/transcript links. `/tickets/logs` redirects here. Diagnostic history also requires dashboard access.

Admins configure Discord ticket delivery in Access Control → **Loggings** (also available on Ticket Logging). They choose channel targets, and opened/closed/renamed/work-started/work-finished events. Work commands retain their saved activity records and can also post to the selected Discord channel. Disabling this delivery leaves history intact. Blacklist and warning logging keep their separate channels.

## Expanded Discord audit events

Access Control → **Loggings** has a separate **Access audit delivery** switch, event checkboxes, and an **Access audit channel**. These settings also appear on Ticket Logging. Existing ticket-channel slash configuration preserves them.

| Events | Discord destination |
| --- | --- |
| Ticket opened/closed/renamed, work started/ended, moved, member added/removed, autoclose changed | Ticket activity channel; ticket delivery switch and event checklist apply |
| Member blacklisted/unblacklisted, expiry, configured blacklist-role changes, manual role changes, blacklist-denied ticket attempts | Blacklist channel → Access audit channel → Ticket activity channel; audit delivery switch and event checklist apply |
| Global block/unblock, cooldown cleared, bulk access removal, unblock link created/revoked | Access audit channel → Ticket activity channel; audit delivery switch and event checklist apply |
| Warnings | Existing Warning log channel |

Each row uses the first configured destination, producing one post per event. Logs use Components V2 and suppress mentions. History is saved independently of Discord filters. If the bot is offline, dashboard changes remain in history; delivery is not queued for replay. A missing cached channel is fetched from Discord. The bot must be able to view and send messages in configured channels.

Manual blacklist-role assignment/removal is observed through Guild Members intent. Its actor is labeled unavailable because a member update does not identify who changed the roles. Bot-driven assignments/removals retain the original command actor and suppress the matching gateway echo. Existing applied blacklist records are preserved.

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



## Manage Pin Access

Access Control → **Manage Pin Access** grants selected roles Discord's native **Pin Messages** permission in every active ticket channel. Members use Discord's normal message Pin/Unpin controls. The old bot **Toggle Pin** context menu is removed during command sync.

The setting applies at ticket creation, after adding/removing roles (including bulk removal), at startup/reconnect, and through **Apply to existing tickets**. Removed roles have the managed Pin Messages Allow cleared back to Inherit. Other channel flags remain unchanged: the setting does not grant Manage Messages, message deletion, View Channel, or Send Messages. Existing role/admin permissions can still allow pinning independently.

The bot needs Manage Roles to modify channel overwrites, and discord.py 2.7.1 or newer for the dedicated pin flag. Failed channel updates retry; successful updates do not repeatedly issue Discord requests. Settings/removal work persists through offline periods and database outages do not clear pending revocations using fallback data. No new Render variables are needed.


## Home, glass surfaces, and Analytics

The former Overview page is now labeled Home, with `/dashboard/home` as its navigation URL. Existing `/dashboard` and `/dashboard/overview` links continue to work. Dashboard surfaces use translucent glass with solid-background and reduced-motion fallbacks. Supported browsers use native transitions between dashboard pages; links and form submissions remain normal server requests.

Analytics supports 7, 14, and 30 UTC calendar days including today, plus category filtering. Queue counts and age always describe currently active tickets, regardless of the period. Created/closed counts use their event dates; lifecycle follows tickets created in the selected period. Timing samples use the date of the measured reply, first work start, or close, and require a valid creation event. Dashed chart lines are averages from the preceding period, not forecasts.

Message tracking starts with this release and stores only ticket/message IDs, timestamps, and actor identity/avatar metadata. Staff means Manage Channels, configured Basic Command Access, or the configured support role; the opener always counts as the member. Guests and bots are excluded. Staff messages do not reset the opener's autoclose timer. Historical tickets without message events may show no recorded staff reply. Work-start metrics use the first `/start-ticket` per ticket, including after work has ended. No historical message content is scanned or backfilled; ratings and written satisfaction feedback are not collected.

Staff rankings are limited to the selected period/category and exclude known opener and bot closes. Discord avatars resolve from the current bot cache, then saved Discord CDN metadata, then a default avatar. No per-member Discord API requests are made by Analytics page loads. Chart tooltips support arrow keys and provide expandable data tables.


### Home navigation cards

Home opens with the connected server's cached Discord icon/name, four Quick Actions (Documentation, Service Status, Server Rules, Carry Guidelines), and a module grid. Every module points to an existing dashboard page and is shown only when its matching sidebar permission allows it. No new module permissions are granted. The grid uses four columns on desktop, then three/two/one as space decreases. Active tickets, total lifetime tickets, cooldowns, and the existing live lists remain below the navigation grid. When the bot has no cached server icon, the header uses a shield fallback.

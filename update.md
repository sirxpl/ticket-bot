# 🎟️ Carry Ticket Bot — Update Log

A running changelog for the **Carry Ticket Bot** Discord commands, ticket system, web dashboard, documentation, and reliability fixes. Newest updates appear first.

**Last updated:** October 30, 2026

**Most recent merged UI update:** [PR #46 — Liquid-glass transcript UI](https://github.com/sirxpl/ticket-bot/pull/46), merged to `main` on October 28, 2026.
**Deployment note:** Merged into `main` does not necessarily mean Render has deployed it. Production deployment, live Discord slash-command sync, and end-to-end testing have **not been verified** in this changelog.

---

## October 2026

### 2026-10-30 — Ticket welcome and Status field formatting

**Status:** Implemented for review; live Discord validation remains pending deployment.

- Moved the configurable welcome mention/content above the Components V2 ticket card to match the reference layout. Kept the ticket title, description and answer fields inside the card, with one Close Ticket button below.
- Made Status a field inside the card: **Status** followed by `Open - you may close this ticket` on its own line. Starting work shows a matching ongoing field; ending work restores the Open text.
- Validation: 197 regression tests passed, including mention/status placement, existing-panel status updates, and one persistent close button. Python compilation and whitespace checks passed.
- Existing V2 panels move their old outside status into the card when `/start-ticket` or `/end-ticket` updates them. Preserved persistent button IDs and reserved message text space for the outside content/status.


### 2026-10-29 — Native Discord Pin Messages access in ticket channels

**Status:** Implemented for review; live Render/Discord validation remains pending deployment.

- Changed **Manage Pin Access** to grant the native Discord **Pin Messages** channel permission. Removed the bot's Toggle Pin context menu and its separate command-access check; Discord's normal Pin/Unpin controls now govern usage.
- New ticket creation grants only `pin_messages=True` for selected roles. Removed the old Manage Messages fallback and the extra View/Send grants; existing unrelated overwrites are preserved.
- Added reconciliation for every active ticket at startup/reconnect, after role add/removal (including bulk removal), and via **Apply to existing tickets**. Removing a selected role clears its managed pin Allow back to Inherit, without erasing explicit Deny or other permissions.
- Permission failures retry automatically without repeatedly patching channels that already match. Pending removals persist while the bot is offline; revision checks prevent an older sync from clearing newer removal work, and database outages do not treat fallback data as successful cleanup.
- Validation: 197 regression tests passed, including actual ticket creation overwrites, native pin grants, role removal, offline persistence, stale-revision protection and failure retries. Python compilation and whitespace checks passed.
- Added admin/CSRF checks to the native pin controls and explicit messages for queued/offline updates. The bot needs Manage Roles to edit channel overwrites. Raised the discord.py minimum to 2.7.1 for its native pin flag; no new Render variables are needed.


### 2026-10-29 — Expanded Discord logging and access audits

**Status:** Implemented for review; live Discord/Render verification remains pending deployment.

- Expanded Access Control → **Loggings** with independent access-audit delivery, event checkboxes, and an optional Access audit channel. Blacklist events use the blacklist channel first, then the access-audit channel, then the ticket activity channel. Other access events use the access-audit channel or ticket activity channel. Each event posts once as Components V2 with mentions suppressed.
- Added member blacklist expiry, blacklist-role configuration add/edit/remove, manual Discord blacklist-role assignment/removal, and blacklist-denied ticket-attempt logs. Bot-managed role changes suppress their matching gateway echo so existing blacklist/unblacklist command posts are not duplicated. Manual Discord role updates require Guild Members intent.
- Added global user block/unblock logs, including bulk unblocking and successful terms-link self-unblocking; cooldown clearing; bulk access removals; and unblock-link creation/revocation. Link logs contain the affected user and actor, never the token or URL. Failed/no-op changes do not announce successful changes.
- Added ticket move, member add/remove, and autoclose-change events to the ticket delivery checklist, alongside existing open/close/rename/start/end events.
- Saved history includes the new events and user/role search fields. Pausing or filtering Discord delivery preserves that history; dashboard changes made while the bot is offline are saved without claiming Discord delivery. Logging failures do not undo successful settings/moderation changes.
- Validation: 173 regression tests passed. Checks cover file/MongoDB-model settings, real authenticated routes, channel priority/fallback, event filters, failure handling, manual-role changes and duplicate suppression, and actual ticket-management callbacks. No new Render environment variables are introduced.


### 2026-10-29 — Fix false blacklist-status error and use configured roles

**Status:** Follow-up fix for review; live Render/Discord verification remains pending deployment.

- Reproduced the reported ticket-submission failure: a function-local `get_ticket_categories` import shadowed the earlier lookup and raised `UnboundLocalError`, producing the generic blacklist-status error for valid members. Removed the shadowing import and added an integration regression that opens and submits the actual Discord modal through channel creation and its single welcome panel.
- Consolidated blacklist checks before badge/ad gates and ticket forms. Delayed verification/ad continuations and final submission recheck current restrictions. Removed duplicate checks that silently swallowed errors.
- Removed automatic Regular/Voidcore command choices. **Blacklist Roles** is now one Access Control editor for role, name, reason and scope; it includes roles from the old flat blacklist role list. `/addticketblacklist` selects those roles and `/removeticketblacklist` uses the saved assignment without a type argument. Existing applied records remain available for removal and expiry cleanup.
- Role fields must select a Discord role, prevent duplicates, and use the role ID internally. Saving category-only roles does not add them to the all-ticket block list. Fixed shared mutable Access Control defaults discovered while testing role isolation.
- Validation: 150 regression tests passed, including the actual form-open/submission flow, pre-form role blocks, role configuration migration, and Discord logging-channel configuration. Python compilation and whitespace checks passed.
- Added admin-only `/ticket-logging channel:#channel` to configure and enable ticket logs directly in Discord, while preserving separate blacklist/warning channels and event selection. Validates the bot can view/send in that channel. Ticket/blacklist delivery fetches a saved channel if it is missing from cache; the dashboard Loggings channel dropdown remains available.


### 2026-10-29 — Ticket close controls, configurable blacklists, and Components V2 logging

**Status:** Implemented for review. Live Discord and Render validation requires deployment.

- New tickets have a pinned Components V2 welcome panel with the configured details and exactly one persistent **Close Ticket** button. Start/end commands update its visible status; the opener sees **still ongoing** while locked and can use that same button after `/end-ticket`. Legacy buttons retain their custom ID and lock behavior across restarts.
- Fixed close buttons rejecting valid active tickets moved outside the default Discord category. Fixed the close-confirmation Cancel response. Start/end commands refuse tickets already being closed, preventing a misleading successful state change during transcript generation.
- Added **Blacklist roles and types** in Access Control, with add/remove fields for a stable ID, name, assigned role, default reason, and all-ticket/category restriction. `/addticketblacklist` autocompletes these configured choices. Regular and the existing Voidcore role remain the initial defaults.
- Saved blacklist restrictions enforce affected categories even when Discord role assignment fails. Ticket forms recheck restrictions on submission, so a form opened earlier cannot bypass a new blacklist. Existing member records keep their original role/scope if a type is edited or removed.
- Fixed blacklist persistence using MongoDB when configured, importing existing local entries once. Database outages report failure rather than silently saving ephemeral data. Removal uses the saved role instead of the command's optional legacy type; role failures keep the record available for retry.
- Added a one-minute expiry worker to remove timed blacklist roles and retry failures. Expired records remain visible in the dashboard until cleanup succeeds. Dashboard unblacklisting now removes the assigned role, requires admin access and a CSRF token, and reports failure.
- Added **Loggings** in Access Control with the existing ticket event switches and ticket/blacklist/warning channels. Shares one form with Ticket Logging and preserves saved channel IDs even when the channel is unavailable in cache.
- Ticket, blacklist, and warning log delivery now uses Components V2 with suppressed mentions. Transcripts retain Components V2 text and nested buttons.
- Added regression checks for both storage backends, type editing, snapshots, expiry retries, role failures, and owner close controls. No new Render environment variables are needed; the bot needs Manage Roles and a role above assigned blacklist roles.


### 2026-10-28 — Bulk settings, organized carry controls, and Ticket Logging

**Status:** Implemented in the pending dashboard update for review; live Render/Discord verification is pending deployment.

- Added selected-entry and Select All removal to the configurable Access Control role/user lists, plus blocked badges, carry categories, and per-category role/badge lists. Bulk actions preserve entries that were not selected.
- Grouped Access Control into Dashboard Access, Carry Team, Ticket/Page Permissions, Command Permissions, Logging/Operations, and Restrictions/Recovery. Kept its panels collapsed by default and reorganized the Carry Team sidebar and carry settings.
- Added **Ticket Logging** at `/dashboard/ticket-logs`: searchable, filtered, 50-event activity pages and admin controls for Discord channels, delivery, and opened/closed/renamed/work-started/work-finished events.
- Connected `/start-ticket` and `/end-ticket` to the selected Discord logging events. Pausing Discord delivery preserves saved history; blacklist and warning channels remain independent.
- Replaced the old hidden history view's missing-layout path with a redirect to Ticket Logging, and put the diagnostic history endpoint behind dashboard access.
- Added a nonmodal, click-through **You have unsaved changes** notice. It does not disable controls, intercept clicks, or prompt/block navigation. Bulk selection and history filters do not mark settings dirty.
- Category background saves now report confirmed success/failure, run in order, and keep later edits marked unsaved while an earlier save finishes. Fixed checkbox alignment when saving several categories.
- New bulk/configuration writes use targeted MongoDB updates, CSRF checks, and explicit storage-failure reporting. No new Render environment variables are required.

### 2026-10-28 — Carry Team Application management

**Status:** Implemented for review; live Render and Discord verification remains pending.

- Added **Carry Team Application** at `/dashboard/carry-applications` for admins and explicitly configured Carry Manager roles with dashboard access.
- Added an accepting-responses switch and configurable 0–365 day denial cooldown (open / 14 days by default), stored through the existing MongoDB configuration. Closed responses are enforced on every application route and at submission storage.
- Added applicant pending/accepted/closed status pages and a live **Next application in** denial countdown; denial DMs use the configured policy and a Discord relative timestamp.
- Added status filters, 25-record pagination, and selected acceptance removal / denial cooldown clearing. Acceptance removal retains staff history and allows reapplication; cooldown clearing keeps the original denial and records the manager/time.
- Settings changes update existing denied deadlines; cleared cooldowns remain cleared. Pending applications are protected from these management actions.
- Added local-file and MongoDB-model policy tests, Flask permission/submission tests, and configurable DM timestamp checks. No additional Render environment variables are needed.

### 2026-10-27 — Optional privileged intents and event-driven ticket departure checks (staging PR)

**Status:** Proposed for the new staging bot; do not merge or deploy to the existing production bot before tests.

- Added opt-in `DISCORD_MEMBERS_INTENT` and `DISCORD_MESSAGE_CONTENT_INTENT` environment variables, **both disabled by default** to avoid breaking old applications without privileged-intent approval.
- Use `on_raw_member_remove` to close the departed member's active ticket immediately when Guild Members intent is available; match both user and server IDs.
- Preserve transcript-first `do_close` safety, prevent overlapping departure closes, and keep REST membership reconciliation every 12 hours (every 2 hours when Guild Members intent is disabled).
- Run 12-hour reminder and 24-hour inactivity checks every 10 minutes rather than every 2 hours; preserve `/autoclose disable`.
- Keep scheduled warning expiration, trial updates, carry-application workers, watchdog, offline schedule, and status checks; they are not replaced by privileged intents.
- Tests and real Render/Discord verification are required before rollout: unauthorized intents can cause Gateway close 4014, and the old and new bots must not share a writable MongoDB production database.

### 2026-10-26 — Discord website domain verification

**Status:** Committed to `main`; live Render deployment and Discord's ownership verification are not yet confirmed.

- Added unauthenticated `GET /.well-known/discord` to the existing Flask app in `bot/main.py` for Discord profile Connections → Domain verification.
- Returns the exact Discord-issued `dh=...` challenge as `text/plain`, without redirects or dashboard sign-in.
- Added `bot/tests/test_discord_domain_verification.py` to check the HTTP 200 response, exact challenge content, content type, and HEAD support without starting the bot.
- After Render deploys the new commit, open `https://ticket-bot-f184.onrender.com/.well-known/discord` to confirm the challenge before clicking **Verify** in Discord.


### 2026-10-24 — Downtime documentation, page consistency, and Docs logo correction (PR #43)

**Status:** Changes committed to [PR #43](https://github.com/sirxpl/ticket-bot/pull/43), which was **merged** on October 24, 2026. Live deployment and browser verification remain unconfirmed.

- **Missing Docs guide:** Added **Downtime & Offline Schedule** to the `/docs` sidebar, top navigation, and Quick Reference, with a new clock icon and dedicated guide at `/docs#downtime`.
- **Documentation content:** Explains the weekly offline schedule, configured timezone, windows spanning midnight, staff-only configuration in Access Control, and the difference between scheduled downtime, service outages, and Discord API rate limits.
- **Inconsistent Downtime UI:** Rebuilt `/downtime` using the shared Docs/Status layout, left sidebar, dark responsive cards, and matching spacing, typography, and navigation.
- **Preserved behavior:** Live status polling, API-limit countdown, weekly schedule, and server/browser timezone labels remain available.
- **Legacy schedule display fix:** Schedule windows with no explicit `enabled` field now display as enabled rather than incorrectly appearing disabled on Status/Downtime.
- **Docs header issue:** Fixed the extra space between **Carry** and **Ticket Bot** by grouping the wordmark into one text element, consistent with the shared public-page header.
- **Regression coverage:** Added tests for the Downtime documentation/sidebar, matching page layout, and consistent header wordmark.
- **Verification needed:** Merge and deploy PR #43, then check `/docs#downtime`, `/downtime`, `/status#downtime`, mobile navigation, and logo alignment.

### 2026-10-24 — Shared website UI, downtime status, and collapsed settings (PR #42)

**Status:** [PR #42](https://github.com/sirxpl/ticket-bot/pull/42) merged to `main` on October 24, 2026. Live deployment and browser testing have not been independently confirmed.

- Updated **Rules & Regulations**, **Carry Guidelines**, **Privacy**, **Status**, and the detailed **Downtime** page to use a shared Docs-inspired dark header and mobile navigation.
- Added the configured weekly **Downtime & Offline Schedule** to `/status`, including timezone, enabled state, and current schedule-window status.
- Removed the automatic floating **Bot Status / Downtime & Offline Schedule** widget from other pages; the information now lives on its dedicated pages.
- Made all seven **Carry Manager Settings** panels collapsed by default while preserving settings and forms. Ad Gag deep links can still open the intended panel.
- Reorganized **Access Control** into 24 independently collapsible panels, preserving its 42 existing forms and authorization logic.
- Added structural regression checks for template compilation, public navigation, Status data, and settings accordions.

### 2026-10-24 — Carry Manager Settings rendering bug (PR #41)

**Status:** Fix merged to `main` in [PR #41](https://github.com/sirxpl/ticket-bot/pull/41) on October 24, 2026; **production deployment and browser verification remain unconfirmed here**.

- **Affected route:** `GET /dashboard/carry-settings` on Render returned **HTTP 500** (observed at 00:32 UTC on October 24, 2026).
- **Root cause:** The admin-only `dashboard_pages/_ad_gag_settings.html` partial had a compressed CSS media query on line 33 beginning with `{#ad-gag-settings`. Jinja2 interprets `{#` as an opening template comment, causing `TemplateSyntaxError: Missing end of comment tag` when Carry Manager Settings includes the partial.
- **Primary fix:** Put the media query's opening brace and `#ad-gag-settings` CSS selectors on separate lines, preventing Jinja from mistaking CSS for a comment. The layout and responsive behavior remain unchanged.
- **Additional bug found:** The **Add Ad** button called an undefined `addAd()` function even though the script exposes `addAprilFoolsAd()`. Updated the button to call the existing function without changing saved ad data.
- **Observed scope:** During the same log window, `/dashboard/trial-schedules`, `/dashboard/warning-builder`, and `/dashboard/access-control` returned HTTP 200. Discord successfully loaded all six cogs and synchronized 26 slash commands. No evidence in these logs indicates a MongoDB write or data-loss issue.
- **Verification needed:** After merging/deploying, load Carry Manager Settings as an authorized admin, confirm HTTP 200, add and remove an advertisement, save settings, refresh to confirm persistence, and test the layout on mobile. Confirm non-admin users cannot access the Ad Gag editor.

### 2026-10-24 — Major dashboard and ticket workflow update

#### 🎫 Slash commands and ticket management
- **`/request-close [reason]`** — new Components V2 close-request panel with approve/decline buttons. Only the ticket opener can respond.
- **`/requestclose`** — retained as a backwards-compatible alias for `/request-close`.
- **`/start-ticket`** — authorized staff can mark a ticket as *in progress*, preventing the opener from closing it while staff are working.
- **`/end-ticket`** — authorized staff can end the in-progress state and restore the opener's ability to close the ticket.
- Added Components V2 status announcements for ticket start/end actions, plus ticket activity log entries.
- Protected the opener's Close Ticket button, its confirmation step, close-request approvals, and the central ticket-close handler from bypassing an active lock.
- Lock changes invalidate old close-request approvals, so a stale approval cannot close a ticket after the lock state changes.
- Commands are **discoverable by server members**, but management actions still require appropriate staff access: Manage Channels, a configured bot admin, or explicit Basic Command Access.
- Updated ticket owner checks to rely on active-ticket data rather than loading the full ticket history just to determine close-button permissions.

#### 🖥️ Dashboard and website
- Split the dashboard into **nine separately maintained HTML page templates** with dedicated URLs:
  `Overview`, `Analytics`, `Transcripts`, `Blacklists`, `Warnings`, `Carry Manager Settings`, `Trial Schedules`, `Warning Builder`, and `Access Control`.
- Added a public introduction page at `/` and moved the protected staff interface to `/dashboard`.
- Preserved the existing dashboard styling, forms, and server-side access restrictions during the split.
- Integrated the April Fools **Ad Gag editor** into Carry Manager Settings; the old `/admin` route redirects to the integrated controls.
- Moved the read-only configured admin-ID display into Access Control.

#### ⚡ MongoDB and performance
- Changed dashboard reads to load data **only for the selected section**, instead of fetching all sections during every page visit.
- Reused bot settings and access-control settings **within a single dashboard request**; no cross-user or long-lived permission cache was added.
- Added transcript pagination (**50 entries per page**) and batched creator/log lookups to reduce repeated MongoDB queries.
- Avoided unnecessary Discord API username lookups for display-only lists by using cached names or user IDs.
- Added atomic MongoDB updates for the ticket owner-close lock and regular per-ticket fields to reduce accidental overwrites from overlapping operations.
- Made ticket locking report an error instead of claiming persistent success if MongoDB is configured but unavailable.

#### 📚 Documentation and navigation
- Redesigned `/docs` with a dark theme, the yellow ticket symbol, an updated sidebar, mobile navigation, and clickable feature cards.
- Updated guides for dashboard pages, access permissions, Ad Gag configuration, transcript browsing, slash commands, and storage.
- Refreshed the Terms & Conditions page header **without changing the underlying legal text**.
- Removed the distracting floating Bot Status widget from documentation and policy pages; the separate `/status` page remains available.

#### ✅ Verification and rollout
- Repository structure, command registration, navigation links, page access checks, and lock-state logic received static/code-level checks during development.
- **Still to verify after deployment:** Python startup/imports, Discord command synchronization, Components V2 button interactions, permissions for staff vs. ticket owners, MongoDB reliability, and dashboard navigation in a real browser.
- Ticket locks are designed to persist in MongoDB; users should verify that MongoDB is connected before relying on them in production.

---

## Earlier updates (August–September 2026)

The original update history is preserved below, with dates formatted as YYYY-MM-DD.

### 2026-09-08
- Fixed dashboard warning timestamps so ISO and Unix timestamps render correctly.
- Replaced invalid zero timestamps that displayed as December 1969 with `Not recorded`.
- Redesigned warning records with cleaner Active, Revoked, and warning-tier badges.
- Restyled dashboard search bars with a search icon, rounded layout, improved placeholder contrast, and focus styling.

### 2026-09-07
- Added a public dashboard preview with fictional overview statistics.
- Extended the preview sidebar to fill the available viewport height.
- Added dismissible X buttons to dashboard notifications.
- Added a sample transcript category and preview transcript page.
- Added fictional `krayon10` and `tdsash` transcript messages with demo avatars.
- Added Terms and Conditions links to the dashboard, transcript page, and public pages.

### 2026-09-06
- Added the public Terms and Conditions web page using `docs/terms_and_conditions.txt`.
- Styled the OAuth2 terms landing page and account-switch experience.
- Added automatic unblock links after terms acceptance.
- Added active single-use unblock-link management and copy controls.
- Added global user blocking for dashboard and slash-command access.
- Added warning records, moderation logs, warning-role removal, timeout removal, and warning DMs.
- Added the admin panel foundation and moderation/dashboard roadmap documentation.

### 2026-09-05
- Added warning commands, warning presets, moderation access roles, and moderation user management.
- Added warning preset management to the dashboard.

### 2026-09-04
- Restyled the dashboard and made public URLs domain-agnostic.
- Updated dashboard, rules, privacy, status, and documentation links to use Flask routes.

### 2026-09-03
- Added Bloxlink verification support.
- Improved default ticket categories and ticket close-confirmation handling.
- Updated trial modifiers and ticket-category initialization.

### 2026-09-02
- Added Trial Schedules to the dashboard.
- Added trial schedule saving, publishing, and current-rotation logic.
- Refactored trial schedule formatting and accessibility.

### 2026-09-01
- Added dashboard analytics and analytics-role management.
- Added pacing for member fetching in the ticket system.

### 2026-08-31
- Added TDS Level collection and placeholders to ticket forms and welcome messages.
- Added cooldown pruning and improved ticket welcome-message handling.

### 2026-08-30
- Fixed Discord OAuth callback URL handling.
- Restored storage compatibility and durable Carry Rules and analytics APIs.
- Improved Discord Linked Roles metadata flow and error handling.

### 2026-08-29
- Added Carry Rules agreement and Discord Linked Roles features.
- Added ticket analytics and staff-reply activity logging.
- Added level collection and related welcome-message settings.
- Expanded README deployment and configuration documentation.

### 2026-08-28
- Added the Linked Roles requirement.

### 2026-08-22
- Fixed role-based access so configured roles are required to execute protected commands.
- Added per-section role requirements.

### 2026-08-20
- Added ticket dropdowns.
- Added Components V2 support.
- Released the initial ticket-system update.

---

## Planned features (not released)

These are ideas, **not** features claimed to exist in the current bot:

- **Graphing / expanded charts** — additional analytics visualizations.
- **Points** — a future points system, scope to be determined.
- **Leaderboards** — future rankings once the underlying data and rules are defined.

---

## Keeping this file current

Add new dated sections **above** the older history. Separate shipped or merged changes from proposed work, and avoid marking features as live until deployment and smoke tests confirm they work. Describe any commands, configuration migrations, permission changes, and known limitations that server administrators need to know.



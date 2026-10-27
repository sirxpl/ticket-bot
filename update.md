# 🎟️ Carry Ticket Bot — Update Log

A running changelog for the **Carry Ticket Bot** Discord commands, ticket system, web dashboard, documentation, and reliability fixes. Newest updates appear first.

**Last updated:** October 26, 2026  
**Most recent merged UI update:** [PR #42 — Public pages, Status, and collapsible settings](https://github.com/sirxpl/ticket-bot/pull/42) (merged to `main` on October 24, 2026)  
**Deployment note:** Merged into `main` does not necessarily mean Render has deployed it. Production deployment, live Discord slash-command sync, and end-to-end testing have **not been verified** in this changelog.

---

## October 2026

### 2026-10-26 — Discord website domain verification

**Status:** Committed to `main`; live Render deployment and Discord's ownership verification are not yet confirmed.

- Added unauthenticated `GET /.well-known/discord` to the existing Flask app in `bot/main.py` for Discord profile Connections → Domain verification.
- Returns the exact Discord-issued `dh=...` challenge as `text/plain`, without redirects or dashboard sign-in.
- Added `bot/tests/test_discord_domain_verification.py` to check the HTTP 200 response, exact challenge content, content type, and HEAD support without starting the bot.
- After Render deploys the new commit, open `https://ticket-bot-f184.onrender.com/.well-known/discord` to confirm the challenge before clicking **Verify** in Discord.


### 2026-10-24 — Downtime documentation, page consistency, and Docs logo correction (PR #43)

**Status:** Changes committed to [PR #43](https://github.com/sirxpl/ticket-bot/pull/43), which is **open and not yet merged**. They are not verified on the live website.

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

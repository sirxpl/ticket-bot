> [!IMPORTANT]
> This is for updating anything, including slash commands and other fixes.
>
To be added:
- Graphings
- Points
- Leaderboards

## Update Log

### 09/08/26
- Fixed dashboard warning timestamps so ISO and Unix timestamps render correctly.
- Replaced invalid zero timestamps that displayed as December 1969 with `Not recorded`.
- Redesigned warning records with cleaner Active, Revoked, and warning-tier badges.
- Restyled dashboard search bars with a search icon, rounded layout, improved placeholder contrast, and focus styling.

### 09/07/26
- Added a public dashboard preview with fictional overview statistics.
- Extended the preview sidebar to fill the available viewport height.
- Added dismissible X buttons to dashboard notifications.
- Added a sample transcript category and preview transcript page.
- Added fictional `krayon10` and `tdsash` transcript messages with demo avatars.
- Added Terms and Conditions links to the dashboard, transcript page, and public pages.

### 09/06/26
- Added the public Terms and Conditions web page using `docs/terms_and_conditions.txt`.
- Styled the OAuth2 terms landing page and account-switch experience.
- Added automatic unblock links after terms acceptance.
- Added active single-use unblock-link management and copy controls.
- Added global user blocking for dashboard and slash-command access.
- Added warning records, moderation logs, warning-role removal, timeout removal, and warning DMs.
- Added the admin panel foundation and moderation/dashboard roadmap documentation.

### 09/05/26
- Added warning commands, warning presets, moderation access roles, and moderation user management.
- Added warning preset management to the dashboard.

### 09/04/26
- Restyled the dashboard and made public URLs domain-agnostic.
- Updated dashboard, rules, privacy, status, and documentation links to use Flask routes.

### 09/03/26
- Added Bloxlink verification support.
- Improved default ticket categories and ticket close-confirmation handling.
- Updated trial modifiers and ticket-category initialization.

### 09/02/26
- Added Trial Schedules to the dashboard.
- Added trial schedule saving, publishing, and current-rotation logic.
- Refactored trial schedule formatting and accessibility.

### 09/01/26
- Added dashboard analytics and analytics-role management.
- Added pacing for member fetching in the ticket system.

### 08/31/26
- Added TDS Level collection and placeholders to ticket forms and welcome messages.
- Added cooldown pruning and improved ticket welcome-message handling.

### 08/30/26
- Fixed Discord OAuth callback URL handling.
- Restored storage compatibility and durable Carry Rules and analytics APIs.
- Improved Discord Linked Roles metadata flow and error handling.

### 08/29/26
- Added Carry Rules agreement and Discord Linked Roles features.
- Added ticket analytics and staff-reply activity logging.
- Added level collection and related welcome-message settings.
- Expanded README deployment and configuration documentation.

### 08/28/26
- Added the Linked Roles requirement.

### 08/22/26
- Fixed role-based access so configured roles are required to execute protected commands.
- Added per-section role requirements.

### 08/20/26
- Added ticket dropdowns.
- Added Components V2 support.
- Released the initial ticket-system update.

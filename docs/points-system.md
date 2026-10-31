# Carry Team Points

The Points feature is guild-scoped and stores its configuration, member balances, award guards, and audit activity in MongoDB when MONGODB_URI is configured. It falls back to an atomic local JSON file only when MongoDB is not configured. A configured but unavailable MongoDB connection does not silently switch the feature to local storage.

## Commands

- /points-add user amount reason adds points.
- /points-subtract user amount reason subtracts points without allowing a negative balance.
- /remove-user user reason removes a user from the visible leaderboard and keeps an audit record.
- /exclude user reason prevents a member from earning automatic points while preserving their existing balance.
- /include user reason restores eligibility for future automatic awards.

The points-add and points-subtract command names avoid a collision because /add and /remove already manage ticket-channel access. Points command roles are configured in the Points dashboard. /exclude and /include are restricted to Carry Managers and administrators.

## Format Checker

All required sections are scanned and validated:

1. Carry team member mentions (one or more).
2. Exactly one carried-user mention.
3. Gamemode text.
4. Numeric ticket number.
5. Optional Notes.

Only section 1 determines recipients and section 3 determines the configured reward. Section 1 is the sole recipient source. Section 2 is validated but never independently awards points; if a user is also explicitly listed in section 1, section 1 remains the source of truth. Sections 2 and 4 and Notes never affect the points amount. Notes may be omitted. Invalid submissions are deleted and logged.

The gamemode in section 3 must match a dashboard gamemode entry exactly, ignoring letter case. An unconfigured gamemode does not award points. A ticket number can only be processed once per server; repeat submissions are logged and ignored.

## Automation

Thread Automation can create a thread from each message in configured channels. Format Checker submissions create a thread after the required format passes. Created threads request a 10,080-minute auto-archive interval (one week of inactivity). Discord permissions and server capabilities still apply; a failed thread creation is recorded in the audit history.

Format Checker reads message content. Enable Message Content Intent in the Discord Developer Portal and configure DISCORD_MESSAGE_CONTENT_INTENT=true in the hosting environment before enabling it. The application code intentionally does not turn on this privileged intent automatically.

## Audit logs

All Points actions are saved in dashboard audit history. Discord delivery is optional and independently configurable by channel and event. Each Discord audit notification uses a full Components V2 LayoutView with Container, TextDisplay, and Separator; it does not use traditional embeds. Events include manual balance changes, leaderboard removals, exclusions and inclusions, automatic awards/skips, duplicate ticket protection, Format Checker passes/rejections, thread creation/archiving/failures, and settings changes.

## Database indexes

MongoDB indexes support guild/user balance lookup, leaderboard ordering, recent activity and per-user activity, and unique guild/ticket award protection. Leaderboard and audit views use server-side pagination rather than loading every record into memory.

# Transcript presentation

The transcript archive list, public sample, and generated ticket archives share a dark liquid-glass visual theme with translucent panels, readable message spacing, attachment cards, and responsive layouts.

- The saved-transcript list keeps its existing 50-entry pagination and page-local name, user-ID, and ticket-number search.
- New archives embed their CSS and JavaScript, so downloaded HTML does not depend on the dashboard's static files. Message and embed text remain escaped; attachment and embed links accept HTTP(S) URLs only.
- The preview uses the same renderer as real transcripts and clearly marks its fictional conversation and attachment.
- Conversation search runs in the browser. Print includes the complete conversation even when search filters the screen. The full transcript remains readable when JavaScript is disabled or backdrop blur is unavailable.
- The viewer adds a compatibility stylesheet when serving this bot's previous HTML format. Stored HTML and conversation records are not rewritten; unrelated imported exporters are left unchanged.
- Existing OAuth, transcript-owner/staff checks, signed links, transcript retention, message limits, and storage remain in place.

No new Python dependencies, database migration, or Render environment variables are required. This presentation does not recover message fields that Discord did not return when an archive was originally captured.

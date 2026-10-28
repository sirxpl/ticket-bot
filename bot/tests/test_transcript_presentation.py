"""Portable transcript rendering and unchanged viewer authorization."""
import ast
import copy
from html.parser import HTMLParser
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from flask import Flask, Response, abort, flash, redirect, request, session, url_for

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.transcripts import render_preview_transcript, render_transcript, theme_saved_transcript


class Tags(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.tags = []
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


LEGACY = ('<!doctype html><html><head><title>Transcript - carry-42</title>'
          '<style>.discord-embed{background:#2b2d31}</style></head><body><div class="container">'
          '<div class="msg"><div class="content">A saved conversation &amp; its original text.</div></div>'
          '</div></body></html>')
MESSAGES = [{"author_name": "Example Member", "author_id": "42", "ts": "2026-10-28T16:30:00Z",
             "content": "Hello!\nThanks for helping.", "attachments": [{"url": "https://example.test/file.txt", "filename": "notes.txt"}],
             "embeds": [{"title": "Carry request", "description": "Standard carry", "color": 123,
                         "fields": [{"name": "Region", "value": "UTC", "inline": True}]}],
             "components": [[{"label": "Close", "style": "danger"},
                             {"label": "Guide", "style": "link", "url": "https://example.test/guide"}]]}]


def render(messages=None, **options):
    return render_transcript(MESSAGES if messages is None else messages, "carry-42",
        {"ticket_number": 42, "creator": {"name": "Example Member"}, "fields": {"timezone": "UTC"}},
        "2026-10-28T16:45:00Z", "ticket-42.html", **options)


class TranscriptRenderingTests(unittest.TestCase):
    def test_messages_embeds_attachments_and_archived_controls_are_preserved(self):
        document = render()
        for text in ("Hello!\nThanks for helping.", "Carry request", "Standard carry", "Region", "notes.txt", "Guide"):
            self.assertIn(text, document)
        tags = Tags(document).tags
        self.assertEqual(sum(tag == "article" and "data-tr-message" in attrs for tag, attrs in tags), 1)
        self.assertTrue(any(tag == "a" and attrs.get("href") == "https://example.test/file.txt" for tag, attrs in tags))
        self.assertTrue(any(tag == "span" and attrs.get("aria-disabled") == "true" for tag, attrs in tags))

    def test_untrusted_text_and_urls_are_not_interpreted_as_markup(self):
        payload = copy.deepcopy(MESSAGES)
        payload[0].update(author_name='<img src=x onerror="alert(1)">', content="<script>alert('content')</script>",
                          avatar_url="javascript:alert(1)")
        payload[0]["embeds"][0].update(title="<b>Injected</b>", url="javascript:alert(1)", color='0; background:url(bad)')
        payload[0]["attachments"][0]["url"] = "data:text/html,<script>alert(1)</script>"
        payload[0]["components"][0][1]["url"] = "javascript:alert(1)"
        document = render(payload)
        self.assertIn("&lt;script&gt;", document)
        self.assertIn("&lt;b&gt;Injected&lt;/b&gt;", document)
        for tag, attrs in Tags(document).tags:
            self.assertNotIn("onerror", attrs)
            for name in ("href", "src"):
                self.assertFalse(attrs.get(name, "").startswith(("javascript:", "data:")))

    def test_generated_html_contains_its_own_styles_and_script(self):
        tags = Tags(render()).tags
        self.assertFalse(any(tag == "link" and attrs.get("rel") == "stylesheet" for tag, attrs in tags))
        self.assertFalse(any(tag == "script" and attrs.get("src") for tag, attrs in tags))
        self.assertTrue(any(tag == "a" and attrs.get("download") == "ticket-42.html" and attrs.get("href") == "" for tag, attrs in tags))

    def test_interactive_controls_have_unique_ids(self):
        ids = [attrs["id"] for tag, attrs in Tags(render_preview_transcript()).tags if attrs.get("id")]
        self.assertEqual(len(ids), len(set(ids)))

    def test_rendering_does_not_mutate_saved_message_data(self):
        messages = copy.deepcopy(MESSAGES)
        before = copy.deepcopy(messages)
        render(messages)
        self.assertEqual(messages, before)

    def test_empty_history_and_omission_notice_are_clear(self):
        self.assertIn("No messages captured", render([]))
        self.assertIn("Older messages were omitted", render(omitted_messages=True))

    def test_time_metadata_is_readable_and_bad_legacy_times_do_not_crash(self):
        self.assertIn("16:30 UTC", render())
        messages = copy.deepcopy(MESSAGES)
        messages[0]["ts"] = "unknown time"
        self.assertIn("unknown time", render(messages))

    def test_preview_uses_same_assets_and_declares_sample_content(self):
        preview = render_preview_transcript()
        document = render()
        self.assertIn("fictional conversation", preview)
        self.assertIn("Sample attachment", preview)
        self.assertEqual(preview.split("<style>", 1)[1].split("</style>", 1)[0],
                         document.split("<style>", 1)[1].split("</style>", 1)[0])

    def test_existing_archives_are_themed_on_read_and_keep_original_content(self):
        themed = theme_saved_transcript(LEGACY)
        self.assertIn('id="transcript-glass-compatibility"', themed)
        self.assertIn("A saved conversation &amp; its original text.", themed)
        self.assertEqual(theme_saved_transcript(themed), themed)
        self.assertNotIn("transcript-glass-compatibility", LEGACY)

    def test_unrelated_imported_html_is_not_rewritten(self):
        document = "<html><head><title>Other exporter</title></head><body>Archive</body></html>"
        self.assertEqual(theme_saved_transcript(document), document)
        self.assertEqual(theme_saved_transcript(render()), render())

    def test_ticket_cog_uses_shared_renderer(self):
        source = ast.parse((ROOT / "cogs/tickets.py").read_text())
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "build_discord_like_transcript")
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "tickets.py", "exec"), namespace)
        document = namespace["build_discord_like_transcript"](MESSAGES, "carry-42", {}, "2026-10-28T16:45:00Z", "ticket-42.html")
        self.assertIn('data-transcript-theme="liquid-glass"', document)


class TranscriptViewerRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "transcript-tests"
        self.app.add_url_rule("/login", "login", lambda: "Login")
        self.app.add_url_rule("/dashboard/<page>", "home", lambda page: "Dashboard")
        self.storage = Mock(return_value=LEGACY)
        self.info = Mock(return_value={"filename": "ticket-42.html"})
        namespace = {"app": self.app, "Response": Response, "abort": abort, "flash": flash,
                     "redirect": redirect, "request": request, "session": session, "url_for": url_for,
                     "get_transcript_info": self.info,
                     "get_logs_for_ticket": lambda ticket: [{"action": "created", "creator": {"id": "owner"}}],
                     "get_member_role_ids": lambda user: [], "has_transcripts_access": lambda user, roles: user == "staff",
                     "get_transcript_html": self.storage, "theme_saved_transcript": theme_saved_transcript,
                     "render_preview_transcript": render_preview_transcript}
        source = ast.parse((ROOT / "main.py").read_text())
        routes = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in {"get_transcript", "preview_transcript"}]
        exec(compile(ast.Module(body=routes, type_ignores=[]), "main.py", "exec"), namespace)
        self.client = self.app.test_client()

    def sign_in(self, user):
        with self.client.session_transaction() as stored:
            stored["user"] = {"id": user}

    def test_anonymous_viewer_still_needs_oauth_and_return_url_is_preserved(self):
        with patch("utils.storage.verify_transcript_token", return_value={"filename": "ticket-42.html"}):
            response = self.client.get("/transcripts/ticket-42.html?token=valid")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)
        with self.client.session_transaction() as stored:
            self.assertEqual(stored["transcript_return_to"], "/transcripts/ticket-42.html?token=valid")
        self.storage.assert_not_called()

    def test_owner_and_authorized_staff_receive_themed_legacy_archive(self):
        for user in ("owner", "staff"):
            self.sign_in(user)
            response = self.client.get("/transcripts/ticket-42.html")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"transcript-glass-compatibility", response.data)
            self.assertTrue(response.content_type.startswith("text/html"))

    def test_other_members_cannot_read_transcript(self):
        self.sign_in("outsider")
        self.assertEqual(self.client.get("/transcripts/ticket-42.html").status_code, 403)
        self.storage.assert_not_called()

    def test_invalid_or_mismatched_signed_link_is_rejected(self):
        self.sign_in("owner")
        for decoded in (None, {"filename": "different.html"}):
            with patch("utils.storage.verify_transcript_token", return_value=decoded):
                self.assertEqual(self.client.get("/transcripts/ticket-42.html?token=invalid").status_code, 403)

    def test_missing_archive_still_returns_not_found(self):
        self.sign_in("staff")
        self.storage.return_value = None
        self.assertEqual(self.client.get("/transcripts/ticket-42.html").status_code, 404)

    def test_public_preview_is_available_without_authentication(self):
        response = self.client.get("/preview/transcript")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"fictional conversation", response.data)
        self.assertIn(b'data-transcript-theme="liquid-glass"', response.data)


if __name__ == "__main__":
    unittest.main()

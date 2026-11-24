"""Tests for archive-wide transcript search."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils import storage


class TranscriptArchiveSearchTests(unittest.TestCase):
    def setUp(self):
        self.filenames = [
            "ticket-100.html",
            "ticket-101.html",
            "ticket-102.html",
        ]
        self.logs = [
            {"action": "created", "ticket_id": 100, "ticket_number": 501,
             "creator": {"name": "ExampleMember", "id": "123456789012345678"}},
            {"action": "created", "ticket_id": 101, "ticket_number": 502,
             "creator": {"name": "OtherMember", "id": "987654321098765432"}},
            {"action": "created", "ticket_id": 102, "ticket_number": 503,
             "creator": {"name": "ThirdMember", "id": "111111111111111111"}},
        ]

    def search(self, query):
        with patch.object(storage, "get_db", return_value=None), \
             patch.object(storage, "list_transcript_filenames", return_value=self.filenames), \
             patch.object(storage, "get_ticket_logs", return_value=self.logs):
            return storage.search_transcripts(query)

    def test_searches_all_archived_ticket_numbers(self):
        rows, total = self.search("502")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["filename"], "ticket-101.html")

    def test_searches_member_names_case_insensitively(self):
        rows, total = self.search("examplemember")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["username"], "ExampleMember")

    def test_searches_discord_user_ids(self):
        rows, total = self.search("987654321098765432")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["ticket_number"], 502)

    def test_searches_ticket_ids_and_filename(self):
        rows, total = self.search("100")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["filename"], "ticket-100.html")

    def test_empty_query_returns_no_results(self):
        with patch.object(storage, "get_db", return_value=None):
            self.assertEqual(storage.search_transcripts("   "), ([], 0))

    def test_results_are_capped_but_total_is_preserved(self):
        with patch.object(storage, "get_db", return_value=None), \
             patch.object(storage, "list_transcript_filenames", return_value=self.filenames), \
             patch.object(storage, "get_ticket_logs", return_value=self.logs):
            rows, total = storage.search_transcripts("Member", limit=2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(total, 3)


if __name__ == "__main__":
    unittest.main()

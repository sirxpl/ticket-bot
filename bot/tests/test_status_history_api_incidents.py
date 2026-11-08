import copy
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

BOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

from utils import status_history


class StatusHistoryApiIncidentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.status_file = os.path.join(self.temp_dir.name, "status_history.json")
        with open(self.status_file, "w", encoding="utf-8") as handle:
            json.dump(copy.deepcopy(status_history._DEFAULTS), handle)
        self.db_patch = patch.object(status_history, "get_db", return_value=None)
        self.file_patch = patch.object(status_history, "STATUS_FILE", self.status_file)
        self.db_patch.start()
        self.file_patch.start()

    def tearDown(self):
        self.file_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_repeated_global_blocks_extend_one_open_incident(self):
        first_deadline = time.time() + 120
        first = status_history.record_api_block(first_deadline, "first block")
        second_deadline = time.time() + 300
        second = status_history.record_api_block(second_deadline, "extended block")

        data = status_history.get_status_data()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(data["incidents"]), 1)
        self.assertEqual(data["open_api_incident_id"], first["id"])
        self.assertGreaterEqual(data["api_block_until"], second_deadline)
        self.assertFalse(status_history.resolve_api_block_incident())

    def test_expired_api_block_incident_is_resolved(self):
        incident = status_history.record_api_block(time.time() - 1, "test block")

        self.assertTrue(status_history.resolve_api_block_incident())
        data = status_history.get_status_data()
        saved = next(item for item in data["incidents"] if item["id"] == incident["id"])
        self.assertIsNone(data["open_api_incident_id"])
        self.assertEqual(data["api_block_until"], 0.0)
        self.assertEqual(saved["updates"][-1]["status"], "resolved")


if __name__ == "__main__":
    unittest.main()

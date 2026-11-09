"""Per-server active ticket limits, message settings, and slot reservations."""

import concurrent.futures
from pathlib import Path
import unittest
from unittest.mock import patch

from test_carry_application_management import ApplicationFixture
from utils import storage


class TicketLimitStorageTests(ApplicationFixture, unittest.TestCase):
    def test_defaults_and_saved_message_are_scoped_to_each_server(self):
        first = storage.get_ticket_limit_config("42")
        second = storage.get_ticket_limit_config("84")
        self.assertEqual(first["limit"], 15)
        self.assertIn("{active_tickets}/{limit}", first["full_message"])

        saved = storage.save_ticket_limit_config(
            "42",
            "7",
            "Server {server} is full ({active_tickets}/{limit}).",
        )
        self.assertEqual(saved["limit"], 7)
        self.assertEqual(storage.get_ticket_limit_config("42"), saved)
        self.assertEqual(storage.get_ticket_limit_config("84")["limit"], 15)

    def test_invalid_limits_and_oversized_messages_are_rejected(self):
        for value in ("", "abc", "1.5", "0", "-1", "10001", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                storage.save_ticket_limit_config("42", value, "Full")
        with self.assertRaises(ValueError):
            storage.save_ticket_limit_config("42", 15, "x" * 1801)

    def test_save_preserves_unrelated_bot_settings(self):
        storage.save_settings({
            "tickets_enabled": False,
            "carry_application_config": {"accepting_responses": False},
            "some_other_setting": "kept",
        })
        storage.save_ticket_limit_config("42", 20, "Tickets are full.")
        saved = storage.get_settings()
        self.assertFalse(saved["tickets_enabled"])
        self.assertFalse(saved["carry_application_config"]["accepting_responses"])
        self.assertEqual(saved["some_other_setting"], "kept")
        self.assertEqual(saved["ticket_limits"]["42"]["limit"], 20)

    def test_mongo_reservations_enforce_the_cap_per_server_and_finalize(self):
        first = storage.reserve_active_ticket_slot("42", "100", 1)
        self.assertTrue(first)
        self.assertEqual(storage.get_active_ticket_count("42"), 1)
        self.assertIsNone(storage.reserve_active_ticket_slot("42", "200", 1))

        # A different server has its own capacity.
        other_server = storage.reserve_active_ticket_slot("84", "200", 1)
        self.assertTrue(other_server)
        self.assertEqual(storage.get_active_ticket_count("84"), 1)

        self.assertTrue(storage.finalize_active_ticket_reservation(
            first, "9001", "9001", "42", "100", 1
        ))
        self.assertEqual(storage.get_active_ticket_count("42"), 1)
        self.assertEqual(storage.get_active_ticket_for_user("100")["channel_id"], "9001")
        self.assertFalse(storage.release_active_ticket_reservation(first))

        # Closing/removing the active record immediately restores capacity.
        storage.remove_active_ticket("9001")
        self.assertEqual(storage.get_active_ticket_count("42"), 0)
        self.assertTrue(storage.release_active_ticket_reservation(other_server))
        self.assertEqual(storage.get_active_ticket_count("84"), 0)

    def test_same_member_cannot_claim_two_slots_in_one_server(self):
        first = storage.reserve_active_ticket_slot("42", "100", 5)
        self.assertTrue(first)
        self.assertIsNone(storage.reserve_active_ticket_slot("42", "100", 5))
        self.assertEqual(storage.get_active_ticket_count("42"), 1)
        self.assertTrue(storage.release_active_ticket_reservation(first))

    def test_cooldown_updates_do_not_overwrite_active_ticket_reservations(self):
        reservation = storage.reserve_active_ticket_slot("42", "100", 2)
        self.assertTrue(reservation)

        storage.add_cooldown("200", hours=1)

        self.assertEqual(storage.get_active_ticket_count("42"), 1)
        self.assertTrue(storage.release_active_ticket_reservation(reservation))

    def test_legacy_tickets_are_assigned_to_their_cached_server(self):
        self.db.tickets_data.insert_one({
            "_id": "singleton",
            "ticket_counter": 1,
            "active_tickets": [
                {"ticket_id": "111", "channel_id": "111", "user_id": "100"},
                {"ticket_id": "222", "channel_id": "222", "user_id": "200"},
            ],
            "cooldowns": [],
        })
        changed = storage.migrate_legacy_ticket_guild_ids({
            "111": "42",
            "222": "84",
        })
        self.assertEqual(changed, 2)
        self.assertEqual(storage.get_active_ticket_count("42"), 1)
        self.assertEqual(storage.get_active_ticket_count("84"), 1)


class FileTicketLimitStorageTests(ApplicationFixture, unittest.TestCase):
    use_mongo = False

    def setUp(self):
        super().setUp()
        self.start_patch(patch.object(
            storage,
            "TICKETS_FILE",
            str(Path(self.temporary.name) / "tickets.json"),
        ))

    def test_concurrent_reservations_never_exceed_the_file_storage_limit(self):
        def reserve(index):
            return storage.reserve_active_ticket_slot("42", str(index + 1), 3)

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            reservations = list(pool.map(reserve, range(12)))

        successful = [reservation for reservation in reservations if reservation]
        self.assertEqual(len(successful), 3)
        self.assertEqual(storage.get_active_ticket_count("42"), 3)
        for reservation in successful:
            self.assertTrue(storage.release_active_ticket_reservation(reservation))
        self.assertEqual(storage.get_active_ticket_count("42"), 0)

    def test_failed_creation_reservation_can_be_released(self):
        reservation = storage.reserve_active_ticket_slot("42", "100", 1)
        self.assertTrue(reservation)
        self.assertEqual(storage.get_active_ticket_count("42"), 1)
        self.assertTrue(storage.release_active_ticket_reservation(reservation))
        self.assertEqual(storage.get_active_ticket_count("42"), 0)


if __name__ == "__main__":
    unittest.main()

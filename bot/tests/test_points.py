"""Unit tests for Points validation, storage, leaderboard, and award protections."""
import os
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils import points


class PointsStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(points, "POINTS_FILE", str(Path(self.temporary.name) / "points.json"))
        self.path_patch.start()
        self.db_patch = patch.object(points, "get_db", return_value=None)
        self.db_patch.start()
        self.env_patch = patch.dict(os.environ, {"MONGODB_URI": ""})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.db_patch.stop()
        self.path_patch.stop()
        self.temporary.cleanup()

    def test_access_control_and_points_templates_compile(self):
        from jinja2 import Environment, FileSystemLoader
        templates = Path(__file__).resolve().parents[1] / "templates"
        env = Environment(loader=FileSystemLoader(str(templates)))
        env.get_template("dashboard.html")
        env.get_template("dashboard_pages/access_control.html")
        env.get_template("dashboard_pages/_points_settings.html")
        env.get_template("dashboard_pages/points.html")

    def test_discord_points_audit_is_a_full_components_v2_layout(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        import discord
        from cogs.points import PointsCog

        channel = SimpleNamespace(send=AsyncMock())
        bot = SimpleNamespace(get_channel=Mock(return_value=channel), fetch_channel=AsyncMock())
        cog = PointsCog(bot)
        config = points.default_points_config()
        config["logging"] = {"enabled": True, "channel_id": "555", "events": ["manual_add"]}

        async def deliver():
            with patch.object(points, "get_points_config", return_value=config):
                await cog._deliver_log(
                    SimpleNamespace(id=777), "manual_add",
                    user_id="101", username="CarryOne", amount=100,
                    points_after=100, reason="Test award", actor_id="900",
                    actor_name="Manager",
                )

        asyncio.run(deliver())
        channel.send.assert_awaited_once()
        kwargs = channel.send.await_args.kwargs
        self.assertIsInstance(kwargs["view"], discord.ui.LayoutView)
        self.assertNotIn("embed", kwargs)
        self.assertIsNone(kwargs.get("content"))

    def test_default_config_is_independent_and_contains_full_v2_log_event_selection(self):
        first = points.default_points_config()
        first["logging"]["events"].clear()
        second = points.default_points_config()
        self.assertIn("manual_add", second["logging"]["events"])
        self.assertIn("automatic_awarded", second["logging"]["events"])
        self.assertIn("format_rejected", second["logging"]["events"])

    def test_config_normalizes_gamemode_text_and_rejects_duplicate_casefolded_names(self):
        config = points.normalize_points_config({
            "gamemodes": [
                {"text": "Fallen", "points": "100", "reason": "Standard"},
                {"text": "Hardcore", "points": 250, "reason": "Hard"},
            ],
        })
        self.assertEqual(config["gamemodes"][0]["points"], 100)
        with self.assertRaises(ValueError):
            points.normalize_points_config({
                "gamemodes": [
                    {"text": "Fallen", "points": 100},
                    {"text": "fallen", "points": 200},
                ],
            })

    def test_gamemode_match_is_case_insensitive_but_exact(self):
        config = points.normalize_points_config({
            "gamemodes": [{"text": "Molten", "points": 75, "reason": "Carry"}],
        })
        self.assertEqual(points.find_gamemode_rule(config, "mOlTeN")["points"], 75)
        self.assertIsNone(points.find_gamemode_rule(config, "Molten Boss"))

    def test_valid_format_uses_section_one_and_three_for_points(self):
        parsed, error = points.validate_format(
            "1. <@101> <@!102>\n"
            "2. <@103>\n"
            "3. Fallen\n"
            "4. 2011\n"
            "Notes: Nice run"
        )
        self.assertIsNone(error)
        self.assertEqual(parsed["recipients"], ["101", "102"])
        self.assertEqual(parsed["carried_user_id"], "103")
        self.assertEqual(parsed["gamemode"], "Fallen")
        self.assertEqual(parsed["ticket_number"], "2011")
        self.assertEqual(parsed["notes"], "Nice run")

    def test_section_one_is_the_only_recipient_source_even_if_section_two_repeats_a_user(self):
        parsed, error = points.validate_format(
            "1. <@101> <@103>\n2. <@103>\n3. Fallen\n4. 2011"
        )
        self.assertIsNone(error)
        self.assertEqual(parsed["recipients"], ["101", "103"])

    def test_optional_notes_can_be_omitted_or_empty(self):
        for content in (
            "1. <@101>\n2. <@102>\n3. Fallen\n4. 2011",
            "1. <@101>\n2. <@102>\n3. Fallen\n4. 2011\nNotes:",
        ):
            with self.subTest(content=content):
                parsed, error = points.validate_format(content)
                self.assertIsNone(error)
                self.assertIsNone(parsed["notes"])

    def test_format_checker_validates_all_four_sections_and_notes_prefix(self):
        invalid = [
            "1. <@101>\n3. Fallen\n4. 2011",
            "1. <@101>\n2. carried user\n3. Fallen\n4. 2011",
            "1. <@101>\n2. <@102>\n3. Fallen\n4. ticket-2011",
            "1. <@101>\n2. <@102>\n3. Fallen\n4. 2011\nextra text",
        ]
        for content in invalid:
            with self.subTest(content=content):
                parsed, error = points.validate_format(content)
                self.assertIsNone(parsed)
                self.assertTrue(error)

    def test_duplicate_mentions_in_first_section_are_only_counted_once(self):
        parsed, error = points.validate_format(
            "1. <@101> <@101> <@102>\n2. <@103>\n3. Fallen\n4. 2011"
        )
        self.assertIsNone(error)
        self.assertEqual(parsed["recipients"], ["101", "102"])

    def test_manual_adjustments_are_audited_and_subtraction_cannot_make_negative_balance(self):
        added = points.adjust_points("guild", "101", "CarryOne", "add", 100, "Award", "900", "Manager")
        self.assertEqual(added["points"], 100)
        subtracted = points.adjust_points("guild", "101", "CarryOne", "subtract", 150, "Correction", "900", "Manager")
        self.assertEqual(subtracted["points"], 0)
        activity = points.get_points_activity("guild")
        self.assertEqual(activity["total"], 2)
        self.assertEqual(activity["items"][0]["action"], "manual_subtract")
        self.assertEqual(activity["items"][0]["amount"], -150)
        self.assertEqual(activity["items"][1]["action"], "manual_add")

    def test_set_balance_allows_zero_and_requires_reason(self):
        result = points.adjust_points("guild", "101", "CarryOne", "set", 0, "Reset")
        self.assertEqual(result["points"], 0)
        with self.assertRaises(ValueError):
            points.adjust_points("guild", "101", "CarryOne", "add", 10, "")

    def test_exclusion_preserves_balance_and_skips_automatic_award(self):
        points.adjust_points("guild", "101", "CarryOne", "add", 50, "Initial")
        points.set_user_excluded("guild", "101", "CarryOne", True, "Manager request")
        result = points.award_auto_points("guild", "2011", "Fallen", ["101"], 100, "Fallen reward")
        self.assertEqual(result["awarded"], [])
        self.assertEqual(result["excluded"], ["101"])
        member = points.get_points_leaderboard("guild")["items"][0]
        self.assertEqual(member["points"], 50)
        self.assertTrue(member["excluded"])

    def test_inclusion_allows_future_awards_again(self):
        points.set_user_excluded("guild", "101", "CarryOne", True)
        points.set_user_excluded("guild", "101", "CarryOne", False)
        result = points.award_auto_points("guild", "2012", "Fallen", ["101"], 75, "Fallen reward")
        self.assertEqual(result["awarded"], ["101"])
        self.assertEqual(points.get_points_leaderboard("guild")["items"][0]["points"], 75)

    def test_ticket_number_award_is_idempotent(self):
        first = points.award_auto_points("guild", "2011", "Fallen", ["101", "102"], 100, "Fallen reward")
        second = points.award_auto_points("guild", "2011", "Fallen", ["101", "102"], 100, "Fallen reward")
        self.assertEqual(first["awarded"], ["101", "102"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(points.get_points_leaderboard("guild")["total"], 2)
        self.assertEqual(points.get_points_activity("guild")["total"], 2)

    def test_remove_user_hides_member_from_leaderboard_and_keeps_audit_history(self):
        points.adjust_points("guild", "101", "CarryOne", "add", 500, "Top-up")
        points.remove_points_user("guild", "101", "CarryOne", "Staff decision", "900", "Manager")
        self.assertEqual(points.get_points_leaderboard("guild")["total"], 0)
        saved = next(row for row in points._load_local()["users"] if row["user_id"] == "101")
        self.assertEqual(saved["points"], 500)
        self.assertTrue(saved["hidden"])
        history = points.get_points_activity("guild")
        self.assertEqual(history["items"][0]["action"], "leaderboard_removed")

    def test_leaderboard_search_and_pagination_are_scoped_and_sorted(self):
        for user_id, name, amount in [
            ("101", "Alpha", 50), ("102", "Bravo", 150), ("103", "Charlie", 100),
        ]:
            points.adjust_points("guild", user_id, name, "add", amount, "Test")
        page = points.get_points_leaderboard("guild", page=1)
        self.assertEqual([row["user_id"] for row in page["items"]], ["102", "103", "101"])
        self.assertEqual(points.get_points_leaderboard("guild", query="char")["items"][0]["user_id"], "103")
        self.assertEqual(points.get_points_leaderboard("other")["total"], 0)

    def test_thread_creation_failures_are_supported_audit_events(self):
        points.record_event("guild", "thread_failed", details="Missing permissions")
        result = points.get_points_activity("guild", action="thread_failed")
        self.assertEqual(result["total"], 1)

    def test_event_filter_is_validated_and_paginated(self):
        points.record_event("guild", "format_rejected", user_id="101", reason="Bad format")
        points.record_event("guild", "thread_created", user_id="101", details="Created")
        selected = points.get_points_activity("guild", action="format_rejected")
        self.assertEqual(selected["total"], 1)
        self.assertEqual(selected["items"][0]["action"], "format_rejected")
        invalid = points.get_points_activity("guild", action="unknown_action")
        self.assertEqual(invalid["total"], 2)


if __name__ == "__main__":
    unittest.main()

"""Blacklist persistence, role lifecycle and Components V2 close-button regressions."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_carry_application_management import ApplicationFixture, ADMIN, MANAGER
import test_dashboard_management as dashboard_tests
from utils import access, blacklists, storage


class BlacklistChecks:
    def setUp(self):
        super().setUp()
        self.start_patch(patch.object(storage, "BLACKLIST_FILE", str(Path(self.temporary.name) / "blacklist.json")))
        self.start_patch(patch.object(access, "ACCESS_FILE", str(Path(self.temporary.name) / "access.json")))

    def test_records_survive_reload_and_removal_without_legacy_resurrection(self):
        Path(storage.BLACKLIST_FILE).write_text(json.dumps({"blacklisted_users": ["123"]}))
        self.assertEqual(storage.get_blacklist_data()["blacklisted_users"][0]["user_id"], "123")
        self.assertTrue(storage.remove_from_blacklist("123"))
        self.assertEqual(storage.get_blacklist_data()["blacklisted_users"], [])
        storage.add_to_blacklist("456", reason="Saved", blacklist_type="custom", role_id="789", block_all=False)
        self.assertEqual(storage.get_blacklist_data()["blacklisted_users"][0]["role_id"], "789")
        if self.db is not None:
            Path(storage.BLACKLIST_FILE).unlink()
            self.assertEqual(storage.get_blacklist_data()["blacklisted_users"][0]["user_id"], "456")

    def test_expired_entries_are_not_enforced_but_are_available_for_role_cleanup(self):
        with patch.object(blacklists.time, "time", return_value=1000):
            storage.add_to_blacklist("123", hours=1)
        with patch.object(blacklists.time, "time", return_value=4601):
            self.assertEqual(storage.get_blacklist_data()["blacklisted_users"], [])
            self.assertEqual(len(storage.get_blacklist_data(include_expired=True)["blacklisted_users"]), 1)

    def test_invalid_durations_and_duplicate_blacklists_do_not_overwrite_assigned_role(self):
        for hours in (-1, 0, float("inf"), float("nan"), 87601):
            with self.assertRaises(ValueError):
                storage.add_to_blacklist("123", hours=hours)
        storage.add_to_blacklist("123", role_id="555")
        with self.assertRaises(ValueError):
            storage.add_to_blacklist("123", role_id="666")
        self.assertEqual(storage.get_blacklist_data()["blacklisted_users"][0]["role_id"], "555")

    def test_profile_changes_preserve_other_permissions_and_existing_member_snapshot(self):
        storage.add_to_blacklist("123", blacklist_type="voidcore", role_id="555", block_all=False)
        blacklists.save_types([dict(id="voidcore", name="Voidcore", role_id="666", reason="New reason", block_all=True)])
        saved = storage.get_blacklist_data()["blacklisted_users"][0]
        self.assertEqual(blacklists.role_id(saved), "555")
        blacklists.save_types([])
        self.assertEqual(blacklists.get_types(), [])
        self.assertEqual(blacklists.role_id(saved), "555")

    def test_profile_validation_requires_unique_name_and_role_but_reason_is_optional(self):
        row = dict(name="Example", role_id="123", reason="", enabled=True)
        for rows in (
            [row, row],
            [{**row, "role_id": ""}],
            [{**row, "role_id": "$bad"}],
            [{**row, "name": ""}],
            [{**row, "reason": "x" * 501}],
            [row] * 26,
        ):
            with self.assertRaises(ValueError):
                blacklists.save_types(rows)
        saved = blacklists.save_types([row])
        self.assertEqual(saved[0]["reason"], "")
        self.assertTrue(saved[0]["enabled"])

    def test_legacy_records_are_preserved_and_flagged_for_review(self):
        storage.add_to_blacklist("123", role_id="555", blacklist_type="voidcore", reason="Old reason")
        storage.add_to_blacklist("456", reason="No matching role")
        profiles = [dict(role_id="555", name="Migrated role", enabled=True)]
        reviewed = blacklists.legacy_review_entries(
            storage.get_blacklist_data(include_expired=True)["blacklisted_users"],
            configured_profiles=profiles,
        )
        self.assertEqual([row["review_status"] for row in reviewed], ["mapped", "needs_review"])
        self.assertEqual(len(storage.get_blacklist_data(include_expired=True)["blacklisted_users"]), 2)
        self.assertEqual(reviewed[0]["reason"], "Old reason")

    def test_role_removal_failure_keeps_blacklist_for_retry(self):
        async def check():
            role = SimpleNamespace(id=555)
            member = SimpleNamespace(id=123, roles=[role], guild=SimpleNamespace(id=42, get_role=Mock(return_value=role)), remove_roles=AsyncMock(side_effect=RuntimeError("permissions")))
            storage.add_to_blacklist("123", role_id="555")
            saved = storage.get_blacklist_data()["blacklisted_users"][0]
            with self.assertRaises(RuntimeError):
                await blacklists.remove_member(member, saved, "Test")
            self.assertEqual(len(storage.get_blacklist_data()["blacklisted_users"]), 1)
            member.remove_roles.side_effect = None
            await blacklists.remove_member(member, saved, "Test")
            self.assertEqual(storage.get_blacklist_data()["blacklisted_users"], [])
        asyncio.run(check())

    def test_stale_removal_cannot_delete_a_replacement_blacklist(self):
        storage.add_to_blacklist("123", role_id="555")
        saved = storage.get_blacklist_data()["blacklisted_users"][0]
        storage.remove_from_blacklist("123")
        storage.add_to_blacklist("123", role_id="666")
        with self.assertRaises(RuntimeError):
            blacklists.remove("123", expected_entry=saved)
        self.assertEqual(storage.get_blacklist_data()["blacklisted_users"][0]["role_id"], "666")

    def test_no_presets_and_preexisting_flat_roles_supply_command_choices(self):
        self.assertEqual(blacklists.get_types(), [])
        access.add_blacklist_role("555")
        self.assertEqual([row["role_id"] for row in blacklists.get_types()], ["555"])
        # Old automatically seeded choices must not return when settings are read.
        settings = access.get_access_settings()
        settings["blacklist_types"] = [
            dict(id="regular", name="Regular", role_id="", reason="Old", block_all=True),
            dict(id="voidcore", name="Voidcore", role_id=blacklists.LEGACY_VOIDCORE_ROLE, reason="Old", block_all=False),
            dict(id="custom", name="Custom", role_id="666", reason="Configured reason", block_all=False)]
        access._save(settings)
        self.assertEqual({row["role_id"] for row in blacklists.get_types()}, {"555", "666"})
        member = SimpleNamespace(id=123, roles=[SimpleNamespace(id=666)])
        self.assertIsNone(blacklists.restriction_reason(member, {"blacklist_roles": []}))
        self.assertEqual(blacklists.restriction_reason(member, {"blacklist_roles": ["666"]}), "Configured reason")
        blacklists.save_types([dict(name="Disabled role", role_id="666", reason="", enabled=False)])
        self.assertIsNone(blacklists.restriction_reason(member, None))
        self.assertFalse(blacklists.get_types()[0]["enabled"])

    def test_editor_updates_the_enabled_global_role_list(self):
        access.add_blacklist_role("555")
        blacklists.save_types([dict(name="Global ban", role_id="666", reason="", enabled=True)])
        self.assertEqual(access.get_blacklist_role_ids(), ["666"])
        blacklists.save_types([dict(name="Global ban", role_id="666", reason="", enabled=False)])
        self.assertEqual(access.get_blacklist_role_ids(), [])
        self.assertEqual(blacklists.get_types()[0]["role_id"], "666")
        blacklists.save_types([])
        self.assertEqual(blacklists.get_types(), [])

    def test_configured_database_outage_does_not_fall_back_to_ephemeral_blacklists(self):
        with patch.dict(os.environ, {"MONGODB_URI": "mongodb://configured"}), patch.object(storage, "get_db", return_value=None):
            with self.assertRaises(RuntimeError):
                storage.add_to_blacklist("123")
            with self.assertRaises(RuntimeError):
                storage.get_blacklist_data()


class MongoBlacklistTests(BlacklistChecks, ApplicationFixture, unittest.TestCase):
    pass


class FileBlacklistTests(BlacklistChecks, ApplicationFixture, unittest.TestCase):
    use_mongo = False


class TicketClosePanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.module = importlib.import_module("cogs.tickets")

    async def test_welcome_is_v2_with_exactly_one_persistent_close_button(self):
        import discord
        panel = self.module.welcome_panel(object(), discord.Embed(title="Created", description="Answers"), "@opener")
        buttons = [item for item in panel.walk_children() if isinstance(item, discord.ui.Button)]
        self.assertIsInstance(panel, discord.ui.LayoutView)
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].custom_id, "ticket_close_btn")
        self.assertTrue(panel.is_persistent())
        self.assertIn("Answers", str(panel.to_components()))
        self.assertIsInstance(panel.children[0], discord.ui.TextDisplay)
        self.assertEqual(panel.children[0].content, "@opener")
        box = next(item for item in panel.children if isinstance(item, discord.ui.Container))
        status = next(item for item in box.children if isinstance(item, discord.ui.TextDisplay) and item.id == 9001)
        self.assertEqual(status.content, "**Status**\nOpen - you may close this ticket")

    async def test_owner_button_explains_ongoing_then_allows_close_after_end(self):
        cog = object()
        bot = SimpleNamespace(get_cog=Mock(return_value=cog))
        interaction = SimpleNamespace(user=SimpleNamespace(id=123), channel=SimpleNamespace(id=42),
                                      response=SimpleNamespace(send_message=AsyncMock()))
        ticket = {"user_id": "123", "owner_close_locked": True}
        with patch.object(self.module, "get_active_ticket", return_value=ticket):
            await self.module.CloseConfirmView(bot).close_button.callback(interaction)
            self.assertIn("still ongoing", interaction.response.send_message.await_args.args[0])
            self.assertNotIn("view", interaction.response.send_message.await_args.kwargs)
            ticket["owner_close_locked"] = False
            await self.module.CloseConfirmView(bot).close_button.callback(interaction)
            self.assertIsInstance(interaction.response.send_message.await_args.kwargs["view"], self.module.ConfirmView)

    async def test_pending_confirmation_cannot_bypass_new_start_lock(self):
        cog = SimpleNamespace(do_close=AsyncMock())
        channel = SimpleNamespace(id=42)
        interaction = SimpleNamespace(user=SimpleNamespace(id=123), response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(self.module, "get_active_ticket", return_value={"user_id": "123", "owner_close_locked": True}):
            await self.module.ConfirmView(cog, channel).confirm.callback(interaction)
        cog.do_close.assert_not_awaited()

    async def test_main_status_refresh_restores_callback_without_adding_buttons(self):
        import discord
        from discord.components import _component_factory
        panel = self.module.welcome_panel(object(), discord.Embed(title="Created"))
        box = next(item for item in panel.children if isinstance(item, discord.ui.Container))
        status = next(item for item in box.children if isinstance(item, discord.ui.TextDisplay) and item.id == 9001)
        box.remove_item(status)
        panel.add_item(status)
        message = SimpleNamespace(components=[_component_factory(item) for item in panel.to_components()], edit=AsyncMock())
        channel = SimpleNamespace(id=42, fetch_message=AsyncMock(return_value=message))
        with patch.object(self.module, "get_active_ticket", return_value={"welcome_message_id": "99"}):
            for locked, text in [(True, "Still ongoing"), (False, "Open - you may close this ticket")]:
                await self.module.refresh_ticket_status(object(), channel, locked)
                view = message.edit.await_args.kwargs["view"]
                self.assertIn(text, str(view.to_components()))
                box = next(item for item in view.children if isinstance(item, discord.ui.Container))
                self.assertTrue(any(item.id == 9001 for item in box.children))
                self.assertEqual(len([item for item in view.walk_children() if isinstance(item, discord.ui.Button)]), 1)
                button = next(item for item in view.walk_children() if isinstance(item, discord.ui.Button))
                self.assertEqual(type(button.callback), type(self.module.CloseConfirmView(object()).close_button.callback))


class BlacklistEditorRoutes(dashboard_tests.DashboardFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.main = importlib.import_module("main")
        self.main.app.config.update(TESTING=True, SECRET_KEY="blacklist-tests")
        self.client = self.main.app.test_client()
        self.seed_access({"carry_manager_roles": ["777"], "allowed_roles": ["101"]})
        self.start_patch(patch.object(self.main, "remember_base_url"))
        self.start_patch(patch.object(self.main, "get_member_role_ids", return_value=[]))
        self.start_patch(patch.object(storage, "BLACKLIST_FILE", str(Path(self.temporary.name) / "blacklist.json")))
        self.sign_in(ADMIN)

    sign_in = dashboard_tests.DashboardManagementRouteTests.sign_in

    # Reuse the authenticated app fixture and existing route checks.
    def test_type_editor_is_admin_only_and_requires_csrf(self):
        data = {"csrf_token": "test-csrf", "blacklist_name": ["Custom restriction"],
                "blacklist_role": ["123"], "blacklist_reason": [""], "blacklist_enabled": ["true"],
                "blacklist_scope": ["all"]}
        self.assertEqual(self.client.post("/dashboard/access/blacklist-types", data={**data, "csrf_token": "wrong"}).status_code, 400)
        self.assertEqual(self.client.post("/dashboard/access/blacklist-types", data=data).status_code, 302)
        self.assertEqual(blacklists.get_types()[0]["role_id"], "123")
        self.assertEqual(blacklists.get_types()[0]["reason"], "")
        self.assertTrue(blacklists.get_types()[0]["enabled"])
        self.sign_in(MANAGER)
        self.client.post("/dashboard/access/blacklist-types", data={"csrf_token": "test-csrf"})
        self.assertEqual(len(blacklists.get_types()), 1)

    def test_existing_category_scope_survives_blacklist_editor_save(self):
        data = {
            "csrf_token": "test-csrf",
            "blacklist_name": ["Existing category rule"],
            "blacklist_role": ["456"],
            "blacklist_reason": [""],
            "blacklist_enabled": ["true"],
            "blacklist_scope": ["categories"],
        }
        response = self.client.post("/dashboard/access/blacklist-types", data=data)
        self.assertEqual(response.status_code, 302)
        profile = blacklists.get_types()[0]
        self.assertFalse(profile["block_all"])
        member = SimpleNamespace(id=123, roles=[SimpleNamespace(id=456)])
        self.assertIsNone(blacklists.restriction_reason(member, {"blacklist_roles": []}))
        self.assertIsNotNone(blacklists.restriction_reason(member, {"blacklist_roles": ["456"]}))

    def test_access_control_has_one_logging_editor_and_preserves_channel_settings(self):
        self.db.access_control.update_one({"_id": "singleton"}, {"$set": {"blacklist_log_channel_id": "999"}})
        result = self.client.get("/dashboard/access-control")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data.count(b'name="blacklist_log_channel_id"'), 1)
        self.assertIn(b'Saved channel', result.data)
        self.assertIn(b'id="blacklist-types-form"', result.data)
        self.assertIn(b'id="access-loggings"', result.data)

    def test_user_targeted_unblacklist_endpoint_is_retired_and_record_is_preserved(self):
        storage.add_to_blacklist("123", role_id="555", guild_id="42", reason="Legacy record")
        response = self.client.post("/api/unblacklist/123", data={"csrf_token": "test-csrf"})
        self.assertEqual(response.status_code, 404)
        records = storage.get_blacklist_data(include_expired=True)["blacklisted_users"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["user_id"], "123")
        self.assertEqual(records[0]["role_id"], "555")


class RetiredUserBlacklistWorkflowTests(unittest.TestCase):
    def test_user_targeted_commands_and_expiry_watcher_are_not_registered(self):
        module = importlib.import_module("cogs.tickets")
        self.assertFalse(hasattr(module.TicketsCog, "add_ticket_blacklist_command"))
        self.assertFalse(hasattr(module.TicketsCog, "remove_ticket_blacklist_command"))
        self.assertFalse(hasattr(module.TicketsCog, "blacklist_expiry_watcher"))


class TicketClosingRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_does_not_announce_ongoing_after_close_has_already_started(self):
        module = importlib.import_module("cogs.tickets")
        cog = SimpleNamespace(_closing_ticket_ids={"42"}, _basic_command_check=AsyncMock(return_value=True))
        interaction = SimpleNamespace(channel=SimpleNamespace(id=42), response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(module, "set_ticket_owner_close_locked") as saved:
            await module.TicketsCog.start_ticket_command.callback(cog, interaction)
        saved.assert_not_called()
        self.assertIn("already closing", interaction.response.send_message.await_args.args[0])

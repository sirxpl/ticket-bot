"""Native Pin Messages grants for new/existing tickets and persisted removal work."""
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_dashboard_management as dashboard_tests
from test_carry_application_management import ApplicationFixture, ADMIN, MANAGER
from utils import access, pin_permissions as pins


class PinConfigurationChecks:
    def setUp(self):
        super().setUp()
        self.start_patch(patch.object(access, "ACCESS_FILE", str(Path(self.temporary.name) / "access.json")))

    def test_offline_removal_is_persisted_until_existing_tickets_can_be_updated(self):
        self.assertEqual(pins.change_roles(add=["555"]), 1)
        self.assertEqual(pins.change_roles(remove=["555"]), 1)
        saved = access.get_access_settings()
        self.assertEqual(saved["pin_message_roles"], [])
        self.assertEqual(saved["pin_permission_pending_removals"], ["555"])
        pins.change_roles(add=["555"])
        self.assertEqual(access.get_access_settings()["pin_permission_pending_removals"], [])

    def test_bulk_removal_tracks_only_previously_selected_roles(self):
        pins.change_roles(add=["555", "666"])
        self.assertEqual(pins.change_roles(remove=["555", "999"]), 1)
        saved = access.get_access_settings()
        self.assertEqual(saved["pin_message_roles"], ["666"])
        self.assertEqual(saved["pin_permission_pending_removals"], ["555"])

    def test_old_sync_cannot_clear_newer_pending_role_removals(self):
        pins.change_roles(add=["555", "666"])
        pins.change_roles(remove=["555"])
        snapshot = access.get_access_settings()
        pins.change_roles(remove=["666"])
        pins._clear_pending(snapshot)
        self.assertEqual(access.get_access_settings()["pin_permission_pending_removals"], ["555", "666"])

    def test_invalid_role_or_database_failure_does_not_save_ephemeral_permissions(self):
        for values in ([], ["bad"]):
            with self.assertRaises(ValueError):
                pins.change_roles(add=values)
        with patch.dict(pins.access.os.environ, {"MONGODB_URI": "mongodb://configured"}), patch.object(access, "get_db", return_value=None):
            with self.assertRaises(RuntimeError):
                pins.change_roles(add=["555"])
        self.assertNotIn("555", pins.selected_roles())


class MongoPinConfigurationTests(PinConfigurationChecks, ApplicationFixture, unittest.TestCase):
    pass


class FilePinConfigurationTests(PinConfigurationChecks, ApplicationFixture, unittest.TestCase):
    use_mongo = False


class NewTicketNativePinTests(unittest.TestCase):
    def test_grants_only_native_pin_and_preserves_existing_viewer_permissions(self):
        role = Mock(id=555)
        guild = SimpleNamespace(get_role=Mock(return_value=role))
        existing = discord.PermissionOverwrite(view_channel=True, send_messages=False, manage_messages=False)
        original = existing.pair()
        overwrites = {role: existing}
        with patch.object(access, "get_access_settings", return_value={"pin_message_roles": ["555"]}):
            self.assertEqual(pins.apply_new_ticket_overwrites(guild, overwrites), ["555"])
        self.assertTrue(overwrites[role].pin_messages)
        self.assertTrue(overwrites[role].view_channel)
        self.assertFalse(overwrites[role].send_messages)
        self.assertFalse(overwrites[role].manage_messages)
        self.assertEqual(existing.pair(), original)

    def test_pin_role_without_ticket_access_gets_no_view_send_or_manage_messages_grant(self):
        role = Mock(id=555)
        overwrites = {}
        with patch.object(access, "get_access_settings", return_value={"pin_message_roles": ["555"]}):
            pins.apply_new_ticket_overwrites(SimpleNamespace(get_role=Mock(return_value=role)), overwrites)
        self.assertEqual(dict((key, value) for key, value in overwrites[role] if value is not None), {"pin_messages": True})

    def test_removed_bot_pin_menu_is_not_registered(self):
        tickets = importlib.import_module("cogs.tickets")
        self.assertFalse(hasattr(tickets, "toggle_pin_message_context_menu"))
        self.assertFalse(hasattr(tickets.TicketsCog, "_pin_command_check"))


class NativePinSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.role = Mock(id=555)
        self.guild = SimpleNamespace(get_role=Mock(return_value=self.role))
        self.current = discord.PermissionOverwrite(view_channel=True, send_messages=False, manage_messages=False)
        self.channel = SimpleNamespace(guild=self.guild, overwrites_for=Mock(side_effect=lambda role: self.current), set_permissions=AsyncMock())
        self.bot = SimpleNamespace(get_channel=Mock(return_value=self.channel), fetch_channel=AsyncMock(return_value=self.channel))
        self.ticket = {"channel_id": "42"}
        self.config = {"pin_message_roles": ["555"]}
        for patcher in [patch.object(access, "get_access_settings", side_effect=lambda: self.config),
                        patch.object(pins.storage, "get_tickets_data", return_value={"active_tickets": [self.ticket]}),
                        patch.object(pins.storage, "update_active_ticket", return_value=True)]:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_existing_ticket_is_updated_without_touching_other_permissions(self):
        result = await pins.sync_active_tickets(self.bot)
        self.assertEqual(result, {"updated": 1, "failed": 0})
        self.bot.get_channel.assert_called_once_with(42)
        overwrite = self.channel.set_permissions.await_args.kwargs["overwrite"]
        self.assertTrue(overwrite.pin_messages)
        self.assertTrue(overwrite.view_channel)
        self.assertFalse(overwrite.send_messages)
        self.assertFalse(overwrite.manage_messages)
        self.assertIsNone(self.current.pin_messages)

    async def test_legacy_ticket_removal_clears_pin_allow_and_preserves_other_flags(self):
        self.config = {"pin_message_roles": [], "pin_permission_pending_removals": ["555"]}
        self.current.pin_messages = True
        with patch.object(pins, "_clear_pending") as cleared:
            result = await pins.sync_active_tickets(self.bot)
        self.assertEqual(result["failed"], 0)
        overwrite = self.channel.set_permissions.await_args.kwargs["overwrite"]
        self.assertIsNone(overwrite.pin_messages)
        self.assertTrue(overwrite.view_channel)
        self.assertFalse(overwrite.manage_messages)
        cleared.assert_called_once()

    async def test_failed_channel_updates_retain_pending_work_and_can_retry(self):
        self.config = {"pin_message_roles": [], "pin_permission_pending_removals": ["555"]}
        self.current.pin_messages = True
        self.channel.set_permissions.side_effect = RuntimeError("Manage Roles denied")
        with patch.object(pins, "_clear_pending") as cleared, self.assertLogs(pins.logger, level="ERROR"):
            result = await pins.sync_active_tickets(self.bot)
        self.assertEqual(result["failed"], 1)
        cleared.assert_not_called()
        self.channel.set_permissions.side_effect = None
        with patch.object(pins, "_clear_pending") as cleared:
            result = await pins.sync_active_tickets(self.bot)
        self.assertEqual(result["failed"], 0)
        cleared.assert_called_once()

    async def test_repeated_sync_does_not_send_unnecessary_permission_requests(self):
        self.current.pin_messages = True
        self.ticket["pin_permission_managed_roles"] = ["555"]
        await pins.sync_active_tickets(self.bot)
        self.channel.set_permissions.assert_not_awaited()
        pins.storage.update_active_ticket.assert_not_called()

    async def test_removed_roles_with_explicit_deny_keep_that_deny(self):
        self.config = {"pin_message_roles": [], "pin_permission_pending_removals": ["555"]}
        self.current.pin_messages = False
        await pins.sync_active_tickets(self.bot)
        self.channel.set_permissions.assert_not_awaited()

    async def test_removing_pin_only_overwrite_removes_empty_overwrite(self):
        self.config = {"pin_message_roles": [], "pin_permission_pending_removals": ["555"]}
        self.current = discord.PermissionOverwrite(pin_messages=True)
        await pins.sync_active_tickets(self.bot)
        self.assertIsNone(self.channel.set_permissions.await_args.kwargs["overwrite"])

    async def test_missing_channel_cache_uses_discord_fetch(self):
        self.bot.get_channel.return_value = None
        await pins.sync_active_tickets(self.bot)
        self.bot.fetch_channel.assert_awaited_once_with(42)

    async def test_retry_worker_stops_after_success_and_reconnect_requests_sync(self):
        tickets = importlib.import_module("cogs.tickets")
        cog = tickets.TicketsCog(self.bot)
        with patch.object(pins, "sync_active_tickets", new_callable=AsyncMock, return_value={"updated": 1, "failed": 0}) as synced:
            await tickets.TicketsCog.pin_permission_watcher.coro(cog)
            await tickets.TicketsCog.pin_permission_watcher.coro(cog)
            synced.assert_awaited_once()
            await cog.on_ready()
            await tickets.TicketsCog.pin_permission_watcher.coro(cog)
            self.assertEqual(synced.await_count, 2)


class NativePinRoutes(dashboard_tests.DashboardFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.main = importlib.import_module("main")
        self.main.app.config.update(TESTING=True, SECRET_KEY="pin-tests")
        self.client = self.main.app.test_client()
        self.seed_access({"allowed_users": [ADMIN]})
        self.start_patch(patch.object(self.main, "remember_base_url"))
        self.start_patch(patch.object(self.main, "get_member_role_ids", return_value=[]))
        self.queued = self.start_patch(patch.object(self.main, "_queue_native_pin_sync", return_value=True))
        self.sign_in(ADMIN)

    sign_in = dashboard_tests.DashboardManagementRouteTests.sign_in

    def test_add_and_remove_queue_native_sync_and_persist_removal(self):
        self.client.post("/dashboard/access/add-pin-message-role", data={"role_id": "555", "csrf_token": "test-csrf"})
        self.assertEqual(pins.selected_roles(), {"555"})
        self.client.post("/dashboard/access/remove-pin-message-role/555", data={"csrf_token": "test-csrf"})
        self.assertEqual(pins.selected_roles(), set())
        self.assertEqual(access.get_access_settings()["pin_permission_pending_removals"], ["555"])
        self.assertEqual(self.queued.call_count, 2)

    def test_bulk_removal_queues_the_same_pin_permission_cleanup(self):
        pins.change_roles(add=["555", "666"])
        self.client.post("/dashboard/access/bulk-remove", data={"list_kind": "pin_message_roles", "selected_ids": ["555"], "csrf_token": "test-csrf"})
        self.assertEqual(pins.selected_roles(), {"666"})
        self.assertEqual(access.get_access_settings()["pin_permission_pending_removals"], ["555"])
        self.queued.assert_called_once()

    def test_pin_controls_are_admin_only_and_csrf_protected(self):
        self.assertEqual(self.client.post("/dashboard/access/add-pin-message-role", data={"role_id": "555"}).status_code, 400)
        self.sign_in(MANAGER)
        self.client.post("/dashboard/access/add-pin-message-role", data={"role_id": "555", "csrf_token": "test-csrf"})
        self.assertEqual(pins.selected_roles(), set())
        self.queued.assert_not_called()

    def test_dashboard_explains_native_permission_and_has_apply_control(self):
        response = self.client.get("/dashboard/access-control")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Manage Pin Access', response.data)
        self.assertIn(b'Apply to existing tickets', response.data)
        self.assertNotIn(b"can use the bot's pin command", response.data)

"""Access audit routing, role-change deduplication, and authenticated mutation logs."""
import asyncio
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_dashboard_management as dashboard_tests
from test_carry_application_management import ApplicationFixture, ADMIN, MANAGER, APPLICANT
from utils import access, audit, blacklists, storage, dashboard_settings as settings


class AuditConfigChecks:
    def setUp(self):
        super().setUp()
        self.start_patch(patch.object(access, "ACCESS_FILE", str(Path(self.temporary.name) / "access.json")))
        self.start_patch(patch.object(storage, "TICKET_LOGS_FILE", str(Path(self.temporary.name) / "logs.json")))

    def test_audit_settings_persist_and_legacy_ticket_channel_command_preserves_them(self):
        settings.save_ticket_logging_config(True, ["opened"], {"log_channel_id": "11"},
            audit={"enabled": True, "events": ["user_unblocked"], "channel_id": "22"})
        settings.save_ticket_logging_config(True, ["opened"], {"log_channel_id": "33"})
        config = settings.get_ticket_logging_config()
        self.assertEqual(config["access_log_channel_id"], "22")
        self.assertEqual(config["audit_events"], ["user_unblocked"])
        self.assertEqual(config["log_channel_id"], "33")

    def test_invalid_audit_configuration_does_not_partially_update_ticket_settings(self):
        settings.save_ticket_logging_config(True, ["opened"], {"log_channel_id": "11"})
        for config in ({"enabled": "yes", "events": [], "channel_id": ""},
                       {"enabled": True, "events": ["unknown"], "channel_id": ""},
                       {"enabled": True, "events": [], "channel_id": "bad"}):
            with self.assertRaises(ValueError):
                settings.save_ticket_logging_config(False, [], {"log_channel_id": "99"}, audit=config)
        self.assertEqual(settings.get_ticket_logging_config()["log_channel_id"], "11")

    def test_saved_history_includes_audit_target_search_with_delivery_offline(self):
        bot = SimpleNamespace(is_ready=Mock(return_value=False))
        audit.emit_event(bot, "user_unblocked", ADMIN, user_id=APPLICANT, source="Dashboard")
        page = settings.ticket_log_page(action="user_unblocked", query=APPLICANT)
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["executor"]["id"], ADMIN)

    def test_empty_event_selection_is_preserved(self):
        settings.save_ticket_logging_config(True, [], {}, audit={"enabled": True, "events": [], "channel_id": ""})
        self.assertEqual(settings.get_ticket_logging_config()["audit_events"], [])


class MongoAuditConfigTests(AuditConfigChecks, ApplicationFixture, unittest.TestCase):
    pass


class FileAuditConfigTests(AuditConfigChecks, ApplicationFixture, unittest.TestCase):
    use_mongo = False


class AuditDiscordTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        audit._expected.clear()
        self.channel = SimpleNamespace(send=AsyncMock())
        self.bot = SimpleNamespace(get_channel=Mock(return_value=self.channel), fetch_channel=AsyncMock(return_value=self.channel))
        self.config = {"audit_enabled": True, "audit_events": list(audit.AUDIT_EVENTS),
            "blacklist_log_channel_id": "11", "access_log_channel_id": "22", "log_channel_id": "33"}

    async def test_channel_priority_and_single_v2_post_with_suppressed_mentions(self):
        with patch.object(settings, "get_ticket_logging_config", return_value=self.config):
            await audit.send_event(self.bot, "blacklist_role_assigned", user_id="@everyone", role_id="555")
            self.bot.get_channel.assert_called_once_with(11)
            self.channel.send.assert_awaited_once()
            payload = self.channel.send.await_args.kwargs
            self.assertNotIn("embed", payload)
            self.assertIn("555", str(payload["view"].to_components()))
            self.assertFalse(payload["allowed_mentions"].everyone)
            self.bot.get_channel.reset_mock()
            self.channel.send.reset_mock()
            await audit.send_event(self.bot, "user_unblocked", user_id="123")
            self.bot.get_channel.assert_called_once_with(22)
            self.channel.send.assert_awaited_once()

    async def test_fallback_to_ticket_channel_and_cache_miss_fetch(self):
        self.config.update(blacklist_log_channel_id=None, access_log_channel_id=None)
        self.bot.get_channel.return_value = None
        with patch.object(settings, "get_ticket_logging_config", return_value=self.config):
            await audit.send_event(self.bot, "blacklist_removed", user_id="123")
        self.bot.fetch_channel.assert_awaited_once_with(33)
        self.channel.send.assert_awaited_once()

    async def test_disabled_unselected_and_no_channel_do_not_send(self):
        with patch.object(settings, "get_ticket_logging_config", return_value=self.config):
            self.config["audit_enabled"] = False
            await audit.send_event(self.bot, "user_unblocked")
            self.config.update(audit_enabled=True, audit_events=[])
            await audit.send_event(self.bot, "user_unblocked")
            self.config.update(audit_events=["user_unblocked"], blacklist_log_channel_id=None, access_log_channel_id=None, log_channel_id=None)
            await audit.send_event(self.bot, "user_unblocked")
        self.channel.send.assert_not_awaited()

    async def test_delivery_failure_does_not_escape_to_mutation_caller(self):
        self.channel.send.side_effect = RuntimeError("Discord offline")
        with patch.object(settings, "get_ticket_logging_config", return_value=self.config), self.assertLogs(audit.logger, level="ERROR"):
            await audit.send_event(self.bot, "user_unblocked")

    async def test_bot_role_change_echo_is_consumed_once_and_failed_requests_leave_no_marker(self):
        member = SimpleNamespace(id=123, guild=SimpleNamespace(id=42), roles=[], add_roles=AsyncMock())
        role = SimpleNamespace(id=555)
        await audit.change_role(member, role, add=True, reason="Blacklist")
        self.assertTrue(audit.consume_role_echo(42, 123, 555, True))
        self.assertFalse(audit.consume_role_echo(42, 123, 555, True))
        member.add_roles.side_effect = RuntimeError("No permission")
        with self.assertRaises(RuntimeError):
            await audit.change_role(member, role, add=True, reason="Blacklist")
        self.assertFalse(audit.consume_role_echo(42, 123, 555, True))

    async def test_gateway_role_update_records_manual_change_and_ignores_bot_echo(self):
        tickets = importlib.import_module("cogs.tickets")
        guild = SimpleNamespace(id=42)
        before = SimpleNamespace(id=123, guild=guild, roles=[])
        after = SimpleNamespace(id=123, guild=guild, roles=[SimpleNamespace(id=555)])
        with patch.object(blacklists, "get_types", return_value=[{"role_id": "555"}]), patch.object(audit, "record_event") as recorded, patch.object(audit, "send_event", new_callable=AsyncMock) as sent:
            await tickets.TicketsCog.on_member_update(SimpleNamespace(bot=self.bot), before, after)
            self.assertEqual(recorded.call_args.args[0], "blacklist_role_assigned")
            sent.assert_awaited_once()
            recorded.reset_mock()
            sent.reset_mock()
            audit._expected[(42, 123, 555, True)] = audit.time.monotonic() + 90
            await tickets.TicketsCog.on_member_update(SimpleNamespace(bot=self.bot), before, after)
            recorded.assert_not_called()
            sent.assert_not_awaited()
            await tickets.TicketsCog.on_member_update(SimpleNamespace(bot=self.bot), after, before)
            self.assertEqual(recorded.call_args.args[0], "blacklist_role_unassigned")


class AccessAuditRoutes(dashboard_tests.DashboardFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.main = importlib.import_module("main")
        self.main.app.config.update(TESTING=True, SECRET_KEY="audit-tests")
        self.client = self.main.app.test_client()
        self.seed_access({"allowed_users": [ADMIN]})
        self.start_patch(patch.object(self.main, "remember_base_url"))
        self.start_patch(patch.object(self.main, "get_member_role_ids", return_value=[]))
        self.emitted = self.start_patch(patch.object(audit, "emit_event"))
        self.sign_in(ADMIN)

    sign_in = dashboard_tests.DashboardManagementRouteTests.sign_in

    def test_block_and_unblock_only_log_successful_changes(self):
        self.client.post("/dashboard/access/add-global-block", data={"user_id": APPLICANT})
        self.assertEqual(self.emitted.call_args.args[1], "user_blocked")
        self.client.post("/dashboard/access/add-global-block", data={"user_id": APPLICANT})
        self.assertEqual(self.emitted.call_count, 1)
        self.client.post("/dashboard/access/remove-global-block/" + APPLICANT)
        self.assertEqual(self.emitted.call_args.args[1], "user_unblocked")
        self.client.post("/dashboard/access/remove-global-block/" + APPLICANT)
        self.assertEqual(self.emitted.call_count, 2)

    def test_blacklist_editor_records_added_edited_and_deleted_roles_only_after_save(self):
        data = {"csrf_token": "test-csrf", "blacklist_name": ["Carry restriction"], "blacklist_role": ["555"],
            "blacklist_reason": ["Reason"], "blacklist_scope": ["all"]}
        self.client.post("/dashboard/access/blacklist-types", data=data)
        self.assertEqual(self.emitted.call_args.args[1], "blacklist_role_added")
        self.emitted.reset_mock()
        self.client.post("/dashboard/access/blacklist-types", data=data)
        self.emitted.assert_not_called()
        self.client.post("/dashboard/access/blacklist-types", data={**data, "blacklist_reason": ["Changed"]})
        self.assertEqual(self.emitted.call_args.args[1], "blacklist_role_updated")
        self.client.post("/dashboard/access/blacklist-types", data={"csrf_token": "test-csrf"})
        self.assertEqual(self.emitted.call_args.args[1], "blacklist_role_deleted")

    def test_failed_blacklist_save_does_not_emit_success_event(self):
        with patch.object(blacklists, "save_types", side_effect=RuntimeError("DB unavailable")), self.assertLogs(self.main.app.logger, level="ERROR"):
            self.client.post("/dashboard/access/blacklist-types", data={"csrf_token": "test-csrf"})
        self.emitted.assert_not_called()

    def test_terms_unblock_logs_affected_user_without_token_or_link(self):
        access.add_globally_blocked_user(APPLICANT)
        token = access.create_unblock_terms_token(APPLICANT)
        self.sign_in(APPLICANT)
        self.assertEqual(self.client.post(f"/terms/unblock/{token}/accept").status_code, 302)
        call = self.emitted.call_args
        self.assertEqual(call.args[1], "user_unblocked")
        self.assertEqual(call.kwargs["user_id"], APPLICANT)
        self.assertNotIn(token, str(call))

    def test_link_creation_and_revocation_do_not_log_secret_token(self):
        self.client.post("/dashboard/access/generate-unblock-link", data={"user_id": APPLICANT})
        token = access.get_active_terms_unblock_tokens()[0]["token"]
        self.assertEqual(self.emitted.call_args.args[1], "unblock_link_created")
        self.client.post("/dashboard/access/revoke-unblock-link", data={"token": token})
        self.assertEqual(self.emitted.call_args.args[1], "unblock_link_revoked")
        self.assertNotIn(token, str(self.emitted.call_args_list))

    def test_bulk_unblocking_and_cooldown_clear_emit_only_actual_changes(self):
        access.add_globally_blocked_user(APPLICANT)
        self.client.post("/dashboard/access/bulk-remove", data={"csrf_token": "test-csrf", "list_kind": "globally_blocked_users", "selected_ids": [APPLICANT, "999"]})
        self.assertEqual(self.emitted.call_args.args[1], "user_unblocked")
        self.assertEqual(self.emitted.call_args.kwargs["removed_ids"], APPLICANT)
        storage.add_cooldown(APPLICANT)
        self.client.post("/api/remove-cooldown/" + APPLICANT)
        self.assertEqual(self.emitted.call_args.args[1], "cooldown_removed")
        count = self.emitted.call_count
        self.client.post("/api/remove-cooldown/" + APPLICANT)
        self.assertEqual(self.emitted.call_count, count)

    def test_new_logging_fields_render_and_csrf_protected_settings_persist(self):
        response = self.client.get("/dashboard/access-control")
        self.assertIn(b'Blacklist and access audit logs', response.data)
        self.assertIn(b'name="access_log_channel_id"', response.data)
        self.assertIn(b'value="user_unblocked"', response.data)
        data = {"csrf_token": "test-csrf", "logging_enabled": "yes", "log_events": ["opened"],
            "log_channel_id": "11", "audit_settings_present": "yes", "audit_logging_enabled": "yes",
            "access_log_channel_id": "22", "audit_events": ["user_unblocked", "blacklist_role_added"]}
        self.assertEqual(self.client.post("/dashboard/ticket-logs/settings", data=data).status_code, 302)
        config = settings.get_ticket_logging_config()
        self.assertEqual(config["audit_events"], ["user_unblocked", "blacklist_role_added"])
        self.assertEqual(config["access_log_channel_id"], "22")
        self.assertEqual(self.client.post("/dashboard/ticket-logs/settings", data={**data, "csrf_token": "bad"}).status_code, 400)


class TicketManagementAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_management_commands_save_and_deliver_one_event(self):
        tickets = importlib.import_module("cogs.tickets")
        cog = tickets.TicketsCog(object())
        cog._basic_command_check = AsyncMock(return_value=True)
        cog._powerful_command_check = AsyncMock(return_value=True)
        member = SimpleNamespace(id=123, bot=False, mention="@member")
        channel = SimpleNamespace(id=42, name="support-0001", mention="#support-0001", set_permissions=AsyncMock(),
            overwrites_for=Mock(return_value=SimpleNamespace(read_messages=False)), edit=AsyncMock())
        interaction = SimpleNamespace(user=SimpleNamespace(id=99), channel=channel,
            guild=SimpleNamespace(get_channel=Mock(return_value=None)), response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(audit, "record_event") as recorded, patch.object(tickets, "send_ticket_log", new_callable=AsyncMock) as delivered, patch.object(tickets, "get_active_ticket", return_value={"autoclose_disabled": False}), patch.object(tickets, "set_autoclose_disabled"), patch.object(tickets, "update_active_ticket"), patch.object(storage, "get_ticket_categories", return_value=[{"label": "Support", "name_prefix": "support"}]), patch.object(storage, "increment_ticket_counter", return_value=2):
            cases = [("add_command", (member,), "ticket_member_added"),
                     ("remove_command", (member,), "ticket_member_removed"),
                     ("autoclose_command", ("disable",), "autoclose_changed"),
                     ("move_command", ("Support",), "ticket_moved")]
            for name, args, action in cases:
                recorded.reset_mock()
                delivered.reset_mock()
                channel.overwrites_for.return_value.read_messages = name == "remove_command"
                await getattr(tickets.TicketsCog, name).callback(cog, interaction, *args)
                recorded.assert_called_once()
                delivered.assert_awaited_once()
                self.assertEqual(recorded.call_args.args[0], action)
                self.assertEqual(delivered.await_args.args[1], action)

    async def test_failed_ticket_member_add_does_not_record_success(self):
        tickets = importlib.import_module("cogs.tickets")
        cog = tickets.TicketsCog(object())
        cog._basic_command_check = AsyncMock(return_value=True)
        member = SimpleNamespace(id=123, bot=False, mention="@member")
        interaction = SimpleNamespace(user=SimpleNamespace(id=99),
            channel=SimpleNamespace(overwrites_for=Mock(return_value=SimpleNamespace(read_messages=False)), set_permissions=AsyncMock(side_effect=RuntimeError("Discord failed"))),
            response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(audit, "record_event") as recorded, patch.object(tickets, "send_ticket_log", new_callable=AsyncMock) as delivered:
            await tickets.TicketsCog.add_command.callback(cog, interaction, member)
        recorded.assert_not_called()
        delivered.assert_not_awaited()

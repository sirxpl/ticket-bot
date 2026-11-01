"""Bulk edits, logging configuration/history, and authenticated dashboard routes."""
import asyncio
import datetime
import importlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_carry_application_management import ApplicationFixture, ADMIN, MANAGER, OUTSIDER
from utils import access, storage, dashboard_settings as settings


class DashboardFixture(ApplicationFixture):
    def setUp(self):
        super().setUp()
        self.start_patch(patch.object(access, "ACCESS_FILE", str(Path(self.temporary.name) / "access.json")))
        self.start_patch(patch.object(storage, "TICKET_LOGS_FILE", str(Path(self.temporary.name) / "logs.json")))

    def seed_access(self, data):
        if self.db is not None:
            self.db.access_control.insert_one({"_id": "singleton", **data})
        else:
            Path(access.ACCESS_FILE).write_text(json.dumps(data))

    def seed_settings(self, data):
        if self.db is not None:
            self.db.bot_settings.insert_one({"_id": "singleton", **data})
        else:
            Path(storage.SETTINGS_FILE).write_text(json.dumps(data))

    def seed_logs(self, rows):
        if self.db is not None:
            self.db.ticket_logs.insert_many([dict(row) for row in rows])
        else:
            Path(storage.TICKET_LOGS_FILE).write_text(json.dumps(rows))


class SettingsChecks:
    def test_bulk_remove_changes_only_selected_ids_in_selected_list(self):
        self.seed_access({"allowed_roles": ["101", "102", "103"], "carry_manager_roles": ["101"], "other_setting": "kept"})
        self.assertEqual(settings.remove_access_entries("allowed_roles", ["101", "103"]), 2)
        saved = access.get_access_settings()
        self.assertEqual(saved["allowed_roles"], ["102"])
        self.assertEqual(saved["carry_manager_roles"], ["101"])
        self.assertEqual(saved["other_setting"], "kept")

    def test_legacy_integer_ids_are_removed_and_unknown_ids_are_ignored(self):
        self.seed_access({"allowed_users": [101, "102"]})
        self.assertEqual(settings.remove_access_entries("allowed_users", ["101", "999"]), 1)
        self.assertEqual(access.get_access_settings()["allowed_users"], ["102"])

    def test_bulk_edits_reject_unknown_fields_and_invalid_selection(self):
        self.seed_access({"allowed_roles": ["101"]})
        cases = [("log_channel_id", ["101"]), ("$unset", ["101"]), ("allowed_roles", []),
                 ("allowed_roles", ["$ne"]), ("allowed_roles", [str(i) for i in range(101)])]
        for kind, values in cases:
            with self.subTest(kind=kind, values=values[:2]), self.assertRaises(ValueError):
                settings.remove_access_entries(kind, values)
        self.assertEqual(access.get_access_settings()["allowed_roles"], ["101"])

    def test_bulk_removal_does_not_change_configured_admins(self):
        self.seed_access({"allowed_users": [ADMIN]})
        settings.remove_access_entries("allowed_users", [ADMIN])
        self.assertTrue(access.is_admin(ADMIN))

    def test_global_badge_bulk_removal_preserves_other_settings(self):
        self.seed_settings({"ticket_badge_verification": {"badge_id": "999", "blocked_badges": ["101", "102", "103"], "verification_url": "https://rover.link/verify/"}, "other": True})
        self.assertEqual(settings.remove_blocked_badges(["101", "103"]), 2)
        saved = storage.get_settings()
        self.assertEqual(saved["ticket_badge_verification"]["blocked_badges"], ["102"])
        self.assertEqual(saved["ticket_badge_verification"]["badge_id"], "999")
        self.assertTrue(saved["other"])

    def test_legacy_single_blocked_badge_can_be_removed(self):
        self.seed_settings({"ticket_badge_verification": {"badge_id": "101"}})
        self.assertEqual(settings.remove_blocked_badges(["101"]), 1)
        self.assertEqual(storage.get_ticket_badge_config()["blocked_badges"], [])

    def test_logging_policy_preserves_unrelated_permissions(self):
        self.seed_access({"allowed_roles": ["101"], "log_channel_id": "555"})
        self.assertTrue(settings.get_ticket_logging_config()["enabled"])
        settings.save_ticket_logging_config(False, ["opened", "ticket_started"], {"log_channel_id": "777", "warning_log_channel_id": "888"})
        config = settings.get_ticket_logging_config()
        self.assertFalse(config["enabled"])
        self.assertEqual(config["events"], ["opened", "ticket_started"])
        self.assertEqual(config["log_channel_id"], "777")
        self.assertEqual(access.get_access_settings()["allowed_roles"], ["101"])

    def test_manual_bot_control_persists_without_changing_access_lists(self):
        self.seed_access({"allowed_users": ["101"], "manual_bot_enabled": True})
        self.assertTrue(access.get_access_settings()["manual_bot_enabled"])
        self.assertFalse(access.save_manual_bot_enabled(False))
        saved = access.get_access_settings()
        self.assertFalse(saved["manual_bot_enabled"])
        self.assertEqual(saved["allowed_users"], ["101"])
        self.assertTrue(access.save_manual_bot_enabled(True))
        self.assertTrue(access.get_access_settings()["manual_bot_enabled"])

    def test_logging_rejects_unknown_events_and_non_numeric_channels(self):
        for enabled, events, channels in [(True, ["arbitrary_event"], {}), (True, [], {"log_channel_id": "not-a-channel"}), ("yes", [], {})]:
            with self.subTest(events=events), self.assertRaises(ValueError):
                settings.save_ticket_logging_config(enabled, events, channels)

    def test_category_save_updates_only_category_field(self):
        self.seed_settings({"tickets_enabled": False, "carry_application_config": {"accepting_responses": False}})
        settings.save_categories_checked([{"label": "Support"}])
        saved = storage.get_settings()
        self.assertFalse(saved["tickets_enabled"])
        self.assertFalse(saved["carry_application_config"]["accepting_responses"])
        self.assertEqual(saved["ticket_categories"], [{"label": "Support"}])

    def test_logs_are_filtered_paginated_and_include_legacy_timestamp_types(self):
        rows = [{"ticket_id": str(i), "action": "created", "timestamp": 1793203200, "creator": {"name": "Example"}} for i in range(55)]
        rows.append({"ticket_id": "55", "action": "closed", "timestamp": "2026-10-28T20:00:00+04:00", "reason": "Done"})
        self.seed_logs(rows)
        page = settings.ticket_log_page(2)
        self.assertEqual((page["total"], page["pages"], len(page["items"])), (56, 2, 6))
        closed = settings.ticket_log_page(action="closed")
        self.assertEqual(closed["total"], 1)
        self.assertIn("16:00 UTC", closed["items"][0]["time_label"])
        self.assertEqual(settings.ticket_log_page(query="Example")["total"], 55)
        self.assertEqual(settings.ticket_log_page(query="[")["total"], 0)
        self.assertEqual(settings.ticket_log_page(query="9" * 100)["total"], 0)

    def test_paused_discord_delivery_does_not_hide_saved_history(self):
        self.seed_logs([{"ticket_id": "101", "action": "created", "timestamp": "2026-10-28T16:00:00Z"}])
        settings.save_ticket_logging_config(False, [], {})
        self.assertEqual(settings.ticket_log_page()["total"], 1)


class LocalDashboardSettingsTests(SettingsChecks, DashboardFixture, unittest.TestCase):
    use_mongo = False

    def test_malformed_local_configuration_is_not_overwritten(self):
        Path(access.ACCESS_FILE).write_text("broken json")
        with self.assertRaises(ValueError):
            settings.remove_access_entries("allowed_roles", ["101"])
        self.assertEqual(Path(access.ACCESS_FILE).read_text(), "broken json")


class MongoDashboardSettingsTests(SettingsChecks, DashboardFixture, unittest.TestCase):
    def test_manual_bot_control_fails_closed_when_configured_mongodb_is_unavailable(self):
        with patch.object(access, "get_db", return_value=None):
            self.assertFalse(access.get_manual_bot_enabled())

    def test_database_outage_does_not_claim_or_write_fallback_settings(self):
        for module, operation in [(access, lambda: settings.remove_access_entries("allowed_roles", ["101"])),
                                  (access, lambda: settings.save_ticket_logging_config(True, ["opened"], {})),
                                  (access, lambda: access.save_manual_bot_enabled(False)),
                                  (storage, lambda: settings.remove_blocked_badges(["101"])),
                                  (storage, lambda: settings.save_categories_checked([])),
                                  (storage, settings.ticket_log_page)]:
            with self.subTest(module=module.__name__), patch.object(module, "get_db", return_value=None):
                with self.assertRaises(RuntimeError):
                    operation()
        self.assertFalse(Path(access.ACCESS_FILE).exists())
        self.assertFalse(Path(storage.SETTINGS_FILE).exists())


class DashboardManagementRouteTests(DashboardFixture, unittest.TestCase):
    def test_status_log_destination_can_be_disabled_or_set_to_channel(self):
        access.save_status_log_settings("disabled")
        self.assertEqual(access.get_status_log_config()["destination"], "disabled")
        access.save_status_log_settings("channel", "123456789012345678")
        config = access.get_status_log_config()
        self.assertEqual(config["destination"], "channel")
        self.assertEqual(config["channel_id"], "123456789012345678")

    def test_status_log_webhook_is_validated_and_not_exposed_to_templates(self):
        webhook = "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz0123456789"
        access.save_status_log_settings("webhook", webhook_url=webhook)
        self.assertEqual(access.get_status_log_config()["webhook_url"], webhook)
        with self.assertRaises(ValueError):
            access.save_status_log_settings("webhook", webhook_url="https://example.com/not-a-webhook")
        response = self.client.get("/dashboard/access-control")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(webhook.encode(), response.data)

    def test_status_log_destination_route_requires_valid_destination(self):
        response = self.client.post("/dashboard/access/save-status-log-channel", data={
            "csrf_token": "test-csrf",
            "status_log_destination": "channel",
            "status_log_channel_id": "not-a-channel",
        })
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(access.get_status_log_config()["destination"], "channel")

    def test_oauth_callback_failure_reaches_emergency_login_during_lockdown(self):
        self.start_patch(patch.dict(os.environ, {
            "DASHBOARD_EMERGENCY_LOGIN_KEY": "test-emergency-key-which-is-longer-than-thirty-two-characters",
            "DASHBOARD_EMERGENCY_ADMIN_ID": ADMIN,
        }))
        future = __import__("time").time() + 300
        self.start_patch(patch.object(self.main, "web_lockdown_until", future))
        self.start_patch(patch.object(self.main, "api_block_until", future))
        self.start_patch(patch.object(self.main, "make_oauth_session", return_value=Mock(
            fetch_token=Mock(side_effect=RuntimeError("Discord API unavailable"))
        )))
        with self.client.session_transaction() as session:
            session["oauth2_state"] = "test-state"
        response = self.client.get("/callback?code=temporary-code")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin-emergency-login", response.headers["Location"])
        recovery = self.client.get(response.headers["Location"])
        self.assertEqual(recovery.status_code, 200)

    def test_emergency_admin_login_sets_only_configured_admin(self):
        self.start_patch(patch.dict(os.environ, {
            "DASHBOARD_EMERGENCY_LOGIN_KEY": "test-emergency-key-which-is-longer-than-thirty-two-characters",
            "DASHBOARD_EMERGENCY_ADMIN_ID": ADMIN,
        }))
        page = self.client.get("/admin-emergency-login")
        self.assertEqual(page.status_code, 200)
        with self.client.session_transaction() as session:
            csrf = session["admin_emergency_csrf"]
        response = self.client.post("/admin-emergency-login", data={
            "csrf_token": csrf,
            "access_key": "test-emergency-key-which-is-longer-than-thirty-two-characters",
        })
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertEqual(session["user"]["id"], ADMIN)
            self.assertEqual(session["user"]["auth_method"], "emergency")

    def test_admin_global_block_check_does_not_require_storage(self):
        with patch.object(access, "get_globally_blocked_users", side_effect=RuntimeError("storage offline")):
            self.assertFalse(access.is_globally_blocked(ADMIN))

    def test_emergency_admin_login_rejects_wrong_key(self):
        self.start_patch(patch.dict(os.environ, {
            "DASHBOARD_EMERGENCY_LOGIN_KEY": "test-emergency-key-which-is-longer-than-thirty-two-characters",
            "DASHBOARD_EMERGENCY_ADMIN_ID": ADMIN,
        }))
        # DashboardFixture signs in an admin for most route tests. Start this
        # case anonymously so it verifies that a bad emergency key cannot
        # create an authenticated session, rather than inheriting that login.
        with self.client.session_transaction() as session:
            session.clear()
        self.client.get("/admin-emergency-login")
        with self.client.session_transaction() as session:
            csrf = session["admin_emergency_csrf"]
        response = self.client.post("/admin-emergency-login", data={
            "csrf_token": csrf,
            "access_key": "wrong-key",
        })
        self.assertEqual(response.status_code, 401)
        with self.client.session_transaction() as session:
            self.assertNotIn("user", session)

    def setUp(self):
        super().setUp()
        self.main = importlib.import_module("main")
        self.main.app.config.update(TESTING=True, SECRET_KEY="dashboard-tests")
        self.client = self.main.app.test_client()
        self.seed_access({"carry_manager_roles": ["777"], "allowed_roles": ["101", "102", "103"]})
        self.start_patch(patch.object(self.main, "remember_base_url"))
        self.start_patch(patch.object(self.main, "get_member_role_ids", side_effect=lambda uid: ["777", "101"] if uid == MANAGER else []))
        self.sign_in(ADMIN)

    def sign_in(self, user):
        with self.client.session_transaction() as saved:
            saved.clear()
            saved["user"] = {"id": user, "username": "Example"}
            saved["carry_application_csrf"] = "test-csrf"

    def test_admin_dashboard_remains_accessible_during_discord_api_cooldown(self):
        future = __import__("time").time() + 300
        self.start_patch(patch.object(self.main, "web_lockdown_until", 0))
        self.start_patch(patch.object(self.main, "api_block_until", future))
        self.assertEqual(self.main.web_lockdown_seconds_left(), 0)
        response = self.client.get("/dashboard/access-control")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Bot Controls", response.data)

    def test_discord_oauth_login_can_start_while_bot_api_is_in_cooldown(self):
        future = __import__("time").time() + 300
        self.start_patch(patch.object(self.main, "web_lockdown_until", 0))
        self.start_patch(patch.object(self.main, "api_block_until", future))
        oauth = Mock()
        oauth.authorization_url.return_value = ("https://discord.com/oauth2/authorize?state=test", "test-state")
        self.start_patch(patch.object(self.main, "make_oauth_session", return_value=oauth))
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "https://discord.com/oauth2/authorize?state=test")
        with self.client.session_transaction() as saved:
            self.assertEqual(saved["oauth2_state"], "test-state")

    def test_admin_can_add_multiple_roles_in_one_access_control_submit(self):
        response = self.client.post("/dashboard/access/add-role", data={"role_id": ["501", "502", "501"]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(access.get_access_settings()["allowed_roles"], ["101", "102", "103", "501", "502"])

        response = self.client.post("/dashboard/access/add-carry-manager-role", data={"role_id": ["601", "602"]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(access.get_access_settings()["carry_manager_roles"], ["777", "601", "602"])

    def test_access_control_has_grouped_lists_and_associated_bulk_checkboxes(self):
        response = self.client.get("/dashboard/access-control")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="access-carry-team"', response.data)
        self.assertIn(b'form="bulk-allowed_roles"', response.data)
        self.assertIn(b'data-bulk-form', response.data)
        self.assertIn(b'You have unsaved changes.', response.data)
        self.assertIn(b'id="dashboard-unsaved-actions"', response.data)
        self.assertIn(b'multiple size="4"', response.data)

    def test_admin_can_bulk_remove_and_new_route_requires_csrf(self):
        data = {"csrf_token": "test-csrf", "list_kind": "allowed_roles", "selected_ids": ["101", "103"]}
        self.assertEqual(self.client.post("/dashboard/access/bulk-remove", data={**data, "csrf_token": "wrong"}).status_code, 400)
        self.assertEqual(self.client.post("/dashboard/access/bulk-remove", data=data).status_code, 302)
        self.assertEqual(access.get_access_settings()["allowed_roles"], ["102"])

    def test_non_admin_and_blocked_users_cannot_bulk_edit_access(self):
        for user in (MANAGER, OUTSIDER):
            self.sign_in(user)
            self.client.post("/dashboard/access/bulk-remove", data={"csrf_token": "test-csrf", "list_kind": "allowed_roles", "selected_ids": ["101"]})
        self.assertEqual(access.get_access_settings()["allowed_roles"], ["101", "102", "103"])
        self.db.access_control.update_one({"_id": "singleton"}, {"$set": {"globally_blocked_users": [MANAGER]}})
        self.sign_in(MANAGER)
        self.client.post("/dashboard/access/bulk-remove", data={"csrf_token": "test-csrf", "list_kind": "allowed_roles", "selected_ids": ["101"]})
        self.assertEqual(access.get_access_settings()["allowed_roles"], ["101", "102", "103"])

    def test_access_bulk_unknown_fields_do_not_mutate_other_settings(self):
        response = self.client.post("/dashboard/access/bulk-remove", data={"csrf_token": "test-csrf", "list_kind": "log_channel_id", "selected_ids": ["101"]})
        self.assertEqual(response.status_code, 400)

    def test_carry_badges_bulk_route_keeps_unselected_ids(self):
        self.seed_settings({"ticket_badge_verification": {"blocked_badges": ["101", "102"]}})
        response = self.client.post("/dashboard/ticket-badge/bulk-remove", data={"csrf_token": "test-csrf", "selected_ids": ["101"]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(storage.get_ticket_badge_config()["blocked_badges"], ["102"])

    def test_logging_page_and_legacy_history_route_work(self):
        self.seed_logs([{"ticket_id": "42", "ticket_name": "carry-42", "action": "created", "timestamp": "2026-10-28T16:00:00Z", "creator": {"name": "Example"}}])
        response = self.client.get("/dashboard/ticket-logs")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"carry-42", response.data)
        self.assertIn(b"Loggings", response.data)
        response = self.client.get("/tickets/logs")
        self.assertIn("/dashboard/ticket-logs", response.location)

    def test_logging_configuration_is_admin_only_and_csrf_protected(self):
        values = {"csrf_token": "test-csrf", "logging_enabled": "no", "log_events": ["opened"], "log_channel_id": "555"}
        self.assertEqual(self.client.post("/dashboard/ticket-logs/settings", data={**values, "csrf_token": "wrong"}).status_code, 400)
        self.assertEqual(self.client.post("/dashboard/ticket-logs/settings", data=values).status_code, 302)
        self.assertFalse(settings.get_ticket_logging_config()["enabled"])
        self.sign_in(MANAGER)
        self.client.post("/dashboard/ticket-logs/settings", data={**values, "logging_enabled": "yes"})
        self.assertFalse(settings.get_ticket_logging_config()["enabled"])

    def test_logging_is_not_exposed_to_anonymous_or_unauthorized_users(self):
        self.sign_in(OUTSIDER)
        response = self.client.get("/dashboard/ticket-logs")
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(b"Activity history", response.data)
        with self.client.session_transaction() as saved:
            saved.clear()
        self.assertEqual(self.client.get("/debug/ticket-logs").status_code, 302)
        self.assertEqual(self.client.get("/tickets/logs").status_code, 302)

    def test_logging_filters_escape_event_content(self):
        self.seed_logs([{"ticket_id": "42", "action": "closed", "timestamp": 1793203200, "reason": "<script>alert(1)</script>"}])
        response = self.client.get("/dashboard/ticket-logs?log_action=closed")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"&lt;script&gt;", response.data)
        self.assertNotIn(b"<script>alert(1)</script>", response.data)
        self.assertEqual(self.client.get("/dashboard/ticket-logs?log_action=not-valid").status_code, 400)

    def test_json_category_autosave_reports_success_and_preserves_checkbox_alignment(self):
        data = {"csrf_token": "test-csrf", "cat_label": ["One", "Two"], "cat_description": ["", ""], "cat_emoji": ["", ""], "cat_dropdown_enabled": ["false", "true"]}
        response = self.client.post("/dashboard/ticket-categories/save", data=data, headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"ok": True, "fully_saved": True})
        self.assertEqual([row["dropdown_enabled"] for row in storage.get_ticket_categories()], [False, True])

    def test_failed_autosave_does_not_report_saved_or_erase_existing_categories(self):
        data = {"csrf_token": "test-csrf", "cat_label": ["One"], "cat_description": [""], "cat_emoji": [""]}
        with patch.object(self.main, "save_categories_checked", side_effect=RuntimeError("unavailable")):
            with self.assertLogs(self.main.app.logger, level="ERROR"):
                response = self.client.post("/dashboard/ticket-categories/save", data=data, headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json["ok"])


class TicketDiscordLoggingTests(unittest.TestCase):
    def test_delivery_respects_enabled_switch_and_event_selection(self):
        module = importlib.import_module("cogs.tickets")
        async def check():
            channel = SimpleNamespace(send=AsyncMock())
            bot = SimpleNamespace(get_channel=Mock(return_value=channel))
            config = {"enabled": False, "events": ["opened"], "log_channel_id": "42"}
            with patch.object(settings, "get_ticket_logging_config", return_value=config):
                await module.send_ticket_log(bot, "opened", ticket="#carry")
                channel.send.assert_not_awaited()
                config["enabled"] = True
                await module.send_ticket_log(bot, "closed", ticket="#carry")
                channel.send.assert_not_awaited()
                await module.send_ticket_log(bot, "opened", ticket="#carry")
                channel.send.assert_awaited_once()
                self.assertIn("Opened", str(channel.send.await_args.kwargs["view"].to_components()))
                self.assertNotIn("embed", channel.send.await_args.kwargs)
        asyncio.run(check())

    def test_start_and_finish_commands_record_and_deliver_events(self):
        module = importlib.import_module("cogs.tickets")
        async def check():
            for name, locked, action in [("start_ticket_command", False, "ticket_started"), ("end_ticket_command", True, "ticket_finished")]:
                cog = SimpleNamespace(bot=object(), _basic_command_check=AsyncMock(return_value=True))
                interaction = SimpleNamespace(channel=SimpleNamespace(id=42, name="carry-42", mention="#carry-42"),
                    user=SimpleNamespace(id=101, mention="@staff"), response=SimpleNamespace(send_message=AsyncMock()))
                with patch.object(module, "get_active_ticket", return_value={"owner_close_locked": locked}), patch.object(module, "set_ticket_owner_close_locked", return_value=True), patch.object(module, "append_ticket_log") as recorded, patch.object(module, "send_ticket_log", new_callable=AsyncMock) as delivered:
                    await getattr(module.TicketsCog, name).callback(cog, interaction)
                    self.assertEqual(recorded.call_args.args[0]["action"], action)
                    self.assertEqual(recorded.call_args.args[0]["ticket_name"], "carry-42")
                    self.assertEqual(delivered.await_args.args[1], action)
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()


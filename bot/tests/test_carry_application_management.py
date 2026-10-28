"""Application policy, lifecycle, and real Flask routes without Discord or MongoDB connections."""
import asyncio
import datetime
import importlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import mongomock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils import access, carry_applications as applications, storage

NOW = datetime.datetime(2026, 10, 28, 16, tzinfo=datetime.timezone.utc)
ADMIN = "900000000000000001"
MANAGER = "900000000000000002"
APPLICANT = "900000000000000003"
OUTSIDER = "900000000000000004"


class ApplicationFixture:
    use_mongo = True

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = mongomock.MongoClient().ticket_bot if self.use_mongo else None
        self.start_patch(patch.dict(os.environ, {"MONGODB_URI": "memory://test" if self.use_mongo else "",
                                               "ADMIN_USER_IDS": ADMIN,
                                               "PUBLIC_BASE_URL": "https://tickets.example.test"}, clear=True))
        for module in (applications, storage, access):
            self.start_patch(patch.object(module, "get_db", return_value=self.db))
        self.start_patch(patch.object(applications, "APPLICATIONS_FILE", str(Path(self.temporary.name) / "applications.json")))
        self.start_patch(patch.object(storage, "SETTINGS_FILE", str(Path(self.temporary.name) / "settings.json")))
        self.start_patch(patch.object(applications, "_active_user_index_ready", False))
        self.start_patch(patch.object(applications, "_now", return_value=NOW))

    def start_patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def submit(self, user=APPLICANT):
        return applications.submit_application(user, "Applicant", {"motivation": "Help the team"}, NOW.isoformat())

    def decided(self, verdict, user=APPLICANT):
        record = self.submit(user)
        return applications.decide_application(record["application_id"], verdict, "Staff reason", MANAGER)


class PolicyChecks:
    def test_defaults_and_settings_survive_new_reads_without_replacing_other_settings(self):
        self.assertEqual(applications.get_application_config(), {"accepting_responses": True, "denial_cooldown_days": 14})
        storage.save_settings({"carry_application_delivery": {"mode": "bot", "channel_id": "123"}})
        applications.save_application_config(False, 7)
        self.assertEqual(applications.get_application_config(), {"accepting_responses": False, "denial_cooldown_days": 7})
        self.assertEqual(storage.get_settings()["carry_application_delivery"]["channel_id"], "123")

    def test_cooldown_validation_rejects_bad_types_and_bounds(self):
        for value in (-1, 366, True, "7", 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                applications.save_application_config(True, value)
        with self.assertRaises(ValueError):
            applications.save_application_config("false", 14)

    def test_closed_form_rejects_new_responses_at_storage_boundary(self):
        applications.save_application_config(False, 14)
        with self.assertRaises(applications.ApplicationStateError) as error:
            self.submit()
        self.assertEqual(error.exception.code, "closed")
        self.assertEqual(applications.get_application_page()["total"], 0)
        applications.save_application_config(True, 14)
        self.assertEqual(self.submit()["status"], "pending")

    def test_pending_and_accepted_cannot_submit_again(self):
        record = self.submit()
        with self.assertRaises(applications.ApplicationStateError) as error:
            self.submit()
        self.assertEqual(error.exception.code, "pending")
        applications.decide_application(record["application_id"], "accepted", "Yes", MANAGER)
        with self.assertRaises(applications.ApplicationStateError) as error:
            self.submit()
        self.assertEqual(error.exception.code, "accepted")

    def test_changed_cooldown_applies_to_existing_denials(self):
        record = self.decided("denied")
        with self.assertRaises(applications.ApplicationStateError) as error:
            self.submit()
        self.assertEqual(error.exception.available_at, NOW + datetime.timedelta(days=14))
        applications.save_application_config(True, 2)
        self.assertEqual(applications.get_application_cooldown_end(record), NOW + datetime.timedelta(days=2))
        with patch.object(applications, "_now", return_value=NOW + datetime.timedelta(days=2)):
            self.assertEqual(self.submit()["status"], "pending")

    def test_zero_days_allows_immediate_reapplication(self):
        self.decided("denied")
        applications.save_application_config(True, 0)
        self.assertEqual(self.submit()["status"], "pending")

    def test_clear_only_selected_denial_and_keep_history(self):
        chosen = self.decided("denied")
        untouched = self.decided("denied", OUTSIDER)
        changed = applications.manage_applications([chosen["application_id"]], "clear_cooldowns", MANAGER)
        self.assertEqual(changed, 1)
        applications.save_application_config(True, 365)
        saved = applications.get_application(chosen["application_id"])
        self.assertEqual(saved["status"], "denied")
        self.assertEqual(saved["decision_reason"], "Staff reason")
        self.assertEqual(saved["cooldown_cleared_by"], MANAGER)
        self.assertIsNone(applications.get_application_cooldown_end(saved))
        self.assertEqual(self.submit()["status"], "pending")
        with self.assertRaises(applications.ApplicationStateError):
            self.submit(OUTSIDER)
        self.assertFalse(applications.get_application(untouched["application_id"]).get("cooldown_cleared_at"))

    def test_remove_acceptance_releases_user_and_preserves_original_response(self):
        chosen = self.decided("accepted")
        untouched = self.decided("accepted", OUTSIDER)
        self.assertEqual(applications.manage_applications([chosen["application_id"]], "remove_accepted", MANAGER), 1)
        saved = applications.get_application(chosen["application_id"])
        self.assertEqual(saved["status"], "removed")
        self.assertEqual(saved["removed_by"], MANAGER)
        self.assertEqual(saved["answers"], chosen["answers"])
        self.assertEqual(self.submit()["status"], "pending")
        self.assertEqual(applications.get_application(untouched["application_id"])["status"], "accepted")
        self.assertEqual(applications.get_application_page(status="removed")["total"], 1)

    def test_actions_cannot_change_records_with_another_status(self):
        pending = self.submit()
        denied = self.decided("denied", OUTSIDER)
        ids = [pending["application_id"], denied["application_id"]]
        self.assertEqual(applications.manage_applications(ids, "remove_accepted", MANAGER), 0)
        self.assertEqual(applications.get_application(pending["application_id"])["status"], "pending")
        self.assertEqual(applications.manage_applications(ids, "clear_cooldowns", MANAGER), 1)
        self.assertEqual(applications.manage_applications(ids, "clear_cooldowns", MANAGER), 0)

    def test_filtered_list_is_paginated_and_page_is_clamped(self):
        for value in range(27):
            self.decided("accepted", str(value))
        page = applications.get_application_page(2, "accepted")
        self.assertEqual((page["total"], page["pages"], len(page["items"])), (27, 2, 2))
        self.assertEqual(applications.get_application_page(999, "accepted")["page"], 2)
        self.assertEqual(applications.get_application_page(status="denied")["total"], 0)


class LocalApplicationPolicyTests(PolicyChecks, ApplicationFixture, unittest.TestCase):
    use_mongo = False


class MongoApplicationPolicyTests(PolicyChecks, ApplicationFixture, unittest.TestCase):
    def test_configured_database_outage_does_not_fall_back_or_claim_success(self):
        with patch.object(applications, "get_db", return_value=None):
            for operation in (applications.get_application_config,
                              lambda: applications.save_application_config(False, 1),
                              lambda: applications.manage_applications(["a" * 32], "remove_accepted", MANAGER),
                              self.submit):
                with self.subTest(operation=operation), self.assertRaises(RuntimeError):
                    operation()
        self.assertFalse(Path(storage.SETTINGS_FILE).exists())
        self.assertFalse(Path(applications.APPLICATIONS_FILE).exists())

    def test_failed_settings_read_does_not_default_to_open(self):
        with patch.object(self.db.bot_settings, "find_one", side_effect=RuntimeError("unavailable")):
            with self.assertRaises(RuntimeError):
                applications.get_application_config()


class CarryApplicationRouteTests(ApplicationFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.main = importlib.import_module("main")
        self.main.app.config.update(TESTING=True, SECRET_KEY="application-test-secret")
        self.client = self.main.app.test_client()
        self.db.access_control.insert_one({"_id": "singleton", "carry_manager_roles": ["777"],
                                          "allowed_users": [], "allowed_roles": []})
        self.start_patch(patch.object(self.main, "remember_base_url"))
        self.start_patch(patch.object(self.main, "_carry_application_member_status", return_value=True))
        self.start_patch(patch.object(self.main, "get_member_role_ids", side_effect=lambda uid: ["777"] if uid == MANAGER else []))
        self.sign_in(MANAGER)

    def sign_in(self, uid):
        with self.client.session_transaction() as session:
            session.clear()
            session["user"] = {"id": uid, "username": "Test member"}
            session["carry_application_csrf"] = "test-csrf"

    def post_settings(self, **overrides):
        values = {"csrf_token": "test-csrf", "accepting_responses": "yes", "denial_cooldown_days": "7", **overrides}
        return self.client.post("/dashboard/carry-applications/settings", data=values)

    def test_manager_can_view_section_and_save_settings_without_admin(self):
        response = self.client.get("/dashboard/carry-applications")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Carry Team Application", response.data)
        self.assertIn(b'data-page="carry-applications"', response.data)
        self.assertEqual(self.post_settings(accepting_responses="no").status_code, 302)
        self.assertEqual(applications.get_application_config(), {"accepting_responses": False, "denial_cooldown_days": 7})

    def test_ordinary_dashboard_user_cannot_view_or_mutate_section(self):
        self.sign_in(OUTSIDER)
        self.assertEqual(self.client.get("/dashboard/carry-applications").status_code, 403)
        self.assertEqual(self.post_settings().status_code, 403)
        response = self.client.get("/dashboard/overview")
        self.assertNotIn(b'data-page="carry-applications"', response.data)
        self.assertEqual(self.client.post("/dashboard/carry-applications/manage", data={"csrf_token": "test-csrf"}).status_code, 403)

    def test_empty_manager_roles_do_not_grant_access_and_admin_still_can_manage(self):
        self.db.access_control.update_one({"_id": "singleton"}, {"$set": {"carry_manager_roles": []}})
        self.assertEqual(self.client.get("/dashboard/carry-applications").status_code, 403)
        self.sign_in(ADMIN)
        self.assertEqual(self.client.get("/dashboard/carry-applications").status_code, 200)
        self.assertEqual(self.post_settings().status_code, 302)

    def test_blocked_manager_cannot_mutate(self):
        self.db.access_control.update_one({"_id": "singleton"}, {"$set": {"globally_blocked_users": [MANAGER]}})
        self.assertEqual(self.post_settings().status_code, 403)

    def test_csrf_and_invalid_values_do_not_save(self):
        self.assertEqual(self.post_settings(csrf_token="wrong").status_code, 400)
        for value in ("-1", "366", "1.5", "NaN"):
            self.post_settings(denial_cooldown_days=value)
        self.assertEqual(applications.get_application_config()["denial_cooldown_days"], 14)
        self.assertEqual(self.client.post("/dashboard/carry-applications/manage", data={"csrf_token": "wrong"}).status_code, 400)

    def test_closed_responses_are_enforced_on_every_public_entry_point(self):
        applications.save_application_config(False, 14)
        self.sign_in(APPLICANT)
        for path in ("/carry-application", "/carry-application/form"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Applications are closed", response.data)
        for path in ("/carry-application/rules", "/carry-application/submit"):
            response = self.client.post(path, data={"csrf_token": "test-csrf", "agree": "yes"})
            self.assertEqual(response.status_code, 409)
        self.assertEqual(applications.get_application_page()["total"], 0)

    def test_public_status_shows_pending_accepted_and_denied_countdown(self):
        record = self.submit()
        self.sign_in(APPLICANT)
        self.assertIn(b"under review", self.client.get("/carry-application").data)
        applications.decide_application(record["application_id"], "denied", "Try again later", MANAGER)
        response = self.client.get("/carry-application")
        self.assertIn(b"Next application in:", response.data)
        self.assertIn(b"Try again later", response.data)
        self.assertIn(b"data-application-deadline=", response.data)
        applications.manage_applications([record["application_id"]], "clear_cooldowns", MANAGER)
        with patch.object(applications, "_now", return_value=NOW + datetime.timedelta(seconds=1)):
            next_record = self.submit()
            applications.decide_application(next_record["application_id"], "accepted", "Welcome", MANAGER)
        self.assertIn(b"Your application was accepted", self.client.get("/carry-application").data)
        self.assertEqual(self.client.post("/carry-application/submit").status_code, 409)

    def test_manager_can_clear_selected_records_but_cannot_remove_pending(self):
        accepted = self.decided("accepted")
        pending = self.submit(OUTSIDER)
        response = self.client.post("/dashboard/carry-applications/manage", data={
            "csrf_token": "test-csrf", "application_ids": [accepted["application_id"], pending["application_id"]],
            "action": "remove_accepted"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(applications.get_application(accepted["application_id"])["status"], "removed")
        self.assertEqual(applications.get_application(pending["application_id"])["status"], "pending")
        self.assertEqual(self.submit()["status"], "pending")

    def test_manager_can_view_full_response_but_an_outsider_cannot(self):
        record = self.decided("accepted")
        url = f'/carry-application/review/{record["application_id"]}'
        self.assertEqual(self.client.get(url).status_code, 200)
        self.sign_in(OUTSIDER)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_clear_cooldown_route_does_not_change_other_denied_users(self):
        chosen = self.decided("denied")
        untouched = self.decided("denied", OUTSIDER)
        response = self.client.post("/dashboard/carry-applications/manage", data={
            "csrf_token": "test-csrf", "application_ids": [chosen["application_id"]],
            "action": "clear_cooldowns", "application_status": "denied"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("application_status=denied", response.location)
        self.assertTrue(applications.get_application(chosen["application_id"]).get("cooldown_cleared_at"))
        self.assertFalse(applications.get_application(untouched["application_id"]).get("cooldown_cleared_at"))

    def test_open_application_can_complete_rules_and_submit(self):
        self.sign_in(APPLICANT)
        response = self.client.post("/carry-application/rules", data={"csrf_token": "test-csrf", "agree": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/carry-application/form").status_code, 200)
        response = self.client.post("/carry-application/submit", data={
            "csrf_token": "test-csrf", "final_agreement": "yes", "age_range": "18+",
            "timezone": "UTC", "roblox_username": "Example", "tds_level": "300",
            "motivation": "Help teammates", "daily_activity": "Two hours", "understands_terms": "Yes",
            "strategy_rating": "8"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(applications.get_latest_application_for_user(APPLICANT)["status"], "pending")

    def test_invalid_management_selection_is_rejected(self):
        for values in ({"application_ids": ["not-an-id"], "action": "remove_accepted"},
                       {"application_ids": ["a" * 32], "action": "delete_everything"}):
            response = self.client.post("/dashboard/carry-applications/manage", data={"csrf_token": "test-csrf", **values})
            self.assertEqual(response.status_code, 400)

    def test_failed_save_reports_failure_without_changing_policy(self):
        with patch.object(self.main, "save_application_config", side_effect=RuntimeError("database unavailable")):
            with self.assertLogs(self.main.app.logger, level="ERROR"):
                self.post_settings(accepting_responses="no")
        with self.client.session_transaction() as session:
            self.assertTrue(any("Could not save" in text for category, text in session["_flashes"]))
        self.assertTrue(applications.get_application_config()["accepting_responses"])

    def test_application_status_does_not_render_user_input_as_html(self):
        record = self.submit()
        applications.decide_application(record["application_id"], "accepted", "<script>alert(1)</script>", MANAGER)
        self.sign_in(APPLICANT)
        response = self.client.get("/carry-application")
        self.assertNotIn(b"<script>alert(1)</script>", response.data)
        self.assertIn(b"&lt;script&gt;", response.data)

    def test_storage_outage_returns_unavailable_instead_of_reopening_portal(self):
        with patch.object(applications, "get_db", return_value=None):
            with self.assertLogs(self.main.app.logger, level="ERROR"):
                self.assertEqual(self.client.get("/carry-application").status_code, 503)

    def test_denial_dm_uses_configured_relative_timestamp(self):
        applications.save_application_config(True, 3)
        record = self.submit()
        cog_module = importlib.import_module("cogs.carry_applications")

        async def check_dm():
            recipient = SimpleNamespace(send=AsyncMock())
            bot = SimpleNamespace(get_user=lambda uid: recipient)
            view = cog_module.ApplicationReviewView(bot, record["application_id"], "https://tickets.example.test/review", application=record)
            interaction = SimpleNamespace(user=SimpleNamespace(id=MANAGER),
                response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()))
            await view.finish_decision(interaction, "denied", "Try again")
            sent_view = recipient.send.await_args.kwargs["view"]
            content = "\n".join(getattr(child, "content", "") for child in sent_view.walk_children())
            expected = int((NOW + datetime.timedelta(days=3)).timestamp())
            self.assertIn(f"<t:{expected}:R>", content)
            self.assertIn("Next application:", content)
            self.assertIn("/carry-application", content)

        asyncio.run(check_dm())


if __name__ == "__main__":
    unittest.main()

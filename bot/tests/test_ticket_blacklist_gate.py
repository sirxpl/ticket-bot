"""Exercise actual ticket-select and modal-submit callbacks with no Discord network."""
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import access, blacklists, storage, audit


class TicketBlacklistGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tickets = importlib.import_module("cogs.tickets")
        self.category = {"label": "Support", "name_prefix": "support", "blacklist_roles": [], "badge_mode": "off"}
        self.member = Mock(id=123, roles=[], name="Applicant", mention="@Applicant")
        self.guild = SimpleNamespace(id=42, get_member=Mock(return_value=self.member), get_role=Mock(return_value=None),
            get_channel=Mock(return_value=None), default_role=Mock(), me=Mock(),
            create_text_channel=AsyncMock(side_effect=RuntimeError("creation reached")))
        self.interaction = SimpleNamespace(user=self.member, guild=self.guild, client=object(),
            response=SimpleNamespace(send_message=AsyncMock(), send_modal=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()))
        self.view = SimpleNamespace(bot=object())
        patches = [patch.object(audit, "record_event"), patch.object(audit, "send_event", new_callable=AsyncMock),
            patch.object(self.tickets, "get_settings", return_value={"tickets_enabled": True}),
            patch.object(self.tickets, "get_ticket_categories", side_effect=lambda: [self.category]),
            patch.object(storage, "get_ticket_categories", side_effect=lambda: [self.category]),
            patch.object(self.tickets, "get_ticket_badge_config", return_value={}),
            patch.object(self.tickets, "get_active_ticket_for_user", return_value=None),
            patch.object(self.tickets, "is_on_cooldown", return_value=(False, None)),
            patch.object(access, "get_access_settings", return_value={}),
            patch.object(blacklists, "get_data", return_value={"blacklisted_users": []}),
            patch.object(storage, "get_april_fools_enabled", return_value=False),
            patch.object(storage, "increment_ticket_counter", return_value=1)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    async def open_form(self):
        await self.tickets.TicketView.ticket_select(self.view, self.interaction, SimpleNamespace(values=["Support"]))
        self.interaction.response.send_modal.assert_awaited_once()
        return self.interaction.response.send_modal.await_args.args[0]

    async def test_eligible_member_can_open_and_submit_actual_form(self):
        self.member.name = "Applicant"
        channel = SimpleNamespace(id=999, name="support-0001", mention="#support-0001", send=AsyncMock())
        welcome = SimpleNamespace(id=888, pin=AsyncMock())
        channel.send.return_value = welcome
        self.guild.create_text_channel.side_effect = None
        self.guild.create_text_channel.return_value = channel
        patches = [patch.object(self.tickets, "get_welcome_message", return_value={"title": "Created", "content": "{user_mention}"}),
            patch.object(self.tickets, "get_redirect_message", return_value={}),
            patch.object(self.tickets, "add_active_ticket"), patch.object(self.tickets, "update_active_ticket"),
            patch.object(storage, "append_ticket_log"), patch.object(self.tickets, "increment_ticket_open_count"),
            patch.object(self.tickets, "send_ticket_log", new_callable=AsyncMock)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        modal = await self.open_form()
        await modal.on_submit(self.interaction)
        self.guild.create_text_channel.assert_awaited_once()
        self.assertIn("Ticket created", self.interaction.followup.send.await_args.args[0])
        channel.send.assert_awaited_once()  # One welcome panel; no duplicate close message.
        self.assertIn("ticket_close_btn", str(channel.send.await_args.kwargs["view"].to_components()))

    async def test_ticket_creation_passes_native_pin_permission_to_discord(self):
        role = Mock(id=555)
        self.guild.get_role.return_value = role
        with patch.object(access, "get_access_settings", return_value={"pin_message_roles": ["555"]}):
            modal = await self.open_form()
            await modal.on_submit(self.interaction)
        overwrite = self.guild.create_text_channel.await_args.kwargs["overwrites"][role]
        self.assertTrue(overwrite.pin_messages)
        self.assertIsNone(overwrite.manage_messages)
        self.assertIsNone(overwrite.view_channel)

    async def test_role_blacklist_blocks_before_badge_gate_or_form(self):
        self.member.roles = [SimpleNamespace(id=555)]
        with patch.object(access, "get_access_settings", return_value={"blacklist_roles": ["555"]}), patch.object(self.tickets, "get_ticket_badge_config") as badges:
            await self.tickets.TicketView.ticket_select(self.view, self.interaction, SimpleNamespace(values=["Support"]))
            self.interaction.response.send_modal.assert_not_awaited()
            self.interaction.response.send_message.assert_awaited_once()
            badges.assert_not_called()

    async def test_new_blacklist_after_form_opens_is_rechecked_at_submit(self):
        modal = await self.open_form()
        with patch.object(blacklists, "get_data", return_value={"blacklisted_users": [{"user_id": "123", "block_all": True, "reason": "New restriction"}]}):
            await modal.on_submit(self.interaction)
        self.guild.create_text_channel.assert_not_awaited()
        self.assertIn("New restriction", str(self.interaction.followup.send.await_args.kwargs["view"].to_components()))

    async def test_category_blacklist_blocks_only_affected_form(self):
        self.member.roles = [SimpleNamespace(id=555)]
        self.category["blacklist_roles"] = ["555"]
        await self.tickets.TicketView.ticket_select(self.view, self.interaction, SimpleNamespace(values=["Support"]))
        self.interaction.response.send_modal.assert_not_awaited()
        self.interaction.response.send_message.assert_awaited_once()

    async def test_late_blacklist_prevents_form_from_verification_continue(self):
        modal = object()
        with patch.object(blacklists, "restriction_reason", return_value="Role blocked"):
            await self.tickets.send_ticket_form(self.interaction, modal, "Support")
        self.interaction.response.send_modal.assert_not_awaited()
        self.assertIn("Role blocked", str(self.interaction.response.send_message.await_args.kwargs["view"].to_components()))


class TicketLoggingChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_discord_command_saves_target_and_preserves_other_logging_channels(self):
        from utils import dashboard_settings
        tickets = importlib.import_module("cogs.tickets")
        guild = SimpleNamespace(id=42, me=object())
        channel = SimpleNamespace(id=777, guild=guild, mention="#ticket-logs", permissions_for=Mock(return_value=SimpleNamespace(view_channel=True, send_messages=True)))
        interaction = SimpleNamespace(guild=guild, user=SimpleNamespace(id=99, guild_permissions=SimpleNamespace(administrator=True)),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        config = {"enabled": False, "events": ["opened", "closed"], "log_channel_id": "111", "blacklist_log_channel_id": "222", "warning_log_channel_id": "333"}
        with patch.object(dashboard_settings, "get_ticket_logging_config", return_value=config), patch.object(dashboard_settings, "save_ticket_logging_config") as saved:
            await tickets.TicketsCog.ticket_logging_command.callback(SimpleNamespace(), interaction, channel)
        self.assertEqual(saved.call_args.args, (True, ["opened", "closed"], {**config, "log_channel_id": "777"}))
        self.assertIn("#ticket-logs", str(interaction.followup.send.await_args.kwargs["view"].to_components()))

    async def test_command_rejects_channel_where_bot_cannot_post(self):
        from utils import dashboard_settings
        tickets = importlib.import_module("cogs.tickets")
        guild = SimpleNamespace(id=42, me=object())
        channel = SimpleNamespace(guild=guild, permissions_for=Mock(return_value=SimpleNamespace(view_channel=True, send_messages=False)))
        interaction = SimpleNamespace(guild=guild, user=SimpleNamespace(id=99, guild_permissions=SimpleNamespace(administrator=True)),
            response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(dashboard_settings, "save_ticket_logging_config") as saved:
            await tickets.TicketsCog.ticket_logging_command.callback(SimpleNamespace(), interaction, channel)
        saved.assert_not_called()
        self.assertIn("Send Messages", interaction.response.send_message.await_args.args[0])

    async def test_non_admin_cannot_change_discord_logging_channel(self):
        from utils import dashboard_settings
        tickets = importlib.import_module("cogs.tickets")
        interaction = SimpleNamespace(user=SimpleNamespace(id=88, guild_permissions=SimpleNamespace(administrator=False)),
            response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(access, "is_admin", return_value=False), patch.object(dashboard_settings, "save_ticket_logging_config") as saved:
            await tickets.TicketsCog.ticket_logging_command.callback(SimpleNamespace(), interaction, object())
        saved.assert_not_called()
        self.assertIn("administrator", interaction.response.send_message.await_args.args[0])

    async def test_ticket_log_fetches_saved_discord_channel_when_not_cached(self):
        from utils import dashboard_settings
        tickets = importlib.import_module("cogs.tickets")
        channel = SimpleNamespace(send=AsyncMock())
        bot = SimpleNamespace(get_channel=Mock(return_value=None), fetch_channel=AsyncMock(return_value=channel))
        with patch.object(dashboard_settings, "get_ticket_logging_config", return_value={"enabled": True, "events": ["opened"], "log_channel_id": "777"}):
            await tickets.send_ticket_log(bot, "opened", ticket="#support-0001")
        bot.fetch_channel.assert_awaited_once_with(777)
        channel.send.assert_awaited_once()
        self.assertIn("#support-0001", str(channel.send.await_args.kwargs["view"].to_components()))

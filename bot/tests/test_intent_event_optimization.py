"""Intent and ticket departure regression checks (no live Discord connection needed)."""
import ast
import asyncio
import copy
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]


class IntentAndDepartureTests(TestCase):
    def test_privileged_intents_are_opt_in(self):
        path = ROOT / "main.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                      and n.name == "_privileged_intent_enabled")
        code = compile(ast.Module(body=[helper], type_ignores=[]), str(path), "exec")
        namespace = {"os": os}
        exec(code, namespace)
        enabled = namespace["_privileged_intent_enabled"]
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(enabled("DISCORD_MEMBERS_INTENT"))
            self.assertFalse(enabled("DISCORD_MESSAGE_CONTENT_INTENT"))
        for affirmative in ("true", "1", "YES", "on"):
            with patch.dict(os.environ, {"DISCORD_MEMBERS_INTENT": affirmative}):
                self.assertTrue(enabled("DISCORD_MEMBERS_INTENT"))
        with patch.dict(os.environ, {"DISCORD_MEMBERS_INTENT": "false"}):
            self.assertFalse(enabled("DISCORD_MEMBERS_INTENT"))
        src = path.read_text(encoding="utf-8")
        self.assertIn('intents.members = _privileged_intent_enabled("DISCORD_MEMBERS_INTENT")', src)
        self.assertIn('intents.message_content = _privileged_intent_enabled("DISCORD_MESSAGE_CONTENT_INTENT")', src)

    def test_raw_departure_only_targets_matching_server_and_user(self):
        path = ROOT / "cogs" / "tickets.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TicketsCog")
        method = copy.deepcopy(next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef)
                                    and n.name == "on_raw_member_remove"))
        method.decorator_list = []
        method.args.args[1].annotation = None
        ast.fix_missing_locations(method)
        code = compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec")
        channels = {
            10: SimpleNamespace(id=10, guild=SimpleNamespace(id=7)),
            11: SimpleNamespace(id=11, guild=SimpleNamespace(id=8)),
            12: SimpleNamespace(id=12, guild=SimpleNamespace(id=7)),
        }
        tickets = [
            {"channel_id": "10", "user_id": "42"},
            {"channel_id": "11", "user_id": "42"},
            {"channel_id": "12", "user_id": "99"},
        ]
        closed = []
        async def close(channel):
            closed.append(channel.id)
        bot = SimpleNamespace(intents=SimpleNamespace(members=True),
                              get_channel=lambda cid: channels.get(cid))
        cog = SimpleNamespace(bot=bot, _close_departed_ticket=close)
        namespace = {"get_tickets_data": lambda: {"active_tickets": tickets}, "logger": Mock()}
        exec(code, namespace)
        payload = SimpleNamespace(user=SimpleNamespace(id=42), guild_id=7)
        asyncio.run(namespace["on_raw_member_remove"](cog, payload))
        self.assertEqual(closed, [10])
        closed.clear()
        bot.intents.members = False
        asyncio.run(namespace["on_raw_member_remove"](cog, payload))
        self.assertEqual(closed, [])

    def test_inactivity_checks_remain_and_membership_is_throttled(self):
        source = (ROOT / "cogs" / "tickets.py").read_text(encoding="utf-8")
        self.assertIn("@tasks.loop(minutes=10)", source)
        self.assertIn("12 * 3600 if self.bot.intents.members else 2 * 3600", source)
        self.assertIn("if reconcile_members:", source)
        self.assertIn("REMINDER_AFTER = 12 * 3600", source)
        self.assertIn("CLOSE_AFTER = 24 * 3600", source)
        self.assertIn('if ticket.get("autoclose_disabled"):', source)
        self.assertIn("self._departing_ticket_ids", source)
        self.assertIn("await self.do_close(", source)


if __name__ == "__main__":
    import unittest
    unittest.main()

"""Regression tests for Discord slash-command visibility rules."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _method(path, class_name, method_name):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method_name
    )


class SlashCommandVisibilityTests(unittest.TestCase):
    def test_ticket_logging_is_visible_to_bot_level_administrators(self):
        method = _method("cogs/tickets.py", "TicketsCog", "ticket_logging_command")
        decorator_names = [
            decorator.func.attr
            for decorator in method.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
        ]

        # Discord's native administrator-only restriction hid this command
        # from configured bot admins who were not also server administrators.
        self.assertNotIn("default_permissions", decorator_names)

        source = ast.get_source_segment(
            (ROOT / "cogs/tickets.py").read_text(encoding="utf-8"), method
        )
        self.assertIn(
            "interaction.user.guild_permissions.administrator or is_admin(interaction.user.id)",
            source,
        )


    def test_manual_points_adjustments_use_one_public_interaction_response(self):
        method = _method("cogs/points.py", "PointsCog", "_manual_adjust")
        source = ast.get_source_segment(
            (ROOT / "cogs/points.py").read_text(encoding="utf-8"), method
        )
        self.assertIn("interaction.response.defer(ephemeral=False)", source)
        self.assertIn("interaction.edit_original_response(", source)
        self.assertNotIn("self._announce_public(", source)


if __name__ == "__main__":
    unittest.main()

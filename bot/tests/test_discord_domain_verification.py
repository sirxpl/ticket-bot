"""Regression test for Discord's plain-text website ownership challenge.

Run from the repository root:
    python -m unittest discover -s bot/tests -p "test_*.py"
"""
import ast
from pathlib import Path
import unittest

from flask import Flask, Response


MAIN = Path(__file__).resolve().parents[1] / "main.py"
CHALLENGE = "dh=8a09c229f65a33f53c04b2cf31a4e98f48d2f317"


class DiscordDomainVerificationTests(unittest.TestCase):
    def test_verification_endpoint_is_public_and_exact(self):
        # Isolate this route so importing the Discord bot does not start any
        # network or background startup work during the test.
        source = ast.parse(MAIN.read_text(encoding="utf-8"), filename=str(MAIN))
        routes = [
            node for node in source.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "discord_domain_verification"
        ]
        self.assertEqual(len(routes), 1)
        app = Flask(__name__)
        endpoint_only = ast.Module(body=routes, type_ignores=[])
        exec(compile(endpoint_only, str(MAIN), "exec"), {"app": app, "Response": Response})
        with app.test_client() as client:
            response = client.get("/.well-known/discord", follow_redirects=False)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_data(as_text=True), CHALLENGE)
            self.assertTrue(response.content_type.startswith("text/plain"))
            self.assertEqual(client.head("/.well-known/discord").status_code, 200)


if __name__ == "__main__":
    unittest.main()

"""Regression checks for the Carry Manager Settings Ad Gag partial.

Run with:
    python -m unittest discover -s bot/tests -p "test_*.py"
"""
from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader


TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
AD_GAG = TEMPLATES / "dashboard_pages" / "_ad_gag_settings.html"


class CarryManagerSettingsTemplateTests(unittest.TestCase):
    def test_ad_gag_template_compiles(self):
        # This catches Jinja interpreting CSS {# as an unclosed comment.
        env = Environment(loader=FileSystemLoader(str(TEMPLATES)))
        env.get_template("dashboard_pages/_ad_gag_settings.html")
        env.get_template("dashboard_pages/carry_settings.html")
        env.get_template("dashboard.html")

    def test_add_ad_button_has_matching_javascript_handler(self):
        markup = AD_GAG.read_text(encoding="utf-8")
        self.assertIn('onclick="addAprilFoolsAd()"', markup)
        self.assertIn("window.addAprilFoolsAd = function", markup)
        self.assertNotIn('onclick="addAd()"', markup)


if __name__ == "__main__":
    unittest.main()

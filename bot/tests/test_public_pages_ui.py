"""Regression tests for the public Docs-family UI and Access Control layout.

Run from repository root:
    python -m unittest discover -s bot/tests -p "test_*.py"
"""
from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
PUBLIC_PAGES = ("rules.html", "guidelines.html", "privacy.html", "status.html", "downtime.html")


class PublicPagesLayoutTests(unittest.TestCase):
    def test_public_templates_compile(self):
        env = Environment(loader=FileSystemLoader(str(TEMPLATES)))
        for name in (*PUBLIC_PAGES, "_public_navigation.html", "dashboard_pages/access_control.html"):
            with self.subTest(page=name):
                env.get_template(name)

    def test_public_navigation_assets_present(self):
        for name in PUBLIC_PAGES:
            with self.subTest(page=name):
                text = (TEMPLATES / name).read_text(encoding="utf-8")
                self.assertIn("{% include '_public_navigation.html' %}", text)
                self.assertIn("public-pages.css", text)
                self.assertIn("public-pages.js", text)
                self.assertIn('id="page-content"', text)

    def test_status_contains_offline_schedule(self):
        status = (TEMPLATES / "status.html").read_text(encoding="utf-8")
        self.assertIn('id="downtime"', status)
        self.assertIn("schedule_timezone", status)
        self.assertIn("schedule_days", status)
        self.assertNotIn('_bot_status_section.html', status)

    def test_access_control_collapsed_sections_keep_forms(self):
        path = TEMPLATES / "dashboard_pages" / "access_control.html"
        content = path.read_text(encoding="utf-8")
        self.assertEqual(content.count("<details"), content.count("</details>"))
        self.assertEqual(content.count("<details"), 24)
        self.assertNotIn('class="card" open', content)
        self.assertEqual(content.count("<form"), content.count("</form>"))
        self.assertIn("offlineScheduleForm", content)
        self.assertIn("Configured Bot Admin IDs", content)

    def test_old_floating_status_overlay_is_not_injected(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("def _inject_public_bot_status(", main)
        self.assertIn('render_template("status.html", base_url=current_base_url()', main)


if __name__ == "__main__":
    unittest.main()

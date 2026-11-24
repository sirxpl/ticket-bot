"""Ticket form timezone dropdown: IANA values, DST-aware labels, Discord limits."""
import datetime as dt
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.timezones import TIMEZONES, timezone_choices, timezone_label

WINTER = dt.datetime(2026, 1, 15, 12, tzinfo=dt.timezone.utc)
SUMMER = dt.datetime(2026, 7, 15, 12, tzinfo=dt.timezone.utc)


class TicketTimezoneOptionsTests(unittest.TestCase):
    def test_new_york_label_follows_daylight_saving(self):
        self.assertEqual(
            timezone_label("America/New_York", WINTER),
            "GMT-5 / ET (New York, Toronto \u2013 standard time)",
        )
        self.assertEqual(
            timezone_label("America/New_York", SUMMER),
            "GMT-4 / ET (New York, Toronto \u2013 daylight time)",
        )

    def test_zones_without_dst_have_no_standard_or_daylight_suffix(self):
        label = timezone_label("Asia/Tokyo", WINTER)
        self.assertEqual(label, "GMT+9 / JST (Tokyo, Seoul)")

    def test_half_hour_offsets_are_formatted(self):
        self.assertTrue(timezone_label("Asia/Kolkata", WINTER).startswith("GMT+5:30 / IST"))

    def test_choices_fit_discord_select_limits(self):
        for now in (WINTER, SUMMER):
            choices = timezone_choices(now)
            self.assertLessEqual(len(choices), 25)
            self.assertEqual(len({value for value, _ in choices}), len(choices))
            for value, label in choices:
                self.assertLessEqual(len(value), 100)
                self.assertLessEqual(len(label), 100)

    def test_values_are_iana_identifiers_and_sorted_west_to_east(self):
        choices = timezone_choices(WINTER)
        self.assertEqual({value for value, _ in choices}, {zone for zone, _, _ in TIMEZONES})
        self.assertEqual(choices[0][0], "Pacific/Honolulu")
        self.assertEqual(choices[-1][0], "Pacific/Auckland")


if __name__ == "__main__":
    unittest.main()

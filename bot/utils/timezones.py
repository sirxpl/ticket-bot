"""Timezone choices for the ticket form.

Every choice is stored as an IANA identifier (for example ``America/New_York``)
and its label is built from the current date, so daylight saving time is
reflected automatically (``GMT-5 / ET ... - standard time`` in winter,
``GMT-4 / ET ... - daylight time`` in summer) with no manual updates.

Discord select menus hold at most 25 options, so this is a curated list that
covers the common offsets rather than every IANA zone.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

# (IANA identifier, short code, example places)
TIMEZONES: tuple[tuple[str, str, str], ...] = (
    ("Pacific/Honolulu", "HST", "Honolulu"),
    ("America/Anchorage", "AKT", "Anchorage"),
    ("America/Los_Angeles", "PT", "Los Angeles, Vancouver"),
    ("America/Denver", "MT", "Denver, Calgary"),
    ("America/Phoenix", "MST", "Phoenix"),
    ("America/Chicago", "CT", "Chicago, Dallas, Winnipeg"),
    ("America/Mexico_City", "CST", "Mexico City"),
    ("America/New_York", "ET", "New York, Toronto"),
    ("America/Halifax", "AT", "Halifax"),
    ("America/Sao_Paulo", "BRT", "Sao Paulo, Buenos Aires"),
    ("Etc/UTC", "UTC", "Universal time"),
    ("Europe/London", "UK", "London, Dublin, Lisbon"),
    ("Europe/Paris", "CET", "Paris, Berlin, Madrid, Rome"),
    ("Europe/Athens", "EET", "Athens, Helsinki, Kyiv"),
    ("Africa/Johannesburg", "SAST", "Johannesburg"),
    ("Europe/Moscow", "MSK", "Moscow, Istanbul, Riyadh"),
    ("Asia/Dubai", "GST", "Dubai"),
    ("Asia/Karachi", "PKT", "Karachi, Tashkent"),
    ("Asia/Kolkata", "IST", "India"),
    ("Asia/Dhaka", "BDT", "Dhaka"),
    ("Asia/Bangkok", "ICT", "Bangkok, Jakarta, Hanoi"),
    ("Asia/Singapore", "SGT", "Singapore, Hong Kong, Manila"),
    ("Asia/Tokyo", "JST", "Tokyo, Seoul"),
    ("Australia/Sydney", "AET", "Sydney, Melbourne"),
    ("Pacific/Auckland", "NZT", "Auckland"),
)

assert len(TIMEZONES) <= 25, "Discord select menus allow at most 25 options"

_BY_ID = {zone_id: (code, places) for zone_id, code, places in TIMEZONES}


def _format_offset(delta: datetime.timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    return f"GMT{sign}{hours}" + (f":{rest:02d}" if rest else "")


def _observes_dst(zone: ZoneInfo, year: int) -> bool:
    january = datetime.datetime(year, 1, 1, 12, tzinfo=zone).utcoffset()
    july = datetime.datetime(year, 7, 1, 12, tzinfo=zone).utcoffset()
    return january != july


def timezone_label(zone_id: str, now: datetime.datetime | None = None) -> str:
    """Return e.g. ``GMT-5 / ET (New York, Toronto - standard time)``."""
    code, places = _BY_ID[zone_id]
    now = now or datetime.datetime.now(datetime.timezone.utc)
    zone = ZoneInfo(zone_id)
    local = now.astimezone(zone)
    label = f"{_format_offset(local.utcoffset())} / {code} ({places}"
    if _observes_dst(zone, local.year):
        label += " \u2013 daylight time" if local.dst() else " \u2013 standard time"
    return label + ")"


def timezone_choices(now: datetime.datetime | None = None) -> list[tuple[str, str]]:
    """(IANA id, label) pairs ordered from west to east by current offset."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    ordered = sorted(
        _BY_ID,
        key=lambda zone_id: (now.astimezone(ZoneInfo(zone_id)).utcoffset(), zone_id),
    )
    return [(zone_id, timezone_label(zone_id, now)) for zone_id in ordered]

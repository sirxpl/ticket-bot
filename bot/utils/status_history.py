import os
import json
import uuid
from datetime import datetime, timedelta, timezone

from utils.db import get_db

DATA_DIR = "data"
STATUS_FILE = os.path.join(DATA_DIR, "status_history.json")

os.makedirs(DATA_DIR, exist_ok=True)

_DEFAULTS = {"daily": {}, "incidents": [], "last_state": None, "open_incident_id": None}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_status_data():
    db = get_db()
    if db is not None:
        doc = db.status_history.find_one({"_id": "singleton"})
        if not doc:
            return dict(_DEFAULTS)
        for k, v in _DEFAULTS.items():
            doc.setdefault(k, v)
        return doc

    if not os.path.exists(STATUS_FILE):
        return dict(_DEFAULTS)
    try:
        with open(STATUS_FILE, "r") as f:
            data = json.load(f)
        for k, v in _DEFAULTS.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return dict(_DEFAULTS)


def _save(data: dict):
    db = get_db()
    if db is not None:
        data = dict(data)
        data["_id"] = "singleton"
        db.status_history.replace_one({"_id": "singleton"}, data, upsert=True)
        return
    with open(STATUS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_check(is_online: bool):
    """Called periodically (roughly once a minute) from a background thread
    in main.py. Tallies today's up/down checks for the uptime bar, and
    auto-opens/resolves an incident whenever the online state flips —
    nothing needs to be logged by hand for a simple disconnect/reconnect.
    """
    data = get_status_data()
    today = datetime.now(timezone.utc).date().isoformat()

    daily = data.setdefault("daily", {})
    bucket = daily.setdefault(today, {"checks": 0, "up": 0})
    bucket["checks"] += 1
    if is_online:
        bucket["up"] += 1

    last_state = data.get("last_state")
    now = _now_iso()

    if last_state is True and is_online is False:
        incident = {
            "id": uuid.uuid4().hex[:8],
            "title": "Bot Disconnected",
            "created_at": now,
            "updates": [{
                "status": "identified",
                "message": "The bot lost its connection to Discord. We're looking into it.",
                "timestamp": now,
            }],
        }
        data.setdefault("incidents", []).append(incident)
        data["open_incident_id"] = incident["id"]
    elif last_state is False and is_online is True:
        open_id = data.get("open_incident_id")
        if open_id:
            for inc in data.get("incidents", []):
                if inc["id"] == open_id:
                    inc["updates"].append({
                        "status": "resolved",
                        "message": "The bot has reconnected to Discord.",
                        "timestamp": now,
                    })
            data["open_incident_id"] = None

    data["last_state"] = is_online
    _save(data)


def get_daily_uptime(days: int = 90):
    """Returns a list (oldest -> newest) of {date, pct} for the last `days`
    calendar days. pct is None for days with no recorded checks yet (e.g.
    before this feature was deployed), rendered as an empty/grey bar."""
    data = get_status_data()
    daily = data.get("daily", {})
    today = datetime.now(timezone.utc).date()
    out = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        bucket = daily.get(d)
        if bucket and bucket.get("checks"):
            pct = round((bucket["up"] / bucket["checks"]) * 100, 1)
        else:
            pct = None
        out.append({"date": d, "pct": pct})
    return out


def get_overall_uptime_pct(days: int = 90):
    points = [p["pct"] for p in get_daily_uptime(days) if p["pct"] is not None]
    if not points:
        return None
    return round(sum(points) / len(points), 2)


def get_incidents_by_month(months: int = 3):
    """Returns incidents grouped by month label ('Aug 2026'), most recent
    month first, each incident's updates newest-first. Months with no
    incidents are included so the page can show 'No notices this month'."""
    data = get_status_data()
    incidents = data.get("incidents", [])

    today = datetime.now(timezone.utc).date().replace(day=1)
    month_keys = []
    for i in range(months):
        y, m = today.year, today.month - i
        while m <= 0:
            m += 12
            y -= 1
        month_keys.append((y, m))

    grouped = {mk: [] for mk in month_keys}
    for inc in incidents:
        try:
            created = datetime.fromisoformat(inc["created_at"])
        except Exception:
            continue
        key = (created.year, created.month)
        if key in grouped:
            inc_copy = dict(inc)
            inc_copy["updates"] = list(reversed(inc.get("updates", [])))
            grouped[key].append(inc_copy)

    result = []
    for (y, m) in month_keys:
        label = datetime(y, m, 1).strftime("%b %Y")
        items = sorted(grouped[(y, m)], key=lambda i: i["created_at"], reverse=True)
        result.append({"label": label, "incidents": items})
    return result

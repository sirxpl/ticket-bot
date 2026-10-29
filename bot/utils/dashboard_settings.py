"""Bounded dashboard list edits and ticket-logging presentation/configuration."""
import datetime
import json
import os
import re
import threading

from pymongo import ReturnDocument
from utils import access, storage

_file_lock = threading.RLock()
ACCESS_LISTS = {
    "allowed_users": "dashboard users", "allowed_roles": "dashboard roles",
    "globally_blocked_users": "blocked users", "blacklist_roles": "ticket blacklist roles",
    "carry_manager_roles": "carry manager roles", "ticket_viewer_roles": "ticket viewer roles",
    "powerful_command_roles": "powerful command roles", "powerful_command_users": "powerful command users",
    "basic_command_roles": "basic command roles", "basic_command_users": "basic command users",
    "moderation_command_roles": "moderation roles", "moderation_command_users": "moderation users",
    "pin_message_roles": "message pinning roles", "pin_message_users": "message pinning users",
    "transcripts_roles": "transcript roles", "analytics_roles": "analytics roles",
    "carry_application_verdict_roles": "application reviewer roles", "remove_cooldown_roles": "cooldown manager roles",
}
DELIVERY_EVENTS = {"opened": "Ticket opened", "closed": "Ticket closed", "renamed": "Ticket renamed",
                   "ticket_started": "Work started", "ticket_finished": "Work finished"}
LOG_ACTIONS = {"created": "Opened", "closed": "Closed", "renamed": "Renamed", "ticket_started": "Work started",
               "ticket_finished": "Work finished", "deleted": "Channel deleted", "delete_failed": "Delete failed",
               "blacklist_added": "Blacklist added", "blacklist_removed": "Blacklist removed",
               "staff_message": "Staff message", "staff_response": "Staff response"}


def selected_ids(values):
    ids = list(dict.fromkeys(str(value) for value in values))
    if not ids or len(ids) > 100 or any(not re.fullmatch(r"[0-9]{1,20}", value) for value in ids):
        raise ValueError("Select between 1 and 100 valid entries.")
    return ids


def _database(module):
    db = module.get_db()
    if db is None and os.getenv("MONGODB_URI"):
        raise RuntimeError("MongoDB is unavailable. Your settings were not changed.")
    return db


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as source:
        return json.load(source)


def _write_json(path, value):
    temporary = path + ".dashboard.tmp"
    with open(temporary, "w", encoding="utf-8") as target:
        json.dump(value, target, indent=2)
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


def remove_access_entries(kind, values):
    if kind not in ACCESS_LISTS:
        raise ValueError("Choose a supported access list.")
    ids = selected_ids(values)
    db = _database(access)
    if db is not None:
        before = db.access_control.find_one_and_update(
            {"_id": "singleton"}, {"$pull": {kind: {"$in": ids + [int(value) for value in ids if int(value) <= 2**63 - 1]}}},
            projection={kind: 1}, return_document=ReturnDocument.BEFORE,
        ) or {}
        return sum(str(value) in ids for value in before.get(kind, []))
    with _file_lock:
        settings = _read_json(access.ACCESS_FILE, {})
        old = settings.get(kind, [])
        settings[kind] = [value for value in old if str(value) not in ids]
        changed = len(old) - len(settings[kind])
        if changed:
            _write_json(access.ACCESS_FILE, settings)
        return changed


def remove_blocked_badges(values):
    ids = selected_ids(values)
    db = _database(storage)
    for _ in range(3):
        with _file_lock:
            settings = (db.bot_settings.find_one({"_id": "singleton"}, {"ticket_badge_verification": 1}) or {}) if db is not None else _read_json(storage.SETTINGS_FILE, {})
            previous = settings.get("ticket_badge_verification") or {}
            config = dict(previous)
            blocked = config.get("blocked_badges")
            if blocked is None:
                blocked = [config["badge_id"]] if str(config.get("badge_id", "")).isdigit() else []
            config["blocked_badges"] = [value for value in blocked if str(value) not in ids]
            changed = len(blocked) - len(config["blocked_badges"])
            if not changed:
                return 0
            if db is not None:
                result = db.bot_settings.update_one(
                    {"_id": "singleton", "ticket_badge_verification": previous},
                    {"$set": {"ticket_badge_verification": config}},
                )
                if not result.matched_count:
                    continue
            else:
                settings["ticket_badge_verification"] = config
                _write_json(storage.SETTINGS_FILE, settings)
            return changed
    raise RuntimeError("The badge list changed while saving. Please retry.")


def save_categories_checked(categories):
    db = _database(storage)
    if db is not None:
        db.bot_settings.update_one({"_id": "singleton"}, {"$set": {"ticket_categories": categories}}, upsert=True)
    else:
        with _file_lock:
            settings = _read_json(storage.SETTINGS_FILE, {})
            settings["ticket_categories"] = categories
            _write_json(storage.SETTINGS_FILE, settings)


def get_ticket_logging_config(settings=None):
    settings = settings if settings is not None else access.get_access_settings()
    events = settings.get("ticket_log_events", list(DELIVERY_EVENTS))
    if not isinstance(events, list):
        events = list(DELIVERY_EVENTS)
    return {"enabled": settings.get("ticket_logging_enabled", True) is True,
            "events": [key for key in events if key in DELIVERY_EVENTS],
            **{key: settings.get(key) for key in ("log_channel_id", "blacklist_log_channel_id", "warning_log_channel_id")}}


def save_ticket_logging_config(enabled, events, channels):
    if type(enabled) is not bool or any(event not in DELIVERY_EVENTS for event in events):
        raise ValueError("Choose valid ticket logging options.")
    updates = {"ticket_logging_enabled": enabled, "ticket_log_events": list(dict.fromkeys(events))}
    for key in ("log_channel_id", "blacklist_log_channel_id", "warning_log_channel_id"):
        value = str(channels.get(key) or "").strip()
        if value and not re.fullmatch(r"[0-9]{1,20}", value):
            raise ValueError("Choose a valid logging channel.")
        updates[key] = value or None
    db = _database(access)
    if db is not None:
        db.access_control.update_one({"_id": "singleton"}, {"$set": updates}, upsert=True)
    else:
        with _file_lock:
            settings = _read_json(access.ACCESS_FILE, {})
            settings.update(updates)
            _write_json(access.ACCESS_FILE, settings)


def _timestamp(value):
    try:
        if isinstance(value, (int, float)):
            stamp = float(value)
        else:
            parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            stamp = parsed.timestamp()
        return datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).strftime("%b %d, %Y · %H:%M UTC")
    except (ValueError, TypeError, OverflowError, OSError):
        return "Time not recorded"


def ticket_log_page(page=1, action="", query="", per_page=50):
    per_page = max(1, min(100, int(per_page)))
    if action and action not in LOG_ACTIONS:
        raise ValueError("Choose a valid ticket event.")
    query = str(query).strip()[:100]
    fields = ("ticket_id", "ticket_name", "ticket_number", "creator.id", "creator.name", "executor.id", "executor.name", "actor.id", "actor.name", "reason", "old_name", "new_name")
    db = _database(storage)
    if db is not None:
        criteria = {"action": action} if action else {}
        if query:
            criteria["$or"] = [{key: {"$regex": re.escape(query), "$options": "i"}} for key in fields]
            if query.isdigit() and int(query) <= 2**63 - 1:
                criteria["$or"].extend({key: int(query)} for key in fields if key.endswith("id") or key == "ticket_number")
        total = db.ticket_logs.count_documents(criteria)
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(max(1, page), pages)
        items = list(db.ticket_logs.find(criteria, {"_id": 0}).sort("_id", -1).skip((page - 1) * per_page).limit(per_page))
    else:
        def contains(record):
            values = []
            for key in fields:
                value = record
                for part in key.split("."):
                    value = value.get(part, "") if isinstance(value, dict) else ""
                values.append(str(value).lower())
            return any(query.lower() in value for value in values)
        items = [record for record in reversed(_read_json(storage.TICKET_LOGS_FILE, []))
                 if (not action or record.get("action") == action) and (not query or contains(record))]
        total = len(items)
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(max(1, page), pages)
        items = items[(page - 1) * per_page:page * per_page]
    for record in items:
        record["action_label"] = LOG_ACTIONS.get(record.get("action"), str(record.get("action") or "Event").replace("_", " ").title())
        record["time_label"] = _timestamp(record.get("timestamp"))
        record["display_actor"] = record.get("executor") or record.get("actor") or record.get("creator") or {"name": "System"}
    return {"items": items, "total": total, "page": page, "pages": pages}

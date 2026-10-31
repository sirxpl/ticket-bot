"""Persistent Carry Team points, format validation, and activity history."""
import copy
import datetime
import json
import logging
import os
import re
import tempfile
import threading

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from utils.db import get_db

logger = logging.getLogger("points")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

DATA_DIR = "data"
POINTS_FILE = os.path.join(DATA_DIR, "points.json")
PAGE_SIZE = 25
MAX_GAMEMODES = 100
MAX_POINTS = 1_000_000_000
_LOCAL_LOCK = threading.RLock()
_INDEX_LOCK = threading.Lock()
_INDEXED_DATABASES = set()

LOG_EVENTS = {
    "manual_add": "Manual points added",
    "manual_subtract": "Manual points subtracted",
    "manual_set": "Balance set",
    "leaderboard_removed": "User removed from leaderboard",
    "excluded": "Automatic points exclusion",
    "included": "Automatic points inclusion",
    "automatic_awarded": "Automatic points awarded",
    "automatic_skipped": "Automatic points skipped",
    "duplicate_ticket": "Duplicate ticket prevented",
    "format_passed": "Format Checker passed",
    "format_rejected": "Format Checker rejected",
    "thread_created": "Thread created",
    "thread_archived": "Thread archived",
    "thread_failed": "Thread creation failed",
    "settings_updated": "Points settings updated",
}
_DEFAULT_CONFIG = {
    "command_role_ids": [],
    "thread_automation": {
        "enabled": False,
        "channel_ids": [],
        "thread_name": "{author} - {ticket_number}",
    },
    "format_checker": {
        "enabled": False,
        "channel_ids": [],
        "exempt_role_ids": [],
    },
    "gamemodes": [],
    "logging": {
        "enabled": False,
        "channel_id": None,
        "events": list(LOG_EVENTS),
    },
}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _guild_key(guild_id):
    value = str(guild_id or "global").strip()
    return value or "global"


def _database():
    db = get_db()
    if db is None and os.getenv("MONGODB_URI"):
        raise RuntimeError("MongoDB is unavailable; Points data was not changed.")
    return db


def _default_local_data():
    return {"configs": {}, "users": [], "activity": [], "awards": []}


def _load_local():
    if not os.path.exists(POINTS_FILE):
        return _default_local_data()
    try:
        with open(POINTS_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError("Points storage must be a JSON object.")
        result = _default_local_data()
        result.update(data)
        for key in ("users", "activity", "awards"):
            if not isinstance(result.get(key), list):
                result[key] = []
        if not isinstance(result.get("configs"), dict):
            result["configs"] = {}
        return result
    except Exception as error:
        logger.exception("Could not read local Points storage")
        raise RuntimeError("Points storage is unavailable; existing data was left untouched.") from error


def _save_local(data):
    os.makedirs(os.path.dirname(POINTS_FILE) or DATA_DIR, exist_ok=True)
    directory = os.path.dirname(POINTS_FILE) or DATA_DIR
    descriptor, temporary_path = tempfile.mkstemp(prefix="points-", suffix=".json", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, POINTS_FILE)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _ensure_indexes(db):
    key = id(db)
    if key in _INDEXED_DATABASES:
        return
    with _INDEX_LOCK:
        if key in _INDEXED_DATABASES:
            return
        db.points_users.create_index(
            [("guild_id", ASCENDING), ("user_id", ASCENDING)],
            unique=True,
            name="points_guild_user_unique",
        )
        db.points_users.create_index(
            [("guild_id", ASCENDING), ("hidden", ASCENDING), ("points", DESCENDING), ("user_id", ASCENDING)],
            name="points_leaderboard",
        )
        db.points_activity.create_index(
            [("guild_id", ASCENDING), ("created_at", DESCENDING)],
            name="points_activity_recent",
        )
        db.points_activity.create_index(
            [("guild_id", ASCENDING), ("user_id", ASCENDING), ("created_at", DESCENDING)],
            name="points_activity_user",
        )
        db.points_awards.create_index(
            [("guild_id", ASCENDING), ("ticket_number", ASCENDING)],
            unique=True,
            name="points_ticket_award_unique",
        )
        _INDEXED_DATABASES.add(key)


def default_points_config():
    return copy.deepcopy(_DEFAULT_CONFIG)


def _clean_id_list(values, field_name):
    if values is None:
        values = []
    if not isinstance(values, (list, tuple, set)):
        raise ValueError(f"{field_name} must be a list of Discord IDs.")
    cleaned = []
    for value in values:
        item = str(value).strip()
        if not item:
            continue
        if not item.isdigit():
            raise ValueError(f"{field_name} contains an invalid Discord ID.")
        if item not in cleaned:
            cleaned.append(item)
    if len(cleaned) > 100:
        raise ValueError(f"{field_name} cannot contain more than 100 entries.")
    return cleaned


def normalize_points_config(data):
    data = data if isinstance(data, dict) else {}
    thread = data.get("thread_automation") or {}
    checker = data.get("format_checker") or {}
    logging_config = data.get("logging") or {}
    result = {
        "command_role_ids": _clean_id_list(data.get("command_role_ids", []), "Points command roles"),
        "thread_automation": {
            "enabled": bool(thread.get("enabled", False)),
            "channel_ids": _clean_id_list(thread.get("channel_ids", []), "Thread automation channels"),
            "thread_name": str(thread.get("thread_name") or "{author} - {ticket_number}").strip()[:100],
        },
        "format_checker": {
            "enabled": bool(checker.get("enabled", False)),
            "channel_ids": _clean_id_list(checker.get("channel_ids", []), "Format Checker channels"),
            "exempt_role_ids": _clean_id_list(checker.get("exempt_role_ids", []), "Format Checker exempt roles"),
        },
        "logging": {
            "enabled": bool(logging_config.get("enabled", False)),
            "channel_id": str(logging_config.get("channel_id") or "").strip() or None,
            "events": [],
        },
        "gamemodes": [],
    }
    if not result["thread_automation"]["thread_name"]:
        result["thread_automation"]["thread_name"] = "{author} - {ticket_number}"
    log_channel = result["logging"]["channel_id"]
    if log_channel and not log_channel.isdigit():
        raise ValueError("Points logging channel must be a valid Discord channel ID.")
    requested_events = logging_config.get("events", list(LOG_EVENTS))
    if not isinstance(requested_events, list):
        raise ValueError("Points logging events must be a list.")
    result["logging"]["events"] = list(dict.fromkeys(
        str(item) for item in requested_events if str(item) in LOG_EVENTS
    ))
    gamemodes = data.get("gamemodes") or []
    if not isinstance(gamemodes, list) or len(gamemodes) > MAX_GAMEMODES:
        raise ValueError(f"Configure no more than {MAX_GAMEMODES} gamemode rules.")
    seen = set()
    for row in gamemodes:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        if len(text) > 80:
            raise ValueError("Gamemode text must be 80 characters or fewer.")
        try:
            amount = int(row.get("points", row.get("number", 0)))
        except (TypeError, ValueError):
            raise ValueError(f"Points for {text} must be a whole number.")
        if amount < 0 or amount > MAX_POINTS:
            raise ValueError(f"Points for {text} must be between 0 and {MAX_POINTS}.")
        if text.casefold() in seen:
            raise ValueError(f"The gamemode {text} is configured more than once.")
        seen.add(text.casefold())
        result["gamemodes"].append({
            "text": text,
            "points": amount,
            "reason": str(row.get("reason") or "").strip()[:500],
        })
    return result


def get_points_config(guild_id):
    guild_key = _guild_key(guild_id)
    db = _database()
    if db is not None:
        try:
            _ensure_indexes(db)
            document = db.points_config.find_one({"_id": guild_key})
            if not document:
                return default_points_config()
            document.pop("_id", None)
            return normalize_points_config(document)
        except Exception as error:
            logger.exception("Points configuration Mongo read failed")
            raise RuntimeError("Points configuration is temporarily unavailable.") from error
    with _LOCAL_LOCK:
        data = _load_local()
        return normalize_points_config(data["configs"].get(guild_key))


def save_points_config(guild_id, config):
    guild_key = _guild_key(guild_id)
    cleaned = normalize_points_config(config)
    db = _database()
    if db is not None:
        try:
            _ensure_indexes(db)
            db.points_config.replace_one({"_id": guild_key}, {"_id": guild_key, **cleaned}, upsert=True)
            return cleaned
        except Exception as error:
            logger.exception("Points configuration Mongo write failed")
            raise RuntimeError("Points configuration could not be saved.") from error
    with _LOCAL_LOCK:
        data = _load_local()
        data["configs"][guild_key] = cleaned
        _save_local(data)
    return cleaned


def find_gamemode_rule(config, gamemode):
    target = str(gamemode or "").strip().casefold()
    if not target:
        return None
    for rule in (config or {}).get("gamemodes", []):
        if str(rule.get("text") or "").strip().casefold() == target:
            return dict(rule)
    return None


def validate_format(content):
    """Scan all numbered fields and optional notes; only fields 1 and 3 award points."""
    lines = [line.strip() for line in str(content or "").splitlines() if line.strip()]
    if len(lines) < 4:
        return None, "The post must include sections 1, 2, 3, and 4."

    def numbered_line(index, label):
        match = re.fullmatch(rf"{index}\.\s+(.+)", lines[index - 1])
        if not match:
            return None, f"Section {index} must start with {index}. followed by {label}."
        return match.group(1).strip(), None

    def mentions_only(value, minimum=1, maximum=None):
        found = re.findall(r"<@!?(\d+)>", value)
        remainder = re.sub(r"<@!?(\d+)>", "", value).strip()
        if remainder or len(found) < minimum or (maximum is not None and len(found) > maximum):
            return []
        return list(dict.fromkeys(found))

    carry_text, error = numbered_line(1, "carry team member mentions")
    if error:
        return None, error
    recipients = mentions_only(carry_text, minimum=1)
    if not recipients:
        return None, "Section 1 must contain one or more user mentions and no other text."

    carried_text, error = numbered_line(2, "the carried user's mention")
    if error:
        return None, error
    carried = mentions_only(carried_text, minimum=1, maximum=1)
    if len(carried) != 1:
        return None, "Section 2 must contain exactly one user mention."

    gamemode, error = numbered_line(3, "a gamemode")
    if error:
        return None, error
    if not gamemode or len(gamemode) > 100:
        return None, "Section 3 must contain a valid gamemode."

    ticket_number, error = numbered_line(4, "a ticket number")
    if error:
        return None, error
    if not ticket_number.isdigit():
        return None, "Section 4 must contain a numeric ticket number."

    notes = None
    trailing = lines[4:]
    if trailing:
        if not trailing[0].casefold().startswith("notes:"):
            return None, "Anything after section 4 must be inside the optional Notes field."
        notes = "\n".join([trailing[0][len("notes:"):].strip(), *trailing[1:]]).strip() or None

    # Section 2 is validation-only. Section 1 alone is the point-recipient source.
    return {
        "recipients": recipients,
        "carried_user_id": carried[0],
        "gamemode": gamemode,
        "ticket_number": ticket_number,
        "notes": notes,
    }, None


def _new_user(guild_key, user_id, username=None):
    now = _now()
    return {
        "guild_id": guild_key,
        "user_id": str(user_id),
        "username": str(username or user_id)[:100],
        "points": 0,
        "excluded": False,
        "hidden": False,
        "created_at": now,
        "updated_at": now,
    }


def _points_pipeline(guild_key, user_id, username, mode, amount, now):
    if mode == "set":
        points_value = {"$literal": int(amount)}
    else:
        delta = int(amount) if mode == "add" else -int(amount)
        points_value = {"$max": [0, {"$add": [{"$ifNull": ["$points", 0]}, delta]}]}
    return [{"$set": {
        "guild_id": {"$literal": guild_key},
        "user_id": {"$literal": str(user_id)},
        "username": {"$literal": str(username or user_id)[:100]},
        "points": points_value,
        "created_at": {"$ifNull": ["$created_at", now]},
        "updated_at": now,
        "hidden": False if mode in {"set", "add"} else {"$ifNull": ["$hidden", False]},
        "excluded": {"$ifNull": ["$excluded", False]},
    }}]


def _record_local_event(data, record):
    data["activity"].append(record)
    if len(data["activity"]) > 5000:
        data["activity"] = data["activity"][-5000:]


def record_event(guild_id, action, *, user_id=None, username=None, amount=0,
                 points_after=None, reason=None, actor_id=None, actor_name=None,
                 ticket_number=None, thread_id=None, details=None):
    if action not in LOG_EVENTS:
        raise ValueError("Unknown Points log event.")
    guild_key = _guild_key(guild_id)
    record = {
        "guild_id": guild_key,
        "user_id": str(user_id) if user_id else None,
        "username": str(username or user_id or "")[:100] or None,
        "action": action,
        "amount": int(amount or 0),
        "points_after": int(points_after) if points_after is not None else None,
        "reason": str(reason or "")[:500],
        "actor_id": str(actor_id) if actor_id else None,
        "actor_name": str(actor_name or actor_id or "Automation")[:100],
        "ticket_number": str(ticket_number) if ticket_number else None,
        "thread_id": str(thread_id) if thread_id else None,
        "details": str(details or "")[:1000],
        "created_at": _now(),
    }
    db = _database()
    if db is not None:
        try:
            _ensure_indexes(db)
            db.points_activity.insert_one(record)
            return record
        except Exception as error:
            logger.exception("Points activity record failed")
            raise RuntimeError("The Points action was saved, but its audit record could not be written.") from error
    with _LOCAL_LOCK:
        data = _load_local()
        _record_local_event(data, record)
        _save_local(data)
    return record


def _write_activity(db, record, session=None):
    db.points_activity.insert_one(record, session=session)


def adjust_points(guild_id, user_id, username, mode, amount, reason, actor_id=None, actor_name=None):
    guild_key = _guild_key(guild_id)
    uid = str(user_id)
    mode = str(mode or "").lower()
    if mode not in {"set", "add", "subtract"}:
        raise ValueError("Choose Set, Add, or Subtract.")
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise ValueError("Point amount must be a whole number.")
    if amount < 0 or amount > MAX_POINTS or (mode != "set" and amount < 1):
        raise ValueError(f"Point amount must be between 1 and {MAX_POINTS}; Set may use 0.")
    clean_reason = str(reason or "").strip()[:500]
    if not clean_reason:
        raise ValueError("A reason is required for manual point changes.")
    now = _now()
    action = {"set": "manual_set", "add": "manual_add", "subtract": "manual_subtract"}[mode]
    db = _database()
    if db is not None:
        try:
            _ensure_indexes(db)
            with db.client.start_session() as session:
                with session.start_transaction():
                    db.points_users.update_one(
                        {"guild_id": guild_key, "user_id": uid},
                        _points_pipeline(guild_key, uid, username, mode, amount, now),
                        upsert=True,
                        session=session,
                    )
                    updated = db.points_users.find_one(
                        {"guild_id": guild_key, "user_id": uid}, {"_id": 0}, session=session
                    )
                    activity = {
                        "guild_id": guild_key, "user_id": uid, "username": str(username or uid)[:100],
                        "action": action, "amount": amount if mode != "subtract" else -amount,
                        "points_after": int((updated or {}).get("points", 0)), "reason": clean_reason,
                        "actor_id": str(actor_id) if actor_id else None,
                        "actor_name": str(actor_name or actor_id or "Dashboard")[:100],
                        "ticket_number": None, "thread_id": None, "details": "",
                        "created_at": now,
                    }
                    _write_activity(db, activity, session=session)
            return updated
        except Exception as error:
            logger.exception("Manual Points update failed")
            raise RuntimeError("Points could not be updated.") from error
    with _LOCAL_LOCK:
        data = _load_local()
        user = next((row for row in data["users"] if row["guild_id"] == guild_key and row["user_id"] == uid), None)
        if user is None:
            user = _new_user(guild_key, uid, username)
            data["users"].append(user)
        user["username"] = str(username or user.get("username") or uid)[:100]
        current = int(user.get("points", 0))
        user["points"] = amount if mode == "set" else max(0, current + (amount if mode == "add" else -amount))
        if mode in {"set", "add"}:
            user["hidden"] = False
        user["updated_at"] = now
        _record_local_event(data, {
            "guild_id": guild_key, "user_id": uid, "username": user["username"],
            "action": action, "amount": amount if mode != "subtract" else -amount,
            "points_after": user["points"], "reason": clean_reason,
            "actor_id": str(actor_id) if actor_id else None,
            "actor_name": str(actor_name or actor_id or "Dashboard")[:100],
            "ticket_number": None, "thread_id": None, "details": "", "created_at": now,
        })
        _save_local(data)
        return dict(user)


def remove_points_user(guild_id, user_id, username=None, reason=None, actor_id=None, actor_name=None):
    guild_key = _guild_key(guild_id)
    uid = str(user_id)
    now = _now()
    clean_reason = str(reason or "Removed from the Points leaderboard.")[:500]
    db = _database()
    if db is not None:
        try:
            _ensure_indexes(db)
            with db.client.start_session() as session:
                with session.start_transaction():
                    db.points_users.update_one(
                        {"guild_id": guild_key, "user_id": uid},
                        [{"$set": {
                            "guild_id": {"$literal": guild_key}, "user_id": {"$literal": uid},
                            "username": {"$literal": str(username or uid)[:100]},
                            "points": {"$ifNull": ["$points", 0]},
                            "hidden": True, "updated_at": now,
                            "created_at": {"$ifNull": ["$created_at", now]},
                            "excluded": {"$ifNull": ["$excluded", False]},
                        }}],
                        upsert=True,
                        session=session,
                    )
                    updated = db.points_users.find_one(
                        {"guild_id": guild_key, "user_id": uid}, {"points": 1}, session=session
                    )
                    _write_activity(db, {
                        "guild_id": guild_key, "user_id": uid, "username": str(username or uid)[:100],
                        "action": "leaderboard_removed", "amount": 0,
                        "points_after": int((updated or {}).get("points", 0)),
                        "reason": clean_reason, "actor_id": str(actor_id) if actor_id else None,
                        "actor_name": str(actor_name or actor_id or "Discord")[:100],
                        "ticket_number": None, "thread_id": None, "details": "", "created_at": now,
                    }, session=session)
            return True
        except Exception as error:
            logger.exception("Removing Points user failed")
            raise RuntimeError("The user could not be removed from the leaderboard.") from error
    with _LOCAL_LOCK:
        data = _load_local()
        user = next((row for row in data["users"] if row["guild_id"] == guild_key and row["user_id"] == uid), None)
        if user is None:
            user = _new_user(guild_key, uid, username)
            data["users"].append(user)
        user.update({"username": str(username or uid)[:100], "hidden": True, "updated_at": now})
        _record_local_event(data, {
            "guild_id": guild_key, "user_id": uid, "username": user["username"],
            "action": "leaderboard_removed", "amount": 0, "points_after": int(user.get("points", 0)),
            "reason": clean_reason, "actor_id": str(actor_id) if actor_id else None,
            "actor_name": str(actor_name or actor_id or "Discord")[:100],
            "ticket_number": None, "thread_id": None, "details": "", "created_at": now,
        })
        _save_local(data)
    return True


def set_user_excluded(guild_id, user_id, username=None, excluded=True, reason=None, actor_id=None, actor_name=None):
    guild_key = _guild_key(guild_id)
    uid = str(user_id)
    now = _now()
    is_excluded = bool(excluded)
    clean_reason = str(reason or ("Excluded from automatic points." if is_excluded else "Included for automatic points again."))[:500]
    action = "excluded" if is_excluded else "included"
    db = _database()
    if db is not None:
        try:
            _ensure_indexes(db)
            with db.client.start_session() as session:
                with session.start_transaction():
                    db.points_users.update_one(
                        {"guild_id": guild_key, "user_id": uid},
                        {
                            "$set": {"username": str(username or uid)[:100], "excluded": is_excluded, "updated_at": now},
                            "$setOnInsert": {
                                "guild_id": guild_key, "user_id": uid, "points": 0,
                                "hidden": False, "created_at": now,
                            },
                        },
                        upsert=True,
                        session=session,
                    )
                    _write_activity(db, {
                        "guild_id": guild_key, "user_id": uid, "username": str(username or uid)[:100],
                        "action": action, "amount": 0, "points_after": None, "reason": clean_reason,
                        "actor_id": str(actor_id) if actor_id else None,
                        "actor_name": str(actor_name or actor_id or "Discord")[:100],
                        "ticket_number": None, "thread_id": None, "details": "", "created_at": now,
                    }, session=session)
            return True
        except Exception as error:
            logger.exception("Updating Points exclusion failed")
            raise RuntimeError("The automatic-points exclusion could not be updated.") from error
    with _LOCAL_LOCK:
        data = _load_local()
        user = next((row for row in data["users"] if row["guild_id"] == guild_key and row["user_id"] == uid), None)
        if user is None:
            user = _new_user(guild_key, uid, username)
            data["users"].append(user)
        user["username"] = str(username or uid)[:100]
        user["excluded"] = is_excluded
        user["updated_at"] = now
        _record_local_event(data, {
            "guild_id": guild_key, "user_id": uid, "username": user["username"],
            "action": action, "amount": 0, "points_after": int(user.get("points", 0)),
            "reason": clean_reason, "actor_id": str(actor_id) if actor_id else None,
            "actor_name": str(actor_name or actor_id or "Discord")[:100],
            "ticket_number": None, "thread_id": None, "details": "", "created_at": now,
        })
        _save_local(data)
    return True


def award_auto_points(guild_id, ticket_number, gamemode, recipient_ids, amount, reason, usernames=None, thread_id=None):
    """Award a validated ticket once; MongoDB updates are committed as a transaction."""
    guild_key = _guild_key(guild_id)
    ticket = str(ticket_number).strip()
    recipients = list(dict.fromkeys(str(item) for item in (recipient_ids or []) if str(item).isdigit()))
    if not ticket or not ticket.isdigit():
        raise ValueError("A valid ticket number is required for automatic points.")
    if not recipients:
        return {"awarded": [], "excluded": [], "duplicate": False}
    amount = int(amount)
    if amount < 0 or amount > MAX_POINTS:
        raise ValueError("Automatic point amount is outside the supported range.")
    usernames = usernames or {}
    now = _now()
    award_record = {
        "guild_id": guild_key, "ticket_number": ticket, "gamemode": str(gamemode),
        "amount": amount, "reason": str(reason or "")[:500],
        "thread_id": str(thread_id) if thread_id else None,
        "recipient_ids": recipients, "created_at": now,
    }
    db = _database()
    if db is not None:
        try:
            _ensure_indexes(db)
            awarded, excluded = [], []
            with db.client.start_session() as session:
                with session.start_transaction():
                    db.points_awards.insert_one(award_record, session=session)
                    for uid in recipients:
                        existing = db.points_users.find_one(
                            {"guild_id": guild_key, "user_id": uid}, {"excluded": 1}, session=session
                        )
                        if existing and existing.get("excluded"):
                            excluded.append(uid)
                            continue
                        uname = str(usernames.get(uid) or uid)[:100]
                        db.points_users.update_one(
                            {"guild_id": guild_key, "user_id": uid},
                            _points_pipeline(guild_key, uid, uname, "add", amount, now),
                            upsert=True,
                            session=session,
                        )
                        updated = db.points_users.find_one(
                            {"guild_id": guild_key, "user_id": uid}, {"points": 1}, session=session
                        )
                        _write_activity(db, {
                            "guild_id": guild_key, "user_id": uid, "username": uname,
                            "action": "automatic_awarded", "amount": amount,
                            "points_after": int((updated or {}).get("points", 0)),
                            "reason": str(reason or f"Automatic {gamemode} points")[:500],
                            "actor_id": None, "actor_name": "Format Checker",
                            "ticket_number": ticket, "thread_id": str(thread_id) if thread_id else None,
                            "details": str(gamemode), "created_at": now,
                        }, session=session)
                        awarded.append(uid)
                    db.points_awards.update_one(
                        {"guild_id": guild_key, "ticket_number": ticket},
                        {"$set": {"awarded_ids": awarded, "excluded_ids": excluded, "completed_at": _now()}},
                        session=session,
                    )
            return {"awarded": awarded, "excluded": excluded, "duplicate": False}
        except DuplicateKeyError:
            return {"awarded": [], "excluded": [], "duplicate": True}
        except Exception as error:
            logger.exception("Automatic Points transaction failed for ticket %s", ticket)
            raise RuntimeError("Automatic points could not be safely recorded; the award was not completed.") from error
    with _LOCAL_LOCK:
        data = _load_local()
        if any(row.get("guild_id") == guild_key and str(row.get("ticket_number")) == ticket for row in data["awards"]):
            return {"awarded": [], "excluded": [], "duplicate": True}
        awarded, excluded = [], []
        for uid in recipients:
            user = next((row for row in data["users"] if row["guild_id"] == guild_key and row["user_id"] == uid), None)
            if user and user.get("excluded"):
                excluded.append(uid)
                continue
            if user is None:
                user = _new_user(guild_key, uid, usernames.get(uid))
                data["users"].append(user)
            user["username"] = str(usernames.get(uid) or user.get("username") or uid)[:100]
            user["points"] = int(user.get("points", 0)) + amount
            user["hidden"] = False
            user["updated_at"] = now
            _record_local_event(data, {
                "guild_id": guild_key, "user_id": uid, "username": user["username"],
                "action": "automatic_awarded", "amount": amount, "points_after": user["points"],
                "reason": str(reason or f"Automatic {gamemode} points")[:500],
                "actor_id": None, "actor_name": "Format Checker", "ticket_number": ticket,
                "thread_id": str(thread_id) if thread_id else None, "details": str(gamemode), "created_at": now,
            })
            awarded.append(uid)
        data["awards"].append({**award_record, "awarded_ids": awarded, "excluded_ids": excluded})
        _save_local(data)
        return {"awarded": awarded, "excluded": excluded, "duplicate": False}


def get_points_leaderboard(guild_id, page=1, query=""):
    guild_key = _guild_key(guild_id)
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    query = str(query or "").strip()[:100]
    db = _database()
    if db is not None:
        try:
            _ensure_indexes(db)
            selector = {"guild_id": guild_key, "hidden": {"$ne": True}}
            if query:
                selector["$or"] = [
                    {"user_id": query},
                    {"username": {"$regex": re.escape(query), "$options": "i"}},
                ]
            total = db.points_users.count_documents(selector)
            rows = list(db.points_users.find(
                selector, {"_id": 0, "guild_id": 0}
            ).sort([("points", DESCENDING), ("user_id", ASCENDING)])
                .skip((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))
        except Exception as error:
            logger.exception("Points leaderboard Mongo read failed")
            raise RuntimeError("The Points leaderboard is temporarily unavailable.") from error
    else:
        with _LOCAL_LOCK:
            data = _load_local()
            rows = [
                dict(row) for row in data["users"]
                if row.get("guild_id") == guild_key and not row.get("hidden")
            ]
        if query:
            lowered = query.casefold()
            rows = [row for row in rows if query == str(row.get("user_id")) or lowered in str(row.get("username", "")).casefold()]
        rows.sort(key=lambda row: (-int(row.get("points", 0)), str(row.get("user_id", ""))))
        total = len(rows)
        rows = rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > pages:
        return get_points_leaderboard(guild_key, pages, query)
    for row in rows:
        row["points"] = int(row.get("points", 0))
    return {"items": rows, "total": total, "page": page, "pages": pages, "per_page": PAGE_SIZE, "query": query}


def get_points_activity(guild_id, page=1, query="", action=""):
    guild_key = _guild_key(guild_id)
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    query = str(query or "").strip()[:100]
    action = str(action or "").strip()
    if action and action not in LOG_EVENTS:
        action = ""
    db = _database()
    if db is not None:
        try:
            _ensure_indexes(db)
            selector = {"guild_id": guild_key}
            if action:
                selector["action"] = action
            if query:
                escaped = re.escape(query)
                selector["$or"] = [
                    {"user_id": query}, {"actor_id": query},
                    {"username": {"$regex": escaped, "$options": "i"}},
                    {"reason": {"$regex": escaped, "$options": "i"}},
                    {"ticket_number": query},
                ]
            total = db.points_activity.count_documents(selector)
            rows = list(db.points_activity.find(
                selector, {"_id": 0, "guild_id": 0}
            ).sort("created_at", DESCENDING).skip((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))
        except Exception as error:
            logger.exception("Points activity Mongo read failed")
            raise RuntimeError("Points activity is temporarily unavailable.") from error
    else:
        with _LOCAL_LOCK:
            data = _load_local()
            rows = [dict(row) for row in data["activity"] if row.get("guild_id") == guild_key]
        if action:
            rows = [row for row in rows if row.get("action") == action]
        if query:
            lowered = query.casefold()
            rows = [row for row in rows if any(
                lowered in str(row.get(field) or "").casefold()
                for field in ("user_id", "actor_id", "username", "reason", "ticket_number")
            )]
        rows.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
        total = len(rows)
        rows = rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > pages:
        return get_points_activity(guild_key, pages, query, action)
    return {"items": rows, "total": total, "page": page, "pages": pages, "per_page": PAGE_SIZE, "query": query, "action": action}


def has_points_command_access(member, guild_id):
    """Admins pass; configured Points roles override the Carry Manager role fallback."""
    from utils.access import get_access_settings, is_admin, has_carry_manager_access

    user_id = getattr(member, "id", None)
    if is_admin(user_id):
        return True
    config = get_points_config(guild_id)
    allowed_roles = {str(item) for item in config.get("command_role_ids", [])}
    member_roles = {str(role.id) for role in getattr(member, "roles", [])}
    if allowed_roles:
        return bool(allowed_roles.intersection(member_roles))
    access = get_access_settings()
    manager_roles = access.get("carry_manager_roles", [])
    if manager_roles:
        return bool({str(item) for item in manager_roles}.intersection(member_roles))
    return has_carry_manager_access(user_id, member_roles)


def has_points_manager_access(member):
    from utils.access import is_admin, has_carry_manager_access
    return is_admin(getattr(member, "id", None)) or has_carry_manager_access(
        getattr(member, "id", None), [str(role.id) for role in getattr(member, "roles", [])]
    )


__all__ = [
    "LOG_EVENTS", "PAGE_SIZE", "default_points_config", "normalize_points_config",
    "get_points_config", "save_points_config", "find_gamemode_rule", "validate_format",
    "record_event", "adjust_points", "remove_points_user", "set_user_excluded",
    "award_auto_points", "get_points_leaderboard", "get_points_activity",
    "has_points_command_access", "has_points_manager_access",
]

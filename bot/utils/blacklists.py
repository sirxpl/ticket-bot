"""Configurable blacklist types and durable records, with legacy migration."""
import copy
import datetime
import json
import math
import os
import re
import threading
import time

from pymongo.errors import DuplicateKeyError
from utils import access, storage

_lock = threading.RLock()
LEGACY_VOIDCORE_ROLE = "1535963860203470858"


def get_types(guild=None):
    """Only configured roles; no built-in Regular/Voidcore command choices."""
    settings = access.get_access_settings()
    result, seen = [], set()
    for entry in settings.get("blacklist_types", []):
        role = str(entry.get("role_id") or "")
        if entry.get("id") in {"regular", "voidcore"} or not role.isdigit() or role in seen:
            continue
        result.append({**copy.deepcopy(entry), "id": role, "role_id": role})
        seen.add(role)
    for value in settings.get("blacklist_roles", []):
        role = str(value)
        if not role.isdigit() or role in seen:
            continue
        resolved = guild.get_role(int(role)) if guild else None
        result.append(dict(id=role, role_id=role, name=resolved.name if resolved else "Blacklist role " + role,
                           reason="Ticket access restricted by this role.", block_all=True))
        seen.add(role)
    return result


def save_types(rows):
    from utils.dashboard_settings import _database, _read_json, _write_json, _file_lock
    if len(rows) > 25:
        raise ValueError("Use at most 25 blacklist roles.")
    result, seen = [], set()
    for row in rows:
        key = str(row.get("role_id", "")).strip()
        name = str(row.get("name", "")).strip()
        role = str(row.get("role_id", "")).strip()
        reason = str(row.get("reason", "")).strip()
        if key in seen:
            raise ValueError("Use each blacklist role only once.")
        if not name or len(name) > 80 or not reason or len(reason) > 500:
            raise ValueError("Supply a name (up to 80 characters) and a reason (up to 500).")
        if not re.fullmatch(r"[0-9]{1,20}", role):
            raise ValueError("Choose a valid blacklist role.")
        block_all = row.get("block_all") is True
        result.append(dict(id=key, name=name, role_id=role, reason=reason, block_all=block_all))
        seen.add(key)
    db = _database(access)
    if db is not None:
        db.access_control.update_one({"_id": "singleton"}, {"$set": {"blacklist_types": result, "blacklist_roles": [row["role_id"] for row in result if row["block_all"]]}}, upsert=True)
    else:
        with _file_lock:
            data = _read_json(access.ACCESS_FILE, {})
            data["blacklist_types"] = result
            data["blacklist_roles"] = [row["role_id"] for row in result if row["block_all"]]
            _write_json(access.ACCESS_FILE, data)
    return result


def _file_rows():
    if not os.path.exists(storage.BLACKLIST_FILE):
        return []
    with open(storage.BLACKLIST_FILE, encoding="utf-8") as source:
        rows = json.load(source).get("blacklisted_users", [])
    return [dict(row) if isinstance(row, dict) else {"user_id": str(row), "reason": "No reason provided."} for row in rows]


def _database():
    db = storage.get_db()
    if db is None and os.getenv("MONGODB_URI"):
        raise RuntimeError("MongoDB is unavailable; blacklist data was not changed.")
    if db is not None and db.ticket_blacklists.find_one({"_id": "singleton"}) is None:
        try:
            db.ticket_blacklists.insert_one({"_id": "singleton", "blacklisted_users": _file_rows()})
        except DuplicateKeyError:
            pass
    return db


def get_data(include_expired=False):
    db = _database()
    rows = (db.ticket_blacklists.find_one({"_id": "singleton"}) or {}).get("blacklisted_users", []) if db is not None else _file_rows()
    if not include_expired:
        rows = [row for row in rows if not row.get("expires_ts") or float(row["expires_ts"]) > time.time()]
    return {"blacklisted_users": rows}


def _change(transform):
    from utils.dashboard_settings import _write_json
    with _lock:
        db = _database()
        for _ in range(5):
            before = get_data(include_expired=True)["blacklisted_users"]
            after, result = transform(copy.deepcopy(before))
            if db is not None:
                saved = db.ticket_blacklists.update_one(
                    {"_id": "singleton", "blacklisted_users": before}, {"$set": {"blacklisted_users": after}})
                if not saved.matched_count:
                    continue
            else:
                _write_json(storage.BLACKLIST_FILE, {"blacklisted_users": after})
            return result
    raise RuntimeError("Blacklist changed while saving. Please retry.")


def add(user_id, reason="No reason provided.", added_by=None, hours=None, blacklist_type="regular", **metadata):
    if hours is not None and (not math.isfinite(hours) or hours <= 0 or hours > 87600):
        raise ValueError("Duration must be between 0 and 87600 hours.")
    row = dict(user_id=str(user_id), reason=reason, added_by=str(added_by) if added_by else None,
               added_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
               expires_ts=time.time() + hours * 3600 if hours else None, blacklist_type=blacklist_type, **metadata)
    def transform(rows):
        if any(str(entry.get("user_id")) == str(user_id) for entry in rows):
            raise ValueError("Remove the existing blacklist first, including any expired role awaiting cleanup.")
        return rows + [row], True
    return _change(transform)


def remove(user_id, expected_entry=None):
    def transform(rows):
        current = next((row for row in rows if str(row.get("user_id")) == str(user_id)), None)
        if expected_entry is not None and current != expected_entry:
            raise RuntimeError("The blacklist changed. Reload and retry.")
        kept = [row for row in rows if str(row.get("user_id")) != str(user_id)]
        return kept, len(kept) < len(rows)
    return _change(transform)


def role_id(entry):
    # Preserve the exact assigned role when types are renamed, edited or removed.
    return str(entry.get("role_id", (LEGACY_VOIDCORE_ROLE if entry.get("blacklist_type") == "voidcore" else "")))


def blocks(entry, category):
    if entry.get("expires_ts") and float(entry["expires_ts"]) <= time.time():
        return False
    if entry.get("block_all", entry.get("blacklist_type", "regular") != "voidcore"):
        return True
    return role_id(entry) in {str(value) for value in (category or {}).get("blacklist_roles", [])}


async def remove_member(member, entry, reason):
    assigned = role_id(entry)
    role = member.guild.get_role(int(assigned)) if assigned else None
    if role and any(item.id == role.id for item in member.roles):
        await member.remove_roles(role, reason=reason[:500])
    # Failed role removal must leave the record available for retry.
    return remove(member.id, expected_entry=entry)


def restriction_reason(member, category):
    entry = next((row for row in get_data()["blacklisted_users"]
                  if str(row.get("user_id")) == str(member.id)), None)
    if entry and blocks(entry, category):
        return entry.get("reason") or "This ticket type is restricted."
    roles = {str(role.id) for role in getattr(member, "roles", [])}
    category_roles = {str(role) for role in (category or {}).get("blacklist_roles", [])}
    for profile in get_types(getattr(member, "guild", None)):
        if profile["role_id"] in roles and (profile["block_all"] or profile["role_id"] in category_roles):
            return profile.get("reason") or "This blacklist role restricts ticket access."
    if roles.intersection(category_roles):
        return "One of your roles is blocked from creating this ticket type."
    return None

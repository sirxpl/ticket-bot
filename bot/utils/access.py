import os
import json

from utils.db import get_db

DATA_DIR = "data"
ACCESS_FILE = os.path.join(DATA_DIR, "access.json")

os.makedirs(DATA_DIR, exist_ok=True)

_DEFAULTS = {"allowed_users": [], "allowed_roles": [], "log_channel_id": None}


def get_access_settings():
    """Return the access-control config: allowed viewer users/roles and the
    channel ticket activity gets logged to. Reads from MongoDB when
    MONGODB_URI is configured, otherwise from the local access.json file."""
    db = get_db()
    if db is not None:
        doc = db.access_control.find_one({"_id": "singleton"})
        if not doc:
            return dict(_DEFAULTS)
        doc.setdefault("allowed_users", [])
        doc.setdefault("allowed_roles", [])
        doc.setdefault("log_channel_id", None)
        return doc

    if not os.path.exists(ACCESS_FILE):
        return dict(_DEFAULTS)
    try:
        with open(ACCESS_FILE, "r") as f:
            data = json.load(f)
        data.setdefault("allowed_users", [])
        data.setdefault("allowed_roles", [])
        data.setdefault("log_channel_id", None)
        return data
    except Exception:
        return dict(_DEFAULTS)


def _save(data: dict):
    db = get_db()
    if db is not None:
        data = dict(data)
        data["_id"] = "singleton"
        db.access_control.replace_one({"_id": "singleton"}, data, upsert=True)
        return
    with open(ACCESS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def add_allowed_user(user_id: str) -> bool:
    data = get_access_settings()
    user_id = str(user_id)
    if user_id not in data["allowed_users"]:
        data["allowed_users"].append(user_id)
        _save(data)
        return True
    return False


def remove_allowed_user(user_id: str) -> bool:
    data = get_access_settings()
    user_id = str(user_id)
    if user_id in data["allowed_users"]:
        data["allowed_users"].remove(user_id)
        _save(data)
        return True
    return False


def add_allowed_role(role_id: str) -> bool:
    data = get_access_settings()
    role_id = str(role_id)
    if role_id not in data["allowed_roles"]:
        data["allowed_roles"].append(role_id)
        _save(data)
        return True
    return False


def remove_allowed_role(role_id: str) -> bool:
    data = get_access_settings()
    role_id = str(role_id)
    if role_id in data["allowed_roles"]:
        data["allowed_roles"].remove(role_id)
        _save(data)
        return True
    return False


def set_log_channel(channel_id):
    data = get_access_settings()
    data["log_channel_id"] = str(channel_id) if channel_id else None
    _save(data)


def get_log_channel_id():
    return get_access_settings().get("log_channel_id")


def has_dashboard_access(user_id: str, member_role_ids=None) -> bool:
    """Return True if this Discord user is allowed to view the dashboard.

    An ADMIN_USER_IDS env var (comma-separated Discord user IDs) always
    passes, regardless of the allow-list below — this exists so a bad
    Access Control entry can never fully lock every admin out.

    Otherwise, access is opt-in: until at least one user or role has been
    added to the allow-list, everyone who logs in with Discord can view the
    dashboard (this is the original behavior, kept as the default so nobody
    gets locked out before setting the allow-list up). Once at least one
    entry exists, only matching users/roles/admins are let in.
    """
    admin_ids = {
        uid.strip() for uid in os.getenv("ADMIN_USER_IDS", "").split(",") if uid.strip()
    }
    if str(user_id) in admin_ids:
        return True

    data = get_access_settings()
    allowed_users = data.get("allowed_users", [])
    allowed_roles = data.get("allowed_roles", [])

    if not allowed_users and not allowed_roles:
        return True

    if str(user_id) in allowed_users:
        return True

    if member_role_ids:
        role_ids = {str(r) for r in member_role_ids}
        if role_ids.intersection(set(allowed_roles)):
            return True

    return False

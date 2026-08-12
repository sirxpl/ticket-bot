import os
import json

DATA_DIR = "data"
ACCESS_FILE = os.path.join(DATA_DIR, "access.json")

os.makedirs(DATA_DIR, exist_ok=True)


def get_access_settings():
    """Return the access-control config: allowed viewer users/roles and the
    channel ticket activity gets logged to."""
    if not os.path.exists(ACCESS_FILE):
        return {"allowed_users": [], "allowed_roles": [], "log_channel_id": None}
    try:
        with open(ACCESS_FILE, "r") as f:
            data = json.load(f)
        data.setdefault("allowed_users", [])
        data.setdefault("allowed_roles", [])
        data.setdefault("log_channel_id", None)
        return data
    except Exception:
        return {"allowed_users": [], "allowed_roles": [], "log_channel_id": None}


def _save(data: dict):
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

    Access is opt-in: until at least one user or role has been added to the
    allow-list, everyone who logs in with Discord can view the dashboard
    (this is the existing behavior, kept as the default so nobody gets
    locked out before setting the allow-list up). Once at least one entry
    exists, only matching users/roles are let in.
    """
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

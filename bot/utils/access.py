import os
import json
import hashlib
import secrets
import time

from utils.db import get_db

DATA_DIR = "data"
ACCESS_FILE = os.path.join(DATA_DIR, "access.json")

os.makedirs(DATA_DIR, exist_ok=True)

_DEFAULTS = {
    "allowed_users": [],
    "allowed_roles": [],
    "log_channel_id": None,
    "blacklist_log_channel_id": None,
    "warning_log_channel_id": None,
    "blacklist_roles": [],
    "carry_manager_roles": [],
    "ticket_viewer_roles": [],
    "powerful_command_roles": [],
    "powerful_command_users": [],
    "basic_command_roles": [],
    "basic_command_users": [],
    "transcripts_roles": [],
    "analytics_roles": [],
    "remove_cooldown_roles": [],
    "moderation_command_roles": [],
    "moderation_command_users": [],
    "globally_blocked_users": [],
    "terms_unblock_tokens": [],
}

# Always treated as admin, on top of whatever's in the ADMIN_USER_IDS env
# var — this is the person who set this feature up, kept here so Access
# Control itself can never be fully locked out from everyone.
SUPER_ADMIN_FALLBACK_IDS = {"777341204047331348"}


def get_admin_ids():
    env_ids = {
        uid.strip() for uid in os.getenv("ADMIN_USER_IDS", "").split(",") if uid.strip()
    }
    return env_ids | SUPER_ADMIN_FALLBACK_IDS


def is_admin(user_id) -> bool:
    """True for admins: always have full dashboard access, and are the only
    ones who can view or edit the Access Control page."""
    return str(user_id) in get_admin_ids()


def get_access_settings():
    """Return the access-control config: allowed viewer users/roles, the
    channel ticket activity gets logged to, and roles that are blocked from
    creating tickets. Reads from MongoDB when MONGODB_URI is configured,
    otherwise from the local access.json file."""
    db = get_db()
    if db is not None:
        doc = db.access_control.find_one({"_id": "singleton"})
        if not doc:
            return dict(_DEFAULTS)
        doc.setdefault("allowed_users", [])
        doc.setdefault("allowed_roles", [])
        doc.setdefault("log_channel_id", None)
        doc.setdefault("blacklist_log_channel_id", None)
        doc.setdefault("warning_log_channel_id", None)
        doc.setdefault("blacklist_roles", [])
        doc.setdefault("carry_manager_roles", [])
        doc.setdefault("ticket_viewer_roles", [])
        doc.setdefault("powerful_command_roles", [])
        doc.setdefault("powerful_command_users", [])
        doc.setdefault("basic_command_roles", [])
        doc.setdefault("basic_command_users", [])
        doc.setdefault("transcripts_roles", [])
        doc.setdefault("analytics_roles", [])
        doc.setdefault("remove_cooldown_roles", [])
        doc.setdefault("moderation_command_roles", [])
        doc.setdefault("moderation_command_users", [])
        doc.setdefault("globally_blocked_users", [])
        doc.setdefault("terms_unblock_tokens", [])
        return doc

    if not os.path.exists(ACCESS_FILE):
        return dict(_DEFAULTS)
    try:
        with open(ACCESS_FILE, "r") as f:
            data = json.load(f)
        data.setdefault("allowed_users", [])
        data.setdefault("allowed_roles", [])
        data.setdefault("log_channel_id", None)
        data.setdefault("blacklist_log_channel_id", None)
        data.setdefault("warning_log_channel_id", None)
        data.setdefault("blacklist_roles", [])
        data.setdefault("carry_manager_roles", [])
        data.setdefault("ticket_viewer_roles", [])
        data.setdefault("powerful_command_roles", [])
        data.setdefault("powerful_command_users", [])
        data.setdefault("basic_command_roles", [])
        data.setdefault("basic_command_users", [])
        data.setdefault("transcripts_roles", [])
        data.setdefault("analytics_roles", [])
        data.setdefault("remove_cooldown_roles", [])
        data.setdefault("moderation_command_roles", [])
        data.setdefault("moderation_command_users", [])
        data.setdefault("globally_blocked_users", [])
        data.setdefault("terms_unblock_tokens", [])
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


def get_globally_blocked_users() -> list[str]:
    return [str(user_id) for user_id in get_access_settings().get("globally_blocked_users", [])]


def is_globally_blocked(user_id) -> bool:
    return str(user_id) in set(get_globally_blocked_users()) and not is_admin(user_id)


def add_globally_blocked_user(user_id: str) -> bool:
    user_id = str(user_id).strip()
    if not user_id.isdigit():
        return False
    data = get_access_settings()
    blocked = data.setdefault("globally_blocked_users", [])
    if user_id in blocked:
        return False
    blocked.append(user_id)
    _save(data)
    return True


def remove_globally_blocked_user(user_id: str) -> bool:
    data = get_access_settings()
    blocked = data.setdefault("globally_blocked_users", [])
    if str(user_id) not in blocked:
        return False
    blocked.remove(str(user_id))
    _save(data)
    return True


def create_unblock_terms_token(user_id: str, expires_seconds: int = 86400) -> str:
    """Create a single-use token bound to one Discord user ID."""
    user_id = str(user_id).strip()
    if not user_id.isdigit():
        raise ValueError("User ID must contain only numbers.")
    token = secrets.token_urlsafe(32)
    data = get_access_settings()
    tokens = data.setdefault("terms_unblock_tokens", [])
    tokens.append({
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "user_id": user_id,
        "expires_at": int(time.time()) + expires_seconds,
        "used": False,
    })
    _save(data)
    return token


def get_terms_unblock_token(token: str) -> dict | None:
    token_hash = hashlib.sha256(str(token).encode()).hexdigest()
    for entry in get_access_settings().get("terms_unblock_tokens", []):
        if (
            entry.get("token_hash") == token_hash
            and not entry.get("used")
            and int(entry.get("expires_at", 0)) > int(time.time())
        ):
            return dict(entry)
    return None


def consume_terms_unblock_token(token: str, user_id: str) -> bool:
    token_hash = hashlib.sha256(str(token).encode()).hexdigest()
    data = get_access_settings()
    for entry in data.setdefault("terms_unblock_tokens", []):
        if (
            entry.get("token_hash") == token_hash
            and str(entry.get("user_id")) == str(user_id)
            and not entry.get("used")
            and int(entry.get("expires_at", 0)) > int(time.time())
        ):
            entry["used"] = True
            entry["used_at"] = int(time.time())
            _save(data)
            return True
    return False


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


def add_blacklist_role(role_id: str) -> bool:
    """Add a role to the Ticket Blacklist Role list. Any member holding one
    of these roles is blocked from creating new tickets."""
    data = get_access_settings()
    role_id = str(role_id)
    if role_id not in data["blacklist_roles"]:
        data["blacklist_roles"].append(role_id)
        _save(data)
        return True
    return False


def remove_blacklist_role(role_id: str) -> bool:
    data = get_access_settings()
    role_id = str(role_id)
    if role_id in data["blacklist_roles"]:
        data["blacklist_roles"].remove(role_id)
        _save(data)
        return True
    return False


def get_blacklist_role_ids():
    return get_access_settings().get("blacklist_roles", [])


def is_role_blacklisted(member_role_ids) -> bool:
    """Return True if any of the given role IDs is on the Ticket Blacklist
    Role list."""
    if not member_role_ids:
        return False
    blacklist_roles = {str(r) for r in get_blacklist_role_ids()}
    role_ids = {str(r) for r in member_role_ids}
    return bool(role_ids.intersection(blacklist_roles))


def add_ticket_viewer_role(role_id: str) -> bool:
    """Add a role that can view and chat in every ticket channel (with
    embed/attachment permissions), applied as an overwrite when tickets are
    created — separate from the single Support Role set on the panel."""
    data = get_access_settings()
    role_id = str(role_id)
    if role_id not in data["ticket_viewer_roles"]:
        data["ticket_viewer_roles"].append(role_id)
        _save(data)
        return True
    return False


def remove_ticket_viewer_role(role_id: str) -> bool:
    data = get_access_settings()
    role_id = str(role_id)
    if role_id in data["ticket_viewer_roles"]:
        data["ticket_viewer_roles"].remove(role_id)
        _save(data)
        return True
    return False


def get_ticket_viewer_role_ids():
    return get_access_settings().get("ticket_viewer_roles", [])


def add_carry_manager_role(role_id: str) -> bool:
    """Add a role allowed to view/use the Carry Manager Settings page."""
    data = get_access_settings()
    role_id = str(role_id)
    if role_id not in data["carry_manager_roles"]:
        data["carry_manager_roles"].append(role_id)
        _save(data)
        return True
    return False


def remove_carry_manager_role(role_id: str) -> bool:
    data = get_access_settings()
    role_id = str(role_id)
    if role_id in data["carry_manager_roles"]:
        data["carry_manager_roles"].remove(role_id)
        _save(data)
        return True
    return False


def get_carry_manager_role_ids():
    return get_access_settings().get("carry_manager_roles", [])


def has_carry_manager_access(user_id: str, member_role_ids=None) -> bool:
    """Return True if this user can view/use the Carry Manager Settings page.

    Admins always pass. Otherwise this is opt-in like the dashboard
    allow-list: while carry_manager_roles is empty, anyone with dashboard
    access can use it; once at least one role is added, only members with
    one of those roles (or admins) get in.
    """
    if is_admin(user_id):
        return True
    roles = get_carry_manager_role_ids()
    if not roles:
        return True
    if member_role_ids:
        role_ids = {str(r) for r in member_role_ids}
        if role_ids.intersection(set(roles)):
            return True
    return False


def add_transcripts_role(role_id: str) -> bool:
    """Add a role allowed to view the Transcripts page."""
    data = get_access_settings()
    role_id = str(role_id)
    if role_id not in data["transcripts_roles"]:
        data["transcripts_roles"].append(role_id)
        _save(data)
        return True
    return False


def remove_transcripts_role(role_id: str) -> bool:
    data = get_access_settings()
    role_id = str(role_id)
    if role_id in data["transcripts_roles"]:
        data["transcripts_roles"].remove(role_id)
        _save(data)
        return True
    return False


def get_transcripts_role_ids():
    return get_access_settings().get("transcripts_roles", [])


def has_transcripts_access(user_id: str, member_role_ids=None) -> bool:
    """Return True if this user can view the Transcripts page.

    Admins always pass. Opt-in like the other section permissions: while
    transcripts_roles is empty, anyone with dashboard access can view it;
    once at least one role is added, only members with one of those roles
    (or admins) get in.
    """
    if is_admin(user_id):
        return True
    roles = get_transcripts_role_ids()
    if not roles:
        return True
    if member_role_ids:
        role_ids = {str(r) for r in member_role_ids}
        if role_ids.intersection(set(roles)):
            return True
    return False


def add_analytics_role(role_id: str) -> bool:
    """Add a role allowed to view the Analytics page."""
    data = get_access_settings()
    role_id = str(role_id)
    if role_id not in data["analytics_roles"]:
        data["analytics_roles"].append(role_id)
        _save(data)
        return True
    return False


def remove_analytics_role(role_id: str) -> bool:
    data = get_access_settings()
    role_id = str(role_id)
    if role_id in data["analytics_roles"]:
        data["analytics_roles"].remove(role_id)
        _save(data)
        return True
    return False


def get_analytics_role_ids():
    return get_access_settings().get("analytics_roles", [])


def has_analytics_access(user_id: str, member_role_ids=None) -> bool:
    """Return True if this user can view the Analytics page.

    Admins always pass. Opt-in like the other section permissions: while
    analytics_roles is empty, anyone with dashboard access can view it;
    once at least one role is added, only members with one of those roles
    (or admins) get in.
    """
    if is_admin(user_id):
        return True
    roles = get_analytics_role_ids()
    if not roles:
        return True
    if member_role_ids:
        role_ids = {str(r) for r in member_role_ids}
        if role_ids.intersection(set(roles)):
            return True
    return False


def add_remove_cooldown_role(role_id: str) -> bool:
    """Add a role allowed to see/use the "Remove Cooldown" button on the
    Overview page."""
    data = get_access_settings()
    role_id = str(role_id)
    if role_id not in data["remove_cooldown_roles"]:
        data["remove_cooldown_roles"].append(role_id)
        _save(data)
        return True
    return False


def remove_remove_cooldown_role(role_id: str) -> bool:
    data = get_access_settings()
    role_id = str(role_id)
    if role_id in data["remove_cooldown_roles"]:
        data["remove_cooldown_roles"].remove(role_id)
        _save(data)
        return True
    return False


def get_remove_cooldown_role_ids():
    return get_access_settings().get("remove_cooldown_roles", [])


def has_remove_cooldown_access(user_id: str, member_role_ids=None) -> bool:
    """Return True if this user can see/use the "Remove Cooldown" button.

    Admins always pass. Opt-in like the other section permissions: while
    remove_cooldown_roles is empty, anyone with dashboard access can use it;
    once at least one role is added, only members with one of those roles
    (or admins) get in.
    """
    if is_admin(user_id):
        return True
    roles = get_remove_cooldown_role_ids()
    if not roles:
        return True
    if member_role_ids:
        role_ids = {str(r) for r in member_role_ids}
        if role_ids.intersection(set(roles)):
            return True
    return False


def add_powerful_command_role(role_id: str) -> bool:
    """Add a role allowed to use the /move and /ticketnumber commands."""
    data = get_access_settings()
    role_id = str(role_id)
    if role_id not in data["powerful_command_roles"]:
        data["powerful_command_roles"].append(role_id)
        _save(data)
        return True
    return False


def remove_powerful_command_role(role_id: str) -> bool:
    data = get_access_settings()
    role_id = str(role_id)
    if role_id in data["powerful_command_roles"]:
        data["powerful_command_roles"].remove(role_id)
        _save(data)
        return True
    return False


def add_powerful_command_user(user_id: str) -> bool:
    """Add a user ID allowed to use the /move and /ticketnumber commands."""
    data = get_access_settings()
    user_id = str(user_id)
    if user_id not in data["powerful_command_users"]:
        data["powerful_command_users"].append(user_id)
        _save(data)
        return True
    return False


def remove_powerful_command_user(user_id: str) -> bool:
    data = get_access_settings()
    user_id = str(user_id)
    if user_id in data["powerful_command_users"]:
        data["powerful_command_users"].remove(user_id)
        _save(data)
        return True
    return False


def get_powerful_command_role_ids():
    return get_access_settings().get("powerful_command_roles", [])


def get_powerful_command_user_ids():
    return get_access_settings().get("powerful_command_users", [])


def has_powerful_command_access(user_id: str, member_role_ids=None) -> bool:
    """Return True if this user can use /move and /ticketnumber.

    Admins always pass. While both lists are empty, anyone who already
    passes the normal Manage Channels check keeps working as before (so
    nobody gets locked out before this is configured). Once at least one
    role or user is added to either list, only matching users/roles/admins
    get through — on top of the existing Manage Channels + valid ticket
    channel requirement.
    """
    if is_admin(user_id):
        return True
    roles = get_powerful_command_role_ids()
    users = get_powerful_command_user_ids()
    if not roles and not users:
        return True
    if str(user_id) in users:
        return True
    if member_role_ids:
        role_ids = {str(r) for r in member_role_ids}
        if role_ids.intersection(set(roles)):
            return True
    return False


def add_basic_command_role(role_id: str) -> bool:
    """Add a role allowed to use the "basic" ticket-management commands
    (/close, /requestclose, /autoclose, /add,
    /remove, /rename) — same shape as Powerful Command Access, but for the
    non-dangerous commands."""
    data = get_access_settings()
    role_id = str(role_id)
    if role_id not in data["basic_command_roles"]:
        data["basic_command_roles"].append(role_id)
        _save(data)
        return True
    return False


def remove_basic_command_role(role_id: str) -> bool:
    data = get_access_settings()
    role_id = str(role_id)
    if role_id in data["basic_command_roles"]:
        data["basic_command_roles"].remove(role_id)
        _save(data)
        return True
    return False


def add_basic_command_user(user_id: str) -> bool:
    """Add a user ID allowed to use the "basic" ticket-management commands."""
    data = get_access_settings()
    user_id = str(user_id)
    if user_id not in data["basic_command_users"]:
        data["basic_command_users"].append(user_id)
        _save(data)
        return True
    return False


def remove_basic_command_user(user_id: str) -> bool:
    data = get_access_settings()
    user_id = str(user_id)
    if user_id in data["basic_command_users"]:
        data["basic_command_users"].remove(user_id)
        _save(data)
        return True
    return False


def get_basic_command_role_ids():
    return get_access_settings().get("basic_command_roles", [])


def get_basic_command_user_ids():
    return get_access_settings().get("basic_command_users", [])


def has_basic_command_access(user_id: str, member_role_ids=None) -> bool:
    """Return True if this user can use the basic ticket-management
    commands (/close, /requestclose, /autoclose,
    /add, /remove, /rename).

    Same shape as has_powerful_command_access: admins always pass; while
    both lists are empty, anyone who already passes Manage Channels keeps
    working as before; once at least one role or user is added, only
    matching users/roles/admins get through — on top of the existing
    Manage Channels + valid ticket channel requirement.
    """
    if is_admin(user_id):
        return True
    roles = get_basic_command_role_ids()
    users = get_basic_command_user_ids()
    if not roles and not users:
        return True
    if str(user_id) in users:
        return True
    if member_role_ids:
        role_ids = {str(r) for r in member_role_ids}
        if role_ids.intersection(set(roles)):
            return True
    return False


def set_log_channel(channel_id):
    data = get_access_settings()
    data["log_channel_id"] = str(channel_id) if channel_id else None
    _save(data)


def get_log_channel_id():
    return get_access_settings().get("log_channel_id")


def set_blacklist_log_channel(channel_id):
    """Separate log channel just for /addticketblacklist and
    /removeticketblacklist — independent of the general ticket activity
    log channel above."""
    data = get_access_settings()
    data["blacklist_log_channel_id"] = str(channel_id) if channel_id else None
    _save(data)


def get_blacklist_log_channel_id():
    return get_access_settings().get("blacklist_log_channel_id")


def set_warning_log_channel(channel_id):
    """Separate log channel just for /w1, /w2, /w3, and /unwarn —
    independent of the general ticket activity log and blacklist log
    channels above."""
    data = get_access_settings()
    data["warning_log_channel_id"] = str(channel_id) if channel_id else None
    _save(data)


def get_warning_log_channel_id():
    return get_access_settings().get("warning_log_channel_id")


def has_dashboard_access(user_id: str, member_role_ids=None) -> bool:
    """Return True if this Discord user is allowed to view the dashboard.

    Admins (see is_admin) always pass, regardless of the allow-list below —
    this exists so a bad Access Control entry can never fully lock every
    admin out.

    Otherwise, access is opt-in: until at least one user or role has been
    added to the allow-list, everyone who logs in with Discord can view the
    dashboard (this is the original behavior, kept as the default so nobody
    gets locked out before setting the allow-list up). Once at least one
    entry exists, only matching users/roles/admins are let in.
    """
    if is_admin(user_id):
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

# --- Moderation warning command access ---

def add_moderation_command_role(role_id: str) -> bool:
    """Allow a role to use /w1, /w2, /w3, and /unwarn."""
    data = get_access_settings()
    role_id = str(role_id)

    roles = data.setdefault("moderation_command_roles", [])
    if role_id in roles:
        return False

    roles.append(role_id)
    _save(data)
    return True


def remove_moderation_command_role(role_id: str) -> bool:
    """Remove a role from the warning-command allow-list."""
    data = get_access_settings()
    role_id = str(role_id)

    roles = data.setdefault("moderation_command_roles", [])
    if role_id not in roles:
        return False

    roles.remove(role_id)
    _save(data)
    return True


def add_moderation_command_user(user_id: str) -> bool:
    """Allow a user to use /w1, /w2, /w3, and /unwarn."""
    data = get_access_settings()
    user_id = str(user_id)

    users = data.setdefault("moderation_command_users", [])
    if user_id in users:
        return False

    users.append(user_id)
    _save(data)
    return True


def remove_moderation_command_user(user_id: str) -> bool:
    """Remove a user from the warning-command allow-list."""
    data = get_access_settings()
    user_id = str(user_id)

    users = data.setdefault("moderation_command_users", [])
    if user_id not in users:
        return False

    users.remove(user_id)
    _save(data)
    return True


def get_moderation_command_role_ids():
    return get_access_settings().get("moderation_command_roles", [])


def get_moderation_command_user_ids():
    return get_access_settings().get("moderation_command_users", [])


def has_moderation_command_access(user_id: str, member_role_ids=None) -> bool:
    """Check permission for /w1, /w2, /w3, and /unwarn.

    Admins always have access. Until you configure a moderation role or user,
    this stays open; once configured, only matching roles/users and admins
    can use the warning commands.
    """
    if is_admin(user_id):
        return True

    roles = get_moderation_command_role_ids()
    users = get_moderation_command_user_ids()

    if not roles and not users:
        return True

    if str(user_id) in {str(item) for item in users}:
        return True

    member_roles = {str(role_id) for role_id in (member_role_ids or [])}
    return bool(member_roles.intersection({str(role_id) for role_id in roles}))

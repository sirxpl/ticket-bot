import os
import json
import logging
import re
import time
import glob

from pymongo import ReturnDocument

from utils.db import get_db

# logger emits to stdout so platform (Render) captures ticket events
logger = logging.getLogger('ticket_storage')
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)s] %(name)s: %(message)s'))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)

DATA_DIR = "data"
TICKETS_FILE = os.path.join(DATA_DIR, "tickets.json")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
TRANSCRIPTS_DIR = os.path.join(DATA_DIR, "transcripts")
TICKET_LOGS_FILE = os.path.join(DATA_DIR, "ticket_logs.json")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)

# ensure logs file exists
if not os.path.exists(TICKET_LOGS_FILE):
    with open(TICKET_LOGS_FILE, "w") as f:
        json.dump([], f, indent=2)

# --- SYSTEM SETTINGS ---
def get_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {"tickets_enabled": True}
    try:
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"tickets_enabled": True}

def set_tickets_enabled(status: bool):
    settings = get_settings()
    settings["tickets_enabled"] = status
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)

def slugify(text: str) -> str:
    """Turn a label into a lowercase, hyphenated channel-name-safe prefix,
    e.g. 'Fallen Carry' -> 'fallen-carry'."""
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "ticket"


# --- CUSTOM TICKET CATEGORY DROPDOWN ---
_DEFAULT_TICKET_CATEGORIES = [
    {"label": "General Support", "description": "General help or questions", "emoji": "❓"},
    {"label": "Report a User", "description": "Report another user", "emoji": "⚠️"},
    {"label": "Appeal / Ban Review", "description": "Appeal moderation action", "emoji": "📝"},
]

def get_ticket_categories():
    settings = get_settings()
    categories = settings.get("ticket_categories")
    if not categories:
        categories = list(_DEFAULT_TICKET_CATEGORIES)
    # backward-compatible: older saved categories won't have these yet
    for c in categories:
        c.setdefault("blacklist_roles", [])
        c.setdefault("name_prefix", slugify(c.get("label", "ticket")))
        c.setdefault("open_note", "")
        c.setdefault("discord_category_id", None)
    return categories

def save_ticket_categories(categories: list):
    settings = get_settings()
    settings["ticket_categories"] = categories
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)


def get_ticket_panel_draft():
    settings = get_settings()
    return settings.get("ticket_panel_draft") or {}


def save_ticket_panel_draft(draft: dict):
    settings = get_settings()
    settings["ticket_panel_draft"] = draft
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)

# --- TICKETS DATA (counter, active tickets, cooldowns) ---
def _prune_expired_cooldowns(data: dict) -> bool:
    """Remove cooldown entries that have already expired. Returns True if
    anything was actually removed, so callers know whether to persist."""
    now = int(time.time())
    cooldowns = data.get("cooldowns", [])
    kept = [c for c in cooldowns if int(c.get("expires_ts", 0)) > now]
    if len(kept) != len(cooldowns):
        data["cooldowns"] = kept
        return True
    return False


def get_tickets_data():
    db = get_db()
    if db is not None:
        doc = db.tickets_data.find_one({"_id": "singleton"})
        if not doc:
            doc = {"_id": "singleton", "ticket_counter": 0, "active_tickets": [], "cooldowns": []}
            db.tickets_data.insert_one(dict(doc))
        doc.setdefault("ticket_counter", 0)
        doc.setdefault("active_tickets", [])
        doc.setdefault("cooldowns", [])
        if _prune_expired_cooldowns(doc):
            try:
                db.tickets_data.update_one(
                    {"_id": "singleton"}, {"$set": {"cooldowns": doc["cooldowns"]}}
                )
            except Exception:
                pass
        return doc

    if not os.path.exists(TICKETS_FILE):
        data = {"ticket_counter": 0, "active_tickets": [], "cooldowns": []}
        with open(TICKETS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return data
    try:
        with open(TICKETS_FILE, "r") as f:
            data = json.load(f)
        # ensure keys
        data.setdefault("ticket_counter", 0)
        data.setdefault("active_tickets", [])
        data.setdefault("cooldowns", [])
        if _prune_expired_cooldowns(data):
            try:
                with open(TICKETS_FILE, "w") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                pass
        return data
    except Exception:
        return {"ticket_counter": 0, "active_tickets": [], "cooldowns": []}


def _save_tickets_data(data: dict):
    db = get_db()
    if db is not None:
        try:
            data = dict(data)
            data["_id"] = "singleton"
            db.tickets_data.replace_one({"_id": "singleton"}, data, upsert=True)
            return True
        except Exception:
            return False
    try:
        with open(TICKETS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False


def increment_ticket_counter():
    """Atomically increment and return the new lifetime ticket counter."""
    db = get_db()
    if db is not None:
        try:
            doc = db.tickets_data.find_one_and_update(
                {"_id": "singleton"},
                {"$inc": {"ticket_counter": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            return doc.get("ticket_counter", 1)
        except Exception:
            logger.exception("Failed to increment ticket counter in MongoDB")
    data = get_tickets_data()
    data["ticket_counter"] = data.get("ticket_counter", 0) + 1
    _save_tickets_data(data)
    return data["ticket_counter"]


# --- PER-CATEGORY TICKET NUMBERING (e.g. fallen-carry-0001, fallen-carry-0002...) ---
def get_category_counter(prefix: str) -> int:
    settings = get_settings()
    return int(settings.get("category_counters", {}).get(prefix, 0))


def set_category_counter(prefix: str, value: int):
    """Set (resume) a category's counter to a given value. The NEXT ticket
    created/moved into this category will be value+1."""
    settings = get_settings()
    counters = settings.get("category_counters", {})
    counters[prefix] = int(value)
    settings["category_counters"] = counters
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)


def increment_category_counter(prefix: str) -> int:
    settings = get_settings()
    counters = settings.get("category_counters", {})
    counters[prefix] = int(counters.get(prefix, 0)) + 1
    settings["category_counters"] = counters
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)
    return counters[prefix]


# --- ACTIVE TICKET LOOKUP/UPDATE (used by ticket slash commands) ---
def is_ticket_channel(channel_id) -> bool:
    data = get_tickets_data()
    return any(str(t.get("channel_id")) == str(channel_id) for t in data.get("active_tickets", []))


def get_active_ticket_for_user(user_id):
    """Return this user's currently open ticket entry (any category), or
    None if they don't have one — used to block opening a second ticket
    while one is still active."""
    data = get_tickets_data()
    return next(
        (t for t in data.get("active_tickets", []) if str(t.get("user_id")) == str(user_id)),
        None,
    )


def get_active_ticket(channel_id):
    data = get_tickets_data()
    return next(
        (t for t in data.get("active_tickets", []) if str(t.get("channel_id")) == str(channel_id)),
        None,
    )


def update_active_ticket(channel_id, **kwargs):
    data = get_tickets_data()
    for t in data.get("active_tickets", []):
        if str(t.get("channel_id")) == str(channel_id):
            t.update(kwargs)
    _save_tickets_data(data)


def add_active_ticket(ticket_id: str, channel_id: str, user_id: str, ticket_number: int = None):
    data = get_tickets_data()
    # ensure no duplicate
    exists = any(t.get('ticket_id') == str(ticket_id) for t in data.get('active_tickets', []))
    if not exists:
        now = __import__('time').time()
        entry = {
            'ticket_id': str(ticket_id),
            'channel_id': str(channel_id),
            'user_id': str(user_id),
            'created_at': now,
            # --- autoclose tracking ---
            'last_activity': now,       # updated whenever the opener sends a message
            'autoclose_disabled': False,
            'reminder_sent': False,
        }
        if ticket_number is not None:
            entry['ticket_number'] = int(ticket_number)
        data['active_tickets'].append(entry)
    data['ticket_counter'] = int(data.get('ticket_counter', 0))
    _save_tickets_data(data)


def touch_ticket_activity(channel_id):
    """Call whenever the ticket opener sends a message — resets the autoclose
    clock and clears the 12h reminder flag so it can fire again next time."""
    update_active_ticket(channel_id, last_activity=__import__('time').time(), reminder_sent=False)


def set_autoclose_disabled(channel_id, disabled: bool = True):
    update_active_ticket(channel_id, autoclose_disabled=bool(disabled))


def remove_active_ticket(ticket_id: str):
    data = get_tickets_data()
    data['active_tickets'] = [t for t in data.get('active_tickets', []) if str(t.get('ticket_id')) != str(ticket_id)]
    _save_tickets_data(data)


def add_cooldown(user_id: str, hours: int = 8):
    data = get_tickets_data()
    now = int(__import__('time').time())
    expires = now + int(hours) * 3600
    # remove existing for user
    data['cooldowns'] = [c for c in data.get('cooldowns', []) if str(c.get('user_id')) != str(user_id)]
    data['cooldowns'].append({'user_id': str(user_id), 'expires_at': __import__('datetime').datetime.utcfromtimestamp(expires).isoformat() + 'Z', 'expires_ts': expires})
    _save_tickets_data(data)


def remove_cooldown(user_id: str):
    data = get_tickets_data()
    before = len(data.get('cooldowns', []))
    data['cooldowns'] = [c for c in data.get('cooldowns', []) if str(c.get('user_id')) != str(user_id)]
    _save_tickets_data(data)
    return len(data.get('cooldowns', [])) < before


def get_cooldowns():
    data = get_tickets_data()
    return data.get('cooldowns', [])


def is_on_cooldown(user_id: str):
    try:
        now = int(__import__('time').time())
        for c in get_cooldowns():
            if str(c.get('user_id')) == str(user_id):
                if int(c.get('expires_ts', 0)) > now:
                    return True, c
        return False, None
    except Exception:
        return False, None


def add_to_blacklist(user_id: str):
    data = get_blacklist_data()
    if user_id not in data.get('blacklisted_users', []):
        data['blacklisted_users'].append(str(user_id))
        with open(BLACKLIST_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    return False


# --- TICKET LOGS ---
def append_ticket_log(entry: dict):
    """Append a log entry dict. Entry should include at least:
    {"ticket_id": str, "action": str, "timestamp": iso, "payload": {...}}
    Writes to MongoDB when configured, otherwise to ticket_logs.json.
    Also logs to stdout so platform logs capture the event for debugging.
    """
    db = get_db()
    if db is not None:
        try:
            db.ticket_logs.insert_one(dict(entry))
            try:
                logger.info(f"ticket_log appended: action={entry.get('action')} ticket_id={entry.get('ticket_id')} payload_keys={list(entry.keys())}")
            except Exception:
                pass
            return True
        except Exception as e:
            logger.exception(f"Failed to append ticket_log to MongoDB: {e}")
            return False

    try:
        logs = []
        if os.path.exists(TICKET_LOGS_FILE):
            with open(TICKET_LOGS_FILE, "r") as f:
                logs = json.load(f)
        logs.append(entry)
        with open(TICKET_LOGS_FILE, "w") as f:
            json.dump(logs, f, indent=2)
        try:
            logger.info(f"ticket_log appended: action={entry.get('action')} ticket_id={entry.get('ticket_id')} payload_keys={list(entry.keys())}")
        except Exception:
            pass
        return True
    except Exception as e:
        try:
            logger.exception(f"Failed to append ticket_log: {e}")
        except Exception:
            pass
        return False


def get_ticket_logs():
    db = get_db()
    if db is not None:
        try:
            return list(db.ticket_logs.find({}, {"_id": 0}))
        except Exception:
            logger.exception("Failed to read ticket_logs from MongoDB")
            return []
    try:
        with open(TICKET_LOGS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def get_logs_for_ticket(ticket_id: str):
    logs = get_ticket_logs()
    return [l for l in logs if str(l.get("ticket_id")) == str(ticket_id)]

def get_transcript_info(filename: str):
    """Given a transcript filename like 'ticket-123456789.html', return the
    ticket's creator username and ticket number for a cleaner dashboard display."""
    ticket_id = filename
    if ticket_id.startswith("ticket-"):
        ticket_id = ticket_id[len("ticket-"):]
    if ticket_id.endswith(".html"):
        ticket_id = ticket_id[: -len(".html")]

    username = "Unknown User"
    ticket_number = None

    logs = get_logs_for_ticket(ticket_id)
    created = next((l for l in logs if l.get("action") == "created"), None)
    if created:
        creator = created.get("creator") or {}
        username = creator.get("name") or username
        ticket_number = created.get("ticket_number")

    return {
        "filename": filename,
        "username": username,
        "ticket_number": ticket_number,
    }


# --- TRANSCRIPT HTML STORAGE ---
def save_transcript_html(filename: str, html: str):
    """Save a rendered transcript's HTML. Stored in MongoDB when configured,
    otherwise written to the local transcripts folder."""
    db = get_db()
    if db is not None:
        try:
            db.transcripts.replace_one(
                {"_id": filename},
                {"_id": filename, "html": html, "saved_at": time.time()},
                upsert=True,
            )
            return True
        except Exception:
            logger.exception(f"Failed to save transcript '{filename}' to MongoDB")
            return False

    try:
        os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
        with open(os.path.join(TRANSCRIPTS_DIR, filename), "w", encoding="utf-8") as f:
            f.write(html)
        return True
    except Exception:
        logger.exception(f"Failed to save transcript '{filename}' to disk")
        return False


def get_transcript_html(filename: str):
    """Return a transcript's HTML content, or None if it doesn't exist."""
    db = get_db()
    if db is not None:
        try:
            doc = db.transcripts.find_one({"_id": filename})
            return doc["html"] if doc else None
        except Exception:
            logger.exception(f"Failed to read transcript '{filename}' from MongoDB")
            return None

    path = os.path.join(TRANSCRIPTS_DIR, filename)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def list_transcript_filenames():
    """Return the filenames of every saved transcript."""
    db = get_db()
    if db is not None:
        try:
            return [doc["_id"] for doc in db.transcripts.find({}, {"_id": 1})]
        except Exception:
            logger.exception("Failed to list transcripts from MongoDB")
            return []

    if not os.path.exists(TRANSCRIPTS_DIR):
        return []
    return [os.path.basename(f) for f in glob.glob(f"{TRANSCRIPTS_DIR}/*.html")]

# --- BLACKLIST DATA ---
def get_blacklist_data():
    if not os.path.exists(BLACKLIST_FILE):
        return {"blacklisted_users": []}
    try:
        with open(BLACKLIST_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"blacklisted_users": []}

def remove_from_blacklist(user_id: str):
    data = get_blacklist_data()
    if user_id in data["blacklisted_users"]:
        data["blacklisted_users"].remove(user_id)
        with open(BLACKLIST_FILE, "w") as f:
            json.dump(data, f, indent=4)
        return True
    return False

# --- Signed short-lived transcript URLs ---
import hmac
import hashlib
import base64
import time


def _get_secret_key():
    # prefer environment SECRET_KEY, fall back to a static default (not recommended for production)
    return os.getenv('SECRET_KEY') or 'supersecretkey123'


def generate_transcript_token(filename: str, expires_seconds: int = 3600) -> str:
    """Return a URL-safe token encoding filename and expiry signed with SECRET_KEY."""
    expiry = int(time.time()) + int(expires_seconds)
    payload = f"{filename}|{expiry}".encode('utf-8')
    key = _get_secret_key().encode('utf-8')
    sig = hmac.new(key, payload, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(payload + b"|" + sig).decode('utf-8')
    return token


def verify_transcript_token(token: str) -> dict:
    """Verify token and return dict {filename, expires} if valid, else None."""
    try:
        raw = base64.urlsafe_b64decode(token.encode('utf-8'))
        parts = raw.split(b"|")
        if len(parts) < 3:
            return None
        filename = parts[0].decode('utf-8')
        expires = int(parts[1].decode('utf-8'))
        sig = b"|".join(parts[2:])
        payload = f"{filename}|{expires}".encode('utf-8')
        key = _get_secret_key().encode('utf-8')
        expected = hmac.new(key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, sig):
            return None
        if int(time.time()) > expires:
            return None
        return {"filename": filename, "expires": expires}
    except Exception:
        return None


def generate_transcript_url(filename: str, expires_seconds: int = 3600) -> str:
    """Build a full absolute HTTPS URL to the transcripts route with a short-lived token."""
    token = generate_transcript_token(filename, expires_seconds=expires_seconds)
    base_url = os.getenv('DASHBOARD_URL') or os.getenv('OAUTH2_REDIRECT_URI') or 'https://ticket-bot-f184.onrender.com'
    if base_url.endswith('/callback'):
        base_url = base_url.rsplit('/callback', 1)[0]
    if not base_url.startswith('http'):
        base_url = 'https://' + base_url
    if base_url.startswith('http://'):
        base_url = 'https://' + base_url[len('http://'):]
    return f"{base_url.rstrip('/')}/transcripts/{filename}?token={token}"

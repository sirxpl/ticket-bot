import os
import json

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

# --- TICKETS DATA ---
def get_tickets_data():
    if not os.path.exists(TICKETS_FILE):
        return {"ticket_counter": 0, "active_tickets": [], "cooldowns": []}
    try:
        with open(TICKETS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"ticket_counter": 0, "active_tickets": [], "cooldowns": []}


# --- TICKET LOGS ---
def append_ticket_log(entry: dict):
    """Append a log entry dict to ticket_logs.json. Entry should include at least:
    {"ticket_id": str, "action": str, "timestamp": iso, "payload": {...}}
    """
    try:
        logs = []
        if os.path.exists(TICKET_LOGS_FILE):
            with open(TICKET_LOGS_FILE, "r") as f:
                logs = json.load(f)
        logs.append(entry)
        with open(TICKET_LOGS_FILE, "w") as f:
            json.dump(logs, f, indent=2)
        return True
    except Exception:
        return False


def get_ticket_logs():
    try:
        with open(TICKET_LOGS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def get_logs_for_ticket(ticket_id: str):
    logs = get_ticket_logs()
    return [l for l in logs if str(l.get("ticket_id")) == str(ticket_id)]

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

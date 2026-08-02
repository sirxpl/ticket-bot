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

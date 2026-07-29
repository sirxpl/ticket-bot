import os
import json

DATA_DIR = "data"
TICKETS_FILE = os.path.join(DATA_DIR, "tickets.json")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
TRANSCRIPTS_DIR = os.path.join(DATA_DIR, "transcripts")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)

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

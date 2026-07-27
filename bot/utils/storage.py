# utils/storage.py
import os
import json
import chat_exporter

STORAGE_DIR = "/var/data" if os.path.exists("/var/data") else "."
TICKETS_FILE = os.path.join(STORAGE_DIR, "tickets.json")
BLACKLIST_FILE = os.path.join(STORAGE_DIR, "blacklist.json")
TRANSCRIPTS_DIR = os.path.join(STORAGE_DIR, "transcripts")

os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)

# --- GENERIC JSON HELPERS ---
def load_json(filepath, default_data):
    if not os.path.exists(filepath):
        save_json(filepath, default_data)
        return default_data
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return default_data

def save_json(filepath, data):
    try:
        with open(filepath, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error saving {filepath}: {e}")

# --- TICKET COUNTER FUNCTIONS ---
def get_tickets_data():
    return load_json(TICKETS_FILE, {"ticket_counter": 0})

def save_tickets_data(data):
    save_json(TICKETS_FILE, data)

# --- BLACKLIST FUNCTIONS ---
def get_blacklist_data():
    return load_json(BLACKLIST_FILE, {"blacklisted_users": []})

def save_blacklist_data(data):
    save_json(BLACKLIST_FILE, data)

def is_user_blacklisted(user_id: int) -> bool:
    data = get_blacklist_data()
    return str(user_id) in data.get("blacklisted_users", [])

def add_to_blacklist(user_id: int):
    data = get_blacklist_data()
    str_id = str(user_id)
    if str_id not in data["blacklisted_users"]:
        data["blacklisted_users"].append(str_id)
        save_blacklist_data(data)

def remove_from_blacklist(user_id: int):
    data = get_blacklist_data()
    str_id = str(user_id)
    if str_id in data["blacklisted_users"]:
        data["blacklisted_users"].remove(str_id)
        save_blacklist_data(data)

# --- TRANSCRIPT GENERATOR ---
async def create_html_transcript(channel):
    try:
        transcript = await chat_exporter.export(channel)
        if transcript:
            file_path = os.path.join(TRANSCRIPTS_DIR, f"{channel.name}.html")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(transcript)
            return file_path
    except Exception as e:
        print(f"Transcript Export Error: {e}")
        return None

# utils/storage.py
import os
import chat_exporter
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI")

if MONGO_URI:
    # Pass tlsAllowInvalidCertificates=True to prevent SSL Handshake errors on Render
    client = MongoClient(MONGO_URI, tlsAllowInvalidCertificates=True)
    db = client["discord_bot"]
    tickets_col = db["tickets"]
    blacklist_col = db["blacklist"]
else:
    print("⚠️ WARNING: 'MONGO_URI' not set!")
    client = None

TRANSCRIPTS_DIR = "/tmp/transcripts"
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)

def get_tickets_data():
    if not client: return {"ticket_counter": 0}
    data = tickets_col.find_one({"_id": "config"})
    return data if data else {"ticket_counter": 0}

def save_tickets_data(data):
    if client:
        tickets_col.update_one({"_id": "config"}, {"$set": {"ticket_counter": data.get("ticket_counter", 0)}}, upsert=True)

def get_blacklist_data():
    if not client: return {"blacklisted_users": []}
    data = blacklist_col.find_one({"_id": "config"})
    return data if data else {"blacklisted_users": []}

def save_blacklist_data(data):
    if client:
        blacklist_col.update_one({"_id": "config"}, {"$set": {"blacklisted_users": data.get("blacklisted_users", [])}}, upsert=True)

def is_user_blacklisted(user_id: int) -> bool:
    data = get_blacklist_data()
    return str(user_id) in data.get("blacklisted_users", [])

def add_to_blacklist(user_id: int):
    data = get_blacklist_data()
    users = data.get("blacklisted_users", [])
    if str(user_id) not in users:
        users.append(str(user_id))
        save_blacklist_data({"blacklisted_users": users})

def remove_from_blacklist(user_id: int):
    data = get_blacklist_data()
    users = data.get("blacklisted_users", [])
    if str(user_id) in users:
        users.remove(str(user_id))
        save_blacklist_data({"blacklisted_users": users})

async def create_html_transcript(channel):
    try:
        transcript = await chat_exporter.export(channel)
        if transcript:
            file_path = os.path.join(TRANSCRIPTS_DIR, f"{channel.name}.html")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(transcript)
            return file_path
    except Exception as e:
        print(f"Transcript Error: {e}")
        return None

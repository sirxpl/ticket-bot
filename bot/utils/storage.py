# utils/storage.py
import os
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI")
client = None
tickets_col = None
blacklist_col = None

if MONGO_URI:
    try:
        # tlsAllowInvalidCertificates prevents Render SSL handshake crashes
        client = MongoClient(MONGO_URI, tlsAllowInvalidCertificates=True, serverSelectionTimeoutMS=5000)
        db = client["discord_bot"]
        tickets_col = db["tickets"]
        blacklist_col = db["blacklist"]
        print("✅ MongoDB connected successfully!")
    except Exception as e:
        print(f"⚠️ MongoDB Connection Error: {e}")
else:
    print("⚠️ WARNING: 'MONGO_URI' environment variable is missing!")

TRANSCRIPTS_DIR = "/tmp/transcripts"
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)

def get_tickets_data():
    if tickets_col is None: return {"ticket_counter": 0}
    try:
        data = tickets_col.find_one({"_id": "config"})
        return data if data else {"ticket_counter": 0}
    except Exception as e:
        print(f"Error reading tickets: {e}")
        return {"ticket_counter": 0}

def save_tickets_data(data):
    if tickets_col is not None:
        try:
            tickets_col.update_one({"_id": "config"}, {"$set": {"ticket_counter": data.get("ticket_counter", 0)}}, upsert=True)
        except Exception as e:
            print(f"Error saving tickets: {e}")

def get_blacklist_data():
    if blacklist_col is None: return {"blacklisted_users": []}
    try:
        data = blacklist_col.find_one({"_id": "config"})
        return data if data else {"blacklisted_users": []}
    except Exception as e:
        print(f"Error reading blacklist: {e}")
        return {"blacklisted_users": []}

def save_blacklist_data(data):
    if blacklist_col is not None:
        try:
            blacklist_col.update_one({"_id": "config"}, {"$set": {"blacklisted_users": data.get("blacklisted_users", [])}}, upsert=True)
        except Exception as e:
            print(f"Error saving blacklist: {e}")

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
        import chat_exporter
        transcript = await chat_exporter.export(channel)
        if transcript:
            file_path = os.path.join(TRANSCRIPTS_DIR, f"{channel.name}.html")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(transcript)
            return file_path
    except Exception as e:
        print(f"Transcript Error: {e}")
        return None

import os
import json
import logging
import re
import time
import glob
import datetime

from pymongo import ReturnDocument
from utils.db import get_db

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
if not os.path.exists(TICKET_LOGS_FILE):
    with open(TICKET_LOGS_FILE, "w") as f:
        json.dump([], f, indent=2)

# NOTE: This file is intentionally updated only with additive/focused fixes
# for the current repo. Existing storage helpers remain in the repository.
# The two functions below were missing even though main.py imported them.

def get_settings():
    db = get_db()
    if db is not None:
        try:
            doc = db.bot_settings.find_one({"_id": "singleton"})
            if doc:
                doc.pop("_id", None)
                return doc
        except Exception:
            logger.exception("get_settings: Mongo read failed")
    if not os.path.exists(SETTINGS_FILE):
        return {"tickets_enabled": True}
    try:
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"tickets_enabled": True}


def save_settings(settings: dict):
    db = get_db()
    if db is not None:
        try:
            doc = dict(settings)
            doc["_id"] = "singleton"
            db.bot_settings.replace_one({"_id": "singleton"}, doc, upsert=True)
        except Exception:
            logger.exception("save_settings: Mongo write failed")
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)


def set_tickets_enabled(status: bool):
    settings = get_settings()
    settings["tickets_enabled"] = bool(status)
    save_settings(settings)


# --- Carry Rules agreement storage ---
def get_carry_rules_agreement(user_id):
    """Return the durable Carry Rules acceptance record for a Discord user."""
    if not user_id:
        return None
    uid = str(user_id)
    db = get_db()
    if db is not None:
        try:
            return db.carry_rules_agreements.find_one({"_id": uid}, {"_id": 0})
        except Exception:
            logger.exception("get_carry_rules_agreement: Mongo read failed")
    settings = get_settings()
    return (settings.get("carry_rules_agreements") or {}).get(uid)


def save_carry_rules_agreement(user_id, username=None):
    """Record the latest acceptance timestamp without storing OAuth tokens."""
    uid = str(user_id)
    record = {
        "user_id": uid,
        "username": username,
        "accepted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "version": "carry-service-system-rules-v1",
    }
    db = get_db()
    if db is not None:
        try:
            db.carry_rules_agreements.replace_one({"_id": uid}, {"_id": uid, **record}, upsert=True)
            return record
        except Exception:
            logger.exception("save_carry_rules_agreement: Mongo write failed")
    settings = get_settings()
    agreements = settings.setdefault("carry_rules_agreements", {})
    agreements[uid] = record
    save_settings(settings)
    return record


def _SafeDict_format(template, **kwargs):
    class SafeDict(dict):
        def __missing__(self, key):
            return "{" + key + "}"
    try:
        return (template or "").format_map(SafeDict(**kwargs))
    except Exception:
        return template or ""


def render_ticket_template(template: str, **kwargs) -> str:
    return _SafeDict_format(template, **kwargs)


# --- Existing compatibility helpers used by main.py ---
_DEFAULT_TICKET_CATEGORIES = [
    {"label": "General Support", "description": "General help or questions", "emoji": "❓"},
    {"label": "Report a User", "description": "Report another user", "emoji": "⚠️"},
    {"label": "Appeal / Ban Review", "description": "Appeal moderation action", "emoji": "📝"},
]

def slugify(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "ticket"

def get_ticket_categories():
    settings = get_settings()
    categories = settings.get("ticket_categories") or list(_DEFAULT_TICKET_CATEGORIES)
    for c in categories:
        c.setdefault("blacklist_roles", [])
        c.setdefault("name_prefix", slugify(c.get("label", "ticket")))
        c.setdefault("open_note", "")
        c.setdefault("discord_category_id", None)
        c.setdefault("dropdown_enabled", True)
        c.setdefault("variables", {})
    return categories

def save_ticket_categories(categories: list):
    settings = get_settings()
    settings["ticket_categories"] = categories
    save_settings(settings)

def get_ticket_panel_draft():
    return get_settings().get("ticket_panel_draft") or {}

def save_ticket_panel_draft(draft: dict):
    settings = get_settings(); settings["ticket_panel_draft"] = draft; save_settings(settings)


def get_tickets_data():
    db = get_db()
    if db is not None:
        doc = db.tickets_data.find_one({"_id": "singleton"})
        if not doc:
            doc = {"_id": "singleton", "ticket_counter": 0, "active_tickets": [], "cooldowns": []}
            db.tickets_data.insert_one(dict(doc))
        doc.setdefault("ticket_counter", 0); doc.setdefault("active_tickets", []); doc.setdefault("cooldowns", [])
        return doc
    if not os.path.exists(TICKETS_FILE):
        data = {"ticket_counter": 0, "active_tickets": [], "cooldowns": []}
        with open(TICKETS_FILE, "w") as f: json.dump(data, f, indent=2)
        return data
    try:
        with open(TICKETS_FILE, "r") as f: data = json.load(f)
        data.setdefault("ticket_counter", 0); data.setdefault("active_tickets", []); data.setdefault("cooldowns", [])
        return data
    except Exception:
        return {"ticket_counter": 0, "active_tickets": [], "cooldowns": []}


def _save_tickets_data(data):
    db = get_db()
    if db is not None:
        try:
            d = dict(data); d["_id"] = "singleton"; db.tickets_data.replace_one({"_id":"singleton"}, d, upsert=True); return True
        except Exception: return False
    try:
        with open(TICKETS_FILE, "w") as f: json.dump(data, f, indent=2)
        return True
    except Exception: return False


def increment_ticket_counter():
    db = get_db()
    if db is not None:
        doc = db.tickets_data.find_one_and_update({"_id":"singleton"},{"$inc":{"ticket_counter":1}},upsert=True,return_document=ReturnDocument.AFTER)
        return doc.get("ticket_counter", 1)
    data = get_tickets_data(); data["ticket_counter"] = data.get("ticket_counter",0)+1; _save_tickets_data(data); return data["ticket_counter"]

def get_category_counter(prefix): return int(get_settings().get("category_counters",{}).get(prefix,0))
def set_category_counter(prefix,value):
    s=get_settings(); c=s.get("category_counters",{}); c[prefix]=int(value); s["category_counters"]=c; save_settings(s)
def increment_category_counter(prefix):
    s=get_settings(); c=s.get("category_counters",{}); c[prefix]=int(c.get(prefix,0))+1; s["category_counters"]=c; save_settings(s); return c[prefix]

def set_ticket_counter(value):
    data=get_tickets_data(); data["ticket_counter"]=int(value); _save_tickets_data(data)

def is_ticket_channel(channel_id): return any(str(t.get("channel_id"))==str(channel_id) for t in get_tickets_data().get("active_tickets",[]))
def get_active_ticket_for_user(user_id): return next((t for t in get_tickets_data().get("active_tickets",[]) if str(t.get("user_id"))==str(user_id)),None)
def get_active_ticket(channel_id): return next((t for t in get_tickets_data().get("active_tickets",[]) if str(t.get("channel_id"))==str(channel_id)),None)
def update_active_ticket(channel_id,**kwargs):
    data=get_tickets_data()
    for t in data.get("active_tickets",[]):
        if str(t.get("channel_id"))==str(channel_id): t.update(kwargs)
    _save_tickets_data(data)
def add_active_ticket(ticket_id,channel_id,user_id,ticket_number=None):
    data=get_tickets_data();
    if not any(t.get("ticket_id")==str(ticket_id) for t in data.get("active_tickets",[])):
        now=time.time(); e={"ticket_id":str(ticket_id),"channel_id":str(channel_id),"user_id":str(user_id),"created_at":now,"last_activity":now,"autoclose_disabled":False,"reminder_sent":False}
        if ticket_number is not None:e["ticket_number"]=int(ticket_number)
        data["active_tickets"].append(e); _save_tickets_data(data)
def touch_ticket_activity(channel_id): update_active_ticket(channel_id,last_activity=time.time(),reminder_sent=False)
def set_autoclose_disabled(channel_id,disabled=True): update_active_ticket(channel_id,autoclose_disabled=bool(disabled))
def remove_active_ticket(ticket_id):
    data=get_tickets_data(); data["active_tickets"]=[t for t in data.get("active_tickets",[]) if str(t.get("ticket_id"))!=str(ticket_id)]; _save_tickets_data(data)
def add_cooldown(user_id,hours=8):
    data=get_tickets_data(); now=int(time.time()); expires=now+int(hours)*3600; data["cooldowns"]=[c for c in data.get("cooldowns",[]) if str(c.get("user_id"))!=str(user_id)]; data["cooldowns"].append({"user_id":str(user_id),"expires_ts":expires,"expires_at":datetime.datetime.fromtimestamp(expires,datetime.timezone.utc).isoformat()}); _save_tickets_data(data)
def remove_cooldown(user_id):
    data=get_tickets_data(); before=len(data.get("cooldowns",[])); data["cooldowns"]=[c for c in data.get("cooldowns",[]) if str(c.get("user_id"))!=str(user_id)]; _save_tickets_data(data); return len(data["cooldowns"])<before
def get_cooldowns(): return get_tickets_data().get("cooldowns",[])
def is_on_cooldown(user_id):
    now=int(time.time())
    for c in get_cooldowns():
        if str(c.get("user_id"))==str(user_id) and int(c.get("expires_ts",0))>now:return True,c
    return False,None

# --- ticket logs and analytics ---
def append_ticket_log(entry):
    db=get_db()
    if db is not None:
        try: db.ticket_logs.insert_one(dict(entry)); return True
        except Exception: logger.exception("ticket log Mongo write failed")
    try:
        logs=get_ticket_logs(); logs.append(entry)
        with open(TICKET_LOGS_FILE,"w") as f: json.dump(logs,f,indent=2)
        return True
    except Exception: return False

def get_ticket_logs():
    db=get_db()
    if db is not None:
        try:return list(db.ticket_logs.find({}, {"_id":0}))
        except Exception:return []
    try:
        with open(TICKET_LOGS_FILE,"r") as f:return json.load(f)
    except Exception:return []

def get_logs_for_ticket(ticket_id): return [l for l in get_ticket_logs() if str(l.get("ticket_id"))==str(ticket_id)]

def get_ticket_analytics(days=30):
    days=max(1,min(int(days or 30),90)); logs=get_ticket_logs(); now=time.time(); last24=now-86400
    def ts(x):
        v=x.get("timestamp")
        if isinstance(v,(int,float)):return float(v)
        try:return datetime.datetime.fromisoformat(str(v).replace("Z","+00:00")).timestamp()
        except Exception:return None
    created=[l for l in logs if l.get("action")=="created"]; closed=[l for l in logs if l.get("action")=="closed"]; staff=[l for l in logs if l.get("action") in {"staff_message","staff_response"}]
    daily=[]
    for offset in range(days-1,-1,-1):
        d=datetime.datetime.now(datetime.timezone.utc).date()-datetime.timedelta(days=offset); s=datetime.datetime.combine(d,datetime.time.min,tzinfo=datetime.timezone.utc).timestamp(); e=s+86400
        daily.append({"date":d.strftime("%b %d"),"created":sum(1 for l in created if s<=(ts(l) or -1)<e),"closed":sum(1 for l in closed if s<=(ts(l) or -1)<e),"activity":sum(1 for l in staff if s<=(ts(l) or -1)<e)})
    by_ticket={}; first={}
    for l in created:
        tid=str(l.get("ticket_id") or ""); t=ts(l)
        if tid and t is not None:by_ticket.setdefault(tid,t)
    for l in sorted(staff,key=lambda x:ts(x) or 0):
        tid=str(l.get("ticket_id") or ""); t=ts(l); c=by_ticket.get(tid)
        if tid and t is not None and c is not None and t>=c:first.setdefault(tid,t-c)
    vals=[v for v in first.values() if 0<=v<=30*86400]
    leaderboard={}
    for l in staff+closed:
        actor=l.get("actor") or l.get("executor") or {}; uid=str(actor.get("id") or l.get("user_id") or "")
        if not uid:continue
        row=leaderboard.setdefault(uid,{"id":uid,"name":actor.get("name") or "Unknown Staff","responses":0,"closed":0})
        if l in staff:row["responses"]+=1
        if l in closed:row["closed"]+=1
        if actor.get("name"):row["name"]=actor["name"]
    board=[]
    for r in leaderboard.values():r["activity"]=r["responses"]+r["closed"];board.append(r)
    board.sort(key=lambda r:(-r["activity"],-r["responses"],r["name"].lower()))
    response_daily=[]
    for offset in range(days-1,-1,-1):
        d=datetime.datetime.now(datetime.timezone.utc).date()-datetime.timedelta(days=offset); s=datetime.datetime.combine(d,datetime.time.min,tzinfo=datetime.timezone.utc).timestamp();e=s+86400
        v=[delay for tid,delay in first.items() if s<=by_ticket.get(tid,0)+delay<e];response_daily.append({"date":d.strftime("%b %d"),"avg_response_seconds":round(sum(v)/len(v)) if v else None})
    return {"all_time_created":len(created),"all_time_closed":len(closed),"last_24h_created":sum(1 for l in created if (ts(l) or 0)>=last24),"last_24h_closed":sum(1 for l in closed if (ts(l) or 0)>=last24),"daily":daily,"response_daily":response_daily,"avg_response_seconds":round(sum(vals)/len(vals)) if vals else None,"response_count":len(vals),"staff_leaderboard":board[:10]}


def get_transcript_info(filename):
    tid=filename.removeprefix("ticket-").removesuffix(".html"); logs=get_logs_for_ticket(tid); c=next((l for l in logs if l.get("action")=="created"),None); creator=(c or {}).get("creator") or {}
    return {"filename":filename,"username":creator.get("name") or "Unknown User","ticket_number":(c or {}).get("ticket_number")}

def list_transcript_filenames():
    db=get_db()
    if db is not None:
        try:return [d["_id"] for d in db.transcripts.find({}, {"_id":1})]
        except Exception:return []
    return [os.path.basename(f) for f in glob.glob(f"{TRANSCRIPTS_DIR}/*.html")]
def get_transcript_html(filename):
    db=get_db()
    if db is not None:
        try:
            d=db.transcripts.find_one({"_id":filename});return d["html"] if d else None
        except Exception:return None
    p=os.path.join(TRANSCRIPTS_DIR,filename)
    try:
        with open(p,encoding="utf-8") as f:return f.read()
    except Exception:return None

def save_transcript_html(filename,html):
    db=get_db()
    if db is not None:
        try:db.transcripts.replace_one({"_id":filename},{"_id":filename,"html":html,"saved_at":time.time()},upsert=True);return True
        except Exception:return False
    try:
        with open(os.path.join(TRANSCRIPTS_DIR,filename),"w",encoding="utf-8") as f:f.write(html)
        return True
    except Exception:return False

# Keep the rest of the repository's existing helpers compatible through these aliases.
def get_redirect_message(): return get_settings().get("ticket_redirect_message") or {"content":"✅ Ticket created! Please head over to {channel}."}
def get_welcome_message(): return get_settings().get("ticket_welcome_message") or {}
def save_redirect_message(content): s=get_settings();s["ticket_redirect_message"]={"content":content};save_settings(s)
def save_welcome_message(data): s=get_settings();s["ticket_welcome_message"]=data;save_settings(s)
def save_transcript_info(*args,**kwargs): return None

def _blacklist_default(e):
    if isinstance(e,dict):
        e.setdefault("user_id",str(e.get("user_id","")));e.setdefault("reason","No reason provided.");return e
    return {"user_id":str(e),"reason":"No reason provided."}
def get_blacklist_data():
    if not os.path.exists(BLACKLIST_FILE):return {"blacklisted_users":[]}
    try:
        with open(BLACKLIST_FILE) as f:d=json.load(f)
        d.setdefault("blacklisted_users",[]);d["blacklisted_users"]=[_blacklist_default(e) for e in d["blacklisted_users"]];return d
    except Exception:return {"blacklisted_users":[]}
def remove_from_blacklist(uid):
    d=get_blacklist_data(); before=len(d["blacklisted_users"]);d["blacklisted_users"]=[e for e in d["blacklisted_users"] if str(e.get("user_id"))!=str(uid)]
    with open(BLACKLIST_FILE,"w") as f:json.dump(d,f,indent=2)
    return len(d["blacklisted_users"])<before


def get_dashboard_base_url():
    base=os.getenv('DASHBOARD_URL') or os.getenv('OAUTH2_REDIRECT_URI') or 'https://ticket-bot-f184.onrender.com';base=base.removesuffix('/callback');return base.rstrip('/')

def generate_transcript_token(filename,expires_seconds=3600):
    import hmac,hashlib,base64
    expiry=int(time.time())+int(expires_seconds);payload=f"{filename}|{expiry}".encode();sig=hmac.new((os.getenv('SECRET_KEY') or 'supersecretkey123').encode(),payload,hashlib.sha256).digest();return base64.urlsafe_b64encode(payload+b"|"+sig).decode()
def verify_transcript_token(token):
    import hmac,hashlib,base64
    try:
        raw=base64.urlsafe_b64decode(token.encode());p=raw.split(b"|");fn=p[0].decode();exp=int(p[1]);sig=b"|".join(p[2:]);payload=f"{fn}|{exp}".encode();expected=hmac.new((os.getenv('SECRET_KEY') or 'supersecretkey123').encode(),payload,hashlib.sha256).digest()
        return {"filename":fn,"expires":exp} if hmac.compare_digest(sig,expected) and time.time()<=exp else None
    except Exception:return None

def generate_transcript_url(filename,expires_seconds=3600):return f"{get_dashboard_base_url()}/transcripts/{filename}?token={generate_transcript_token(filename,expires_seconds)}"

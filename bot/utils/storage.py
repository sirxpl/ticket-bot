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
COFFEE_PREFS_FILE = os.path.join(DATA_DIR, "coffee_prefs.json")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
if not os.path.exists(TICKET_LOGS_FILE):
    with open(TICKET_LOGS_FILE, "w") as f: json.dump([], f, indent=2)


def get_settings():
    db=get_db()
    if db is not None:
        try:
            doc=db.bot_settings.find_one({"_id":"singleton"})
            if doc: doc.pop("_id",None); return doc
        except Exception: logger.exception("get_settings Mongo read failed")
    if not os.path.exists(SETTINGS_FILE): return {"tickets_enabled":True}
    try:
        with open(SETTINGS_FILE) as f:return json.load(f)
    except Exception:return {"tickets_enabled":True}

def save_settings(settings):
    db=get_db()
    if db is not None:
        try:
            doc=dict(settings);doc["_id"]="singleton";db.bot_settings.replace_one({"_id":"singleton"},doc,upsert=True)
        except Exception:logger.exception("save_settings Mongo write failed")
    with open(SETTINGS_FILE,"w") as f:json.dump(settings,f,indent=4)

def set_tickets_enabled(status):
    s=get_settings();s["tickets_enabled"]=bool(status);save_settings(s)

# --- Trial schedule settings ---
def get_trial_schedule_settings():
    """Persistent config for the automatically maintained Trial Schedule post."""
    defaults = {
        "enabled": False,
        "channel_id": None,
        "message_id": None,
    }
    saved = get_settings().get("trial_schedule") or {}
    return {**defaults, **saved}

def save_trial_schedule_settings(data):
    s = get_settings()
    current = s.get("trial_schedule") or {}
    current.update(data or {})
    s["trial_schedule"] = current
    save_settings(s)
    return {**{"enabled": False, "channel_id": None, "message_id": None}, **current}

# --- Existing coffee preference API ---
def get_coffee_dm_enabled(user_id):
    uid=str(user_id);db=get_db()
    if db is not None:
        try:
            doc=db.coffee_prefs.find_one({"_id":uid})
            return bool(doc.get("dms_enabled",True)) if doc else True
        except Exception:logger.exception("coffee preference Mongo read failed")
    try:
        with open(COFFEE_PREFS_FILE) as f:return bool(json.load(f).get(uid,True))
    except Exception:return True

def set_coffee_dm_enabled(user_id,enabled):
    uid=str(user_id);db=get_db()
    if db is not None:
        try:db.coffee_prefs.replace_one({"_id":uid},{"_id":uid,"dms_enabled":bool(enabled)},upsert=True)
        except Exception:logger.exception("coffee preference Mongo write failed")
    try:
        with open(COFFEE_PREFS_FILE) as f:data=json.load(f)
    except Exception:data={}
    data[uid]=bool(enabled)
    with open(COFFEE_PREFS_FILE,"w") as f:json.dump(data,f,indent=2)

class _SafeDict(dict):
    def __missing__(self,key):return "{"+key+"}"
def render_ticket_template(template,**kwargs):
    if not template:return ""
    try:return template.format_map(_SafeDict(**kwargs))
    except Exception:return template

def get_redirect_message():return {**{"content":"✅ Ticket created! Please head over to {channel}."},**(get_settings().get("ticket_redirect_message") or {})}
def save_redirect_message(content):s=get_settings();s["ticket_redirect_message"]={"content":content};save_settings(s)
def get_welcome_message():return {**{"use_embed":True,"content":"{user_mention}","title":"🎫 {category} - {user_name}","description":"","color":"#3498db"},**(get_settings().get("ticket_welcome_message") or {})}
def save_welcome_message(data):s=get_settings();s["ticket_welcome_message"]=data;save_settings(s)

def slugify(text):return re.sub(r"[^a-z0-9]+","-",(text or "").lower().strip()).strip("-") or "ticket"
_DEFAULT_TICKET_CATEGORIES=[{"label":"General Support","description":"General help or questions","emoji":"❓"},{"label":"Report a User","description":"Report another user","emoji":"⚠️"},{"label":"Appeal / Ban Review","description":"Appeal moderation action","emoji":"📝"}]
def get_ticket_categories():
    c=get_settings().get("ticket_categories") or list(_DEFAULT_TICKET_CATEGORIES)
    for x in c:x.setdefault("blacklist_roles",[]);x.setdefault("name_prefix",slugify(x.get("label","ticket")));x.setdefault("open_note","");x.setdefault("discord_category_id",None);x.setdefault("dropdown_enabled",True);x.setdefault("variables",{})
    return c
def save_ticket_categories(categories):s=get_settings();s["ticket_categories"]=categories;save_settings(s)
def get_ticket_panel_draft():return get_settings().get("ticket_panel_draft") or {}
def save_ticket_panel_draft(draft):s=get_settings();s["ticket_panel_draft"]=draft;save_settings(s)

# --- Carry Rules agreement storage ---
def get_carry_rules_agreement(user_id):
    if not user_id:return None
    uid=str(user_id);db=get_db()
    if db is not None:
        try:return db.carry_rules_agreements.find_one({"_id":uid},{"_id":0})
        except Exception:logger.exception("Carry agreement Mongo read failed")
    return (get_settings().get("carry_rules_agreements") or {}).get(uid)

def save_carry_rules_agreement(user_id,username=None):
    uid=str(user_id);record={"user_id":uid,"username":username,"accepted_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"version":"carry-service-system-rules-v1"};db=get_db()
    if db is not None:
        try:db.carry_rules_agreements.replace_one({"_id":uid},{"_id":uid,**record},upsert=True);return record
        except Exception:logger.exception("Carry agreement Mongo write failed")
    s=get_settings();a=s.setdefault("carry_rules_agreements",{});a[uid]=record;save_settings(s);return record

# --- Tickets ---
def get_tickets_data():
    db=get_db()
    if db is not None:
        try:
            d=db.tickets_data.find_one({"_id":"singleton"})
            if not d:d={"_id":"singleton","ticket_counter":0,"active_tickets":[],"cooldowns":[]};db.tickets_data.insert_one(dict(d))
            d.setdefault("ticket_counter",0);d.setdefault("active_tickets",[]);d.setdefault("cooldowns",[]);return d
        except Exception:logger.exception("tickets Mongo read failed")
    if not os.path.exists(TICKETS_FILE):
        d={"ticket_counter":0,"active_tickets":[],"cooldowns":[]};json.dump(d,open(TICKETS_FILE,"w"),indent=2);return d
    try:
        d=json.load(open(TICKETS_FILE));d.setdefault("ticket_counter",0);d.setdefault("active_tickets",[]);d.setdefault("cooldowns",[]);return d
    except Exception:return {"ticket_counter":0,"active_tickets":[],"cooldowns":[]}

def _save_tickets_data(data):
    db=get_db()
    if db is not None:
        try:d=dict(data);d["_id"]="singleton";db.tickets_data.replace_one({"_id":"singleton"},d,upsert=True);return True
        except Exception:return False
    try:json.dump(data,open(TICKETS_FILE,"w"),indent=2);return True
    except Exception:return False

def increment_ticket_counter():
    db=get_db()
    if db is not None:
        try:return db.tickets_data.find_one_and_update({"_id":"singleton"},{"$inc":{"ticket_counter":1}},upsert=True,return_document=ReturnDocument.AFTER).get("ticket_counter",1)
        except Exception:pass
    d=get_tickets_data();d["ticket_counter"]=d.get("ticket_counter",0)+1;_save_tickets_data(d);return d["ticket_counter"]
def set_ticket_counter(value):d=get_tickets_data();d["ticket_counter"]=int(value);_save_tickets_data(d)
def get_category_counter(prefix):return int(get_settings().get("category_counters",{}).get(prefix,0))
def set_category_counter(prefix,value):s=get_settings();c=s.get("category_counters",{});c[prefix]=int(value);s["category_counters"]=c;save_settings(s)
def increment_category_counter(prefix):s=get_settings();c=s.get("category_counters",{});c[prefix]=int(c.get(prefix,0))+1;s["category_counters"]=c;save_settings(s);return c[prefix]
def is_ticket_channel(channel_id):return any(str(t.get("channel_id"))==str(channel_id) for t in get_tickets_data().get("active_tickets",[]))
def get_active_ticket_for_user(user_id):return next((t for t in get_tickets_data().get("active_tickets",[]) if str(t.get("user_id"))==str(user_id)),None)
def get_active_ticket(channel_id):return next((t for t in get_tickets_data().get("active_tickets",[]) if str(t.get("channel_id"))==str(channel_id)),None)
def update_active_ticket(channel_id,**kwargs):
    d=get_tickets_data()
    for t in d.get("active_tickets",[]):
        if str(t.get("channel_id"))==str(channel_id):t.update(kwargs)
    _save_tickets_data(d)
def add_active_ticket(ticket_id,channel_id,user_id,ticket_number=None):
    d=get_tickets_data()
    if not any(t.get("ticket_id")==str(ticket_id) for t in d["active_tickets"]):
        now=time.time();e={"ticket_id":str(ticket_id),"channel_id":str(channel_id),"user_id":str(user_id),"created_at":now,"last_activity":now,"autoclose_disabled":False,"reminder_sent":False};
        if ticket_number is not None:e["ticket_number"]=int(ticket_number)
        d["active_tickets"].append(e);_save_tickets_data(d)
def touch_ticket_activity(channel_id):update_active_ticket(channel_id,last_activity=time.time(),reminder_sent=False)
def set_autoclose_disabled(channel_id,disabled=True):update_active_ticket(channel_id,autoclose_disabled=bool(disabled))
def remove_active_ticket(ticket_id):d=get_tickets_data();d["active_tickets"]=[t for t in d.get("active_tickets",[]) if str(t.get("ticket_id"))!=str(ticket_id)];_save_tickets_data(d)
def add_cooldown(user_id,hours=8):
    d=get_tickets_data();expires=int(time.time())+int(hours)*3600;d["cooldowns"]=[c for c in d.get("cooldowns",[]) if str(c.get("user_id"))!=str(user_id)];d["cooldowns"].append({"user_id":str(user_id),"expires_ts":expires,"expires_at":datetime.datetime.fromtimestamp(expires,datetime.timezone.utc).isoformat()});_save_tickets_data(d)
def remove_cooldown(user_id):d=get_tickets_data();b=len(d.get("cooldowns",[]));d["cooldowns"]=[c for c in d.get("cooldowns",[]) if str(c.get("user_id"))!=str(user_id)];_save_tickets_data(d);return len(d["cooldowns"])<b
def _prune_expired_cooldowns(d):
    """Remove cooldown entries whose expires_ts has already passed. Returns
    True if anything was actually removed, so callers know whether the
    pruned result needs to be persisted."""
    now=int(time.time())
    cooldowns=d.get("cooldowns",[])
    kept=[c for c in cooldowns if int(c.get("expires_ts",0) or 0)>now]
    if len(kept)!=len(cooldowns):
        d["cooldowns"]=kept
        return True
    return False
def get_cooldowns():
    """Returns only cooldowns that haven't expired yet — expired ones are
    pruned from storage as a side effect of reading, so they don't pile up
    or show as stale entries on the dashboard."""
    d=get_tickets_data()
    if _prune_expired_cooldowns(d):
        _save_tickets_data(d)
    return d.get("cooldowns",[])
def is_on_cooldown(user_id):
    n=int(time.time())
    for c in get_cooldowns():
        if str(c.get("user_id"))==str(user_id) and int(c.get("expires_ts",0))>n:return True,c
    return False,None

# --- Blacklist ---
def _norm_blacklist(e):return e if isinstance(e,dict) else {"user_id":str(e),"reason":"No reason provided."}
def get_blacklist_data():
    if not os.path.exists(BLACKLIST_FILE):return {"blacklisted_users":[]}
    try:d=json.load(open(BLACKLIST_FILE));d.setdefault("blacklisted_users",[]);d["blacklisted_users"]=[_norm_blacklist(e) for e in d["blacklisted_users"]];return d
    except Exception:return {"blacklisted_users":[]}
def add_to_blacklist(user_id,reason="No reason provided.",added_by=None,hours=None,blacklist_type="regular"):
    d=get_blacklist_data();d["blacklisted_users"]=[e for e in d["blacklisted_users"] if str(e.get("user_id"))!=str(user_id)];d["blacklisted_users"].append({"user_id":str(user_id),"reason":reason,"added_by":str(added_by) if added_by else None,"added_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"expires_ts":int(time.time()+hours*3600) if hours else None,"blacklist_type":blacklist_type});json.dump(d,open(BLACKLIST_FILE,"w"),indent=2);return True
def remove_from_blacklist(user_id):
    d=get_blacklist_data();b=len(d["blacklisted_users"]);d["blacklisted_users"]=[e for e in d["blacklisted_users"] if str(e.get("user_id"))!=str(user_id)];json.dump(d,open(BLACKLIST_FILE,"w"),indent=2);return len(d["blacklisted_users"])<b

# --- Ticket logs + analytics ---
def append_ticket_log(entry):
    db=get_db()
    if db is not None:
        try:db.ticket_logs.insert_one(dict(entry));return True
        except Exception:logger.exception("ticket log Mongo write failed")
    try:
        logs=get_ticket_logs();logs.append(entry);json.dump(logs,open(TICKET_LOGS_FILE,"w"),indent=2);return True
    except Exception:return False
def get_ticket_logs():
    db=get_db()
    if db is not None:
        try:return list(db.ticket_logs.find({}, {"_id":0}))
        except Exception:return []
    try:return json.load(open(TICKET_LOGS_FILE))
    except Exception:return []
def get_logs_for_ticket(ticket_id):return [l for l in get_ticket_logs() if str(l.get("ticket_id"))==str(ticket_id)]
def get_ticket_analytics(days=30):
    days=max(1,min(int(days or 30),90));logs=get_ticket_logs();now=time.time();last24=now-86400
    def ts(x):
        v=x.get("timestamp")
        if isinstance(v,(int,float)):return float(v)
        try:return datetime.datetime.fromisoformat(str(v).replace("Z","+00:00")).timestamp()
        except Exception:return None
    created=[l for l in logs if l.get("action")=="created"];closed=[l for l in logs if l.get("action")=="closed"];staff=[l for l in logs if l.get("action") in {"staff_message","staff_response"}]
    daily=[]
    for o in range(days-1,-1,-1):
        d=datetime.datetime.now(datetime.timezone.utc).date()-datetime.timedelta(days=o);s=datetime.datetime.combine(d,datetime.time.min,tzinfo=datetime.timezone.utc).timestamp();e=s+86400;daily.append({"date":d.strftime("%b %d"),"created":sum(1 for l in created if s<=(ts(l) or -1)<e),"closed":sum(1 for l in closed if s<=(ts(l) or -1)<e),"activity":sum(1 for l in staff if s<=(ts(l) or -1)<e)})
    by={};first={}
    for l in created:
        tid=str(l.get("ticket_id") or "");t=ts(l)
        if tid and t is not None:by.setdefault(tid,t)
    for l in sorted(staff,key=lambda x:ts(x) or 0):
        tid=str(l.get("ticket_id") or "");t=ts(l);c=by.get(tid)
        if tid and t is not None and c is not None and t>=c:first.setdefault(tid,t-c)
    vals=[v for v in first.values() if 0<=v<=30*86400]
    rows={}
    for l in staff+closed:
        a=l.get("actor") or l.get("executor") or {};uid=str(a.get("id") or l.get("user_id") or "")
        if not uid:continue
        r=rows.setdefault(uid,{"id":uid,"name":a.get("name") or "Unknown Staff","responses":0,"closed":0});r["responses"]+=1 if l in staff else 0;r["closed"]+=1 if l in closed else 0;r["name"]=a.get("name") or r["name"]
    board=[]
    for r in rows.values():r["activity"]=r["responses"]+r["closed"];board.append(r)
    board.sort(key=lambda r:(-r["activity"],-r["responses"],r["name"].lower()))
    response_daily=[]
    for o in range(days-1,-1,-1):
        d=datetime.datetime.now(datetime.timezone.utc).date()-datetime.timedelta(days=o);s=datetime.datetime.combine(d,datetime.time.min,tzinfo=datetime.timezone.utc).timestamp();e=s+86400;v=[delay for tid,delay in first.items() if s<=by.get(tid,0)+delay<e];response_daily.append({"date":d.strftime("%b %d"),"avg_response_seconds":round(sum(v)/len(v)) if v else None})
    return {"all_time_created":len(created),"all_time_closed":len(closed),"last_24h_created":sum(1 for l in created if (ts(l) or 0)>=last24),"last_24h_closed":sum(1 for l in closed if (ts(l) or 0)>=last24),"daily":daily,"response_daily":response_daily,"avg_response_seconds":round(sum(vals)/len(vals)) if vals else None,"response_count":len(vals),"staff_leaderboard":board[:10]}

# --- Transcripts ---
def get_transcript_info(filename):
    tid=filename.removeprefix("ticket-").removesuffix(".html");c=next((l for l in get_logs_for_ticket(tid) if l.get("action")=="created"),None);cr=(c or {}).get("creator") or {};return {"filename":filename,"username":cr.get("name") or "Unknown User","ticket_number":(c or {}).get("ticket_number")}
def list_transcript_filenames():
    db=get_db()
    if db is not None:
        try:return [d["_id"] for d in db.transcripts.find({}, {"_id":1})]
        except Exception:return []
    return [os.path.basename(f) for f in glob.glob(f"{TRANSCRIPTS_DIR}/*.html")]
def get_transcript_html(filename):
    db=get_db()
    if db is not None:
        try:d=db.transcripts.find_one({"_id":filename});return d["html"] if d else None
        except Exception:return None
    try:return open(os.path.join(TRANSCRIPTS_DIR,filename),encoding="utf-8").read()
    except Exception:return None
def save_transcript_html(filename,html):
    db=get_db()
    if db is not None:
        try:db.transcripts.replace_one({"_id":filename},{"_id":filename,"html":html,"saved_at":time.time()},upsert=True);return True
        except Exception:return False
    try:open(os.path.join(TRANSCRIPTS_DIR,filename),"w",encoding="utf-8").write(html);return True
    except Exception:return False

# --- Transcript links ---
def get_dashboard_base_url():
    b=os.getenv('DASHBOARD_URL') or os.getenv('OAUTH2_REDIRECT_URI') or 'https://ticket-bot-f184.onrender.com';return b.removesuffix('/callback').rstrip('/')
def generate_transcript_token(filename,expires_seconds=3600):
    import hmac,hashlib,base64
    exp=int(time.time())+int(expires_seconds);p=f"{filename}|{exp}".encode();sig=hmac.new((os.getenv('SECRET_KEY') or 'supersecretkey123').encode(),p,hashlib.sha256).digest();return base64.urlsafe_b64encode(p+b"|"+sig).decode()
def verify_transcript_token(token):
    import hmac,hashlib,base64
    try:
        raw=base64.urlsafe_b64decode(token.encode());parts=raw.split(b"|");fn=parts[0].decode();exp=int(parts[1]);sig=b"|".join(parts[2:]);p=f"{fn}|{exp}".encode();key=(os.getenv('SECRET_KEY') or 'supersecretkey123').encode();return {"filename":fn,"expires":exp} if hmac.compare_digest(sig,hmac.new(key,p,hashlib.sha256).digest()) and time.time()<=exp else None
    except Exception:return None
def generate_transcript_url(filename,expires_seconds=3600):return f"{get_dashboard_base_url()}/transcripts/{filename}?token={generate_transcript_token(filename,expires_seconds)}"

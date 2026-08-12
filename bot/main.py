import os
import glob
import json
import asyncio
from functools import wraps
from dotenv import load_dotenv

import discord
from discord.ext import commands
from flask import (
    Flask, 
    render_template, 
    request, 
    redirect, 
    flash, 
    session, 
    url_for, 
    jsonify, 
    send_from_directory
)
from requests_oauthlib import OAuth2Session

# Import storage helpers
from utils.storage import (
    get_tickets_data, 
    get_blacklist_data, 
    remove_from_blacklist, 
    TRANSCRIPTS_DIR,
    get_settings,
    set_tickets_enabled,
    get_ticket_logs,
    get_logs_for_ticket,
    get_transcript_info
)

# Environment & OAuth Setup
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("OAUTH2_REDIRECT_URI", "https://ticket-bot-f184.onrender.com/callback")

AUTHORIZATION_BASE_URL = 'https://discord.com/api/oauth2/authorize'
TOKEN_URL = 'https://discord.com/api/oauth2/token'

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

# Flask App
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey123")

# Discord Bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


def make_oauth_session(state=None):
    return OAuth2Session(
        client_id=CLIENT_ID,
        state=state,
        scope=['identify', 'guilds', 'guilds.members.read'],
        redirect_uri=REDIRECT_URI
    )


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user"):
            flash("🔒 Please log in with Discord to access the dashboard.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


# --- FLASK ROUTES ---
@app.route("/")
def home():
    user_data = session.get("user", None)
    if not user_data:
        return render_template("dashboard.html", user=None)

    guild = bot.guilds[0] if bot.guilds else None
    channels = guild.text_channels if guild else []
    categories = guild.categories if guild else []
    roles = guild.roles if guild else []
    
    tickets_info = get_tickets_data()
    blacklist_info = get_blacklist_data()
    settings = get_settings()

    transcripts = []
    if os.path.exists(TRANSCRIPTS_DIR):
        transcripts = [
            get_transcript_info(os.path.basename(f))
            for f in glob.glob(f"{TRANSCRIPTS_DIR}/*.html")
        ]

    return render_template(
        "dashboard.html",
        user=user_data,
        channels=channels,
        categories=categories,
        roles=roles,
        tickets_enabled=settings.get("tickets_enabled", True),
        total_tickets=tickets_info.get("ticket_counter", 0),
        active_tickets=tickets_info.get("active_tickets", []),
        transcripts=transcripts,
        cooldowns=tickets_info.get("cooldowns", []),
        blacklisted_users=blacklist_info.get("blacklisted_users", [])
    )


@app.route("/login")
def login():
    discord_sess = make_oauth_session()
    authorization_url, state = discord_sess.authorization_url(AUTHORIZATION_BASE_URL)
    session['oauth2_state'] = state
    return redirect(authorization_url)


@app.route("/callback")
def callback():
    if request.args.get('error'):
        return request.args['error']
    
    discord_sess = make_oauth_session(state=session.get('oauth2_state'))
    token = discord_sess.fetch_token(
        TOKEN_URL,
        client_secret=CLIENT_SECRET,
        authorization_response=request.url
    )
    session['oauth2_token'] = token
    
    user_data = discord_sess.get('https://discord.com/api/users/@me').json()
    user_id = user_data.get('id')
    avatar_hash = user_data.get('avatar')
    
    avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png" if avatar_hash else "https://cdn.discordapp.com/embed/avatars/0.png"

    session['user'] = {
        'username': user_data.get('username'),
        'id': user_id,
        'avatar_url': avatar_url
    }
    
    flash(f"👋 Welcome back, {user_data.get('username')}!", "success")
    return redirect(url_for('home'))


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for('home'))


@app.route("/dashboard/toggle-tickets", methods=["POST"])
@login_required
def toggle_tickets():
    is_enabled = request.form.get("tickets_enabled") in ["on", "true", "True"]
    set_tickets_enabled(is_enabled)
    
    status_text = "enabled" if is_enabled else "disabled"
    flash(f"⚙️ Ticket creation has been {status_text}.", "success" if is_enabled else "warning")
    return redirect("/")


@app.route("/transcripts/<path:filename>")
def get_transcript(filename):
    # Allow access with a valid short-lived token (for DMed links); otherwise require login
    from flask import request, abort
    token = request.args.get('token')
    if token:
        from utils.storage import verify_transcript_token
        info = verify_transcript_token(token)
        if not info or info.get('filename') != filename:
            abort(403)
        return send_from_directory(TRANSCRIPTS_DIR, filename)

    # no token, require logged-in session
    if not session.get('user'):
        flash("🔒 Please log in with Discord to access the transcript.", "warning")
        return redirect(url_for('login'))
    return send_from_directory(TRANSCRIPTS_DIR, filename)


@app.route("/tickets/logs")
@login_required
def view_ticket_logs():
    logs = get_ticket_logs()
    # sort descending
    logs = sorted(logs, key=lambda l: l.get("timestamp", ""), reverse=True)
    return render_template("ticket_logs.html", logs=logs)


# Temporary unauthenticated debug endpoint to inspect ticket logs quickly
@app.route("/debug/ticket-logs")
def debug_ticket_logs():
    try:
        logs = get_ticket_logs()
        # return last 100 entries
        last = sorted(logs, key=lambda l: l.get("timestamp", ""), reverse=True)[:100]
        return jsonify({"ok": True, "count": len(last), "logs": last})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/tickets/<ticket_id>")
@login_required
def view_ticket(ticket_id):
    logs = get_logs_for_ticket(ticket_id)
    ticket = None
    if logs:
        # take earliest create log as ticket meta
        ticket = logs[0]
        created = next((l for l in logs if l.get("action") == "created"), None)
    else:
        ticket = {"ticket_id": ticket_id, "fields": {}, "ticket_name": None}
        created = None

    # try to find a transcript file containing the ticket name or id
    transcript_url = None
    try:
        import glob
        import os
        for f in glob.glob(f"{TRANSCRIPTS_DIR}/*.html"):
            name = os.path.basename(f)
            if ticket_id in name or (ticket.get("ticket_name") and ticket.get("ticket_name") in name):
                transcript_url = f"/transcripts/{name}"
                break
    except Exception:
        transcript_url = None

    created_at = created.get("timestamp") if created else None
    return render_template("ticket_detail.html", ticket=ticket, logs=logs, transcript_url=transcript_url, created_at=created_at)


@app.route("/api/unblacklist/<user_id>", methods=["POST"])
@login_required
def api_unblacklist(user_id):
    remove_from_blacklist(user_id)
    return jsonify({"success": True})


@app.route("/api/remove-cooldown/<user_id>", methods=["POST"])
@login_required
def api_remove_cooldown(user_id):
    from utils.storage import remove_cooldown
    ok = remove_cooldown(user_id)
    return jsonify({"success": ok})


@app.route("/dashboard/tickets", methods=["POST"])
@login_required
def deploy_ticket_panel():
    def safe_int(val):
        try:
            return int(val) if val and str(val).strip() else None
        except ValueError:
            return None

    channel_id = safe_int(request.form.get("channel_id"))
    category_id = safe_int(request.form.get("category_id"))
    support_role_id = safe_int(request.form.get("support_role_id"))

    if not channel_id:
        flash("❌ Please select a valid target channel.", "danger")
        return redirect("/")

    title = request.form.get("title", "Request Carry")
    description = request.form.get("description", "Click below to request a carry ticket!")
    embed_color = request.form.get("embed_color", "#58b9ff")
    image_url = request.form.get("image_url", "").strip() or None
    thumbnail_url = request.form.get("thumbnail_url", "").strip() or None
    footer_text = request.form.get("footer_text", "").strip() or None

    raw_fields_json = request.form.get("fields_json", "[]")
    try:
        fields = json.loads(raw_fields_json)
    except Exception:
        fields = []

    cog = bot.get_cog("TicketsCog") or bot.get_cog("Tickets")
    if cog:
        future = asyncio.run_coroutine_threadsafe(
            cog.deploy_panel_from_dashboard(
                channel_id=channel_id,
                title=title,
                description=description,
                category_id=category_id,
                support_role_id=support_role_id,
                color=embed_color,
                image_url=image_url,
                thumbnail_url=thumbnail_url,
                footer_text=footer_text,
                fields=fields
            ),
            bot.loop
        )
        try:
            success, msg = future.result(timeout=10)
            if success:
                flash("🎉 Carry Panel deployed successfully!", "success")
            else:
                flash(f"❌ {msg}", "danger")
        except Exception as e:
            flash(f"❌ Error deploying panel: {e}", "danger")
    else:
        flash("❌ Ticket cog not found.", "danger")

    return redirect("/")


# --- BOT EVENT HANDLERS & RUNNER ---
@bot.event
async def setup_hook():
    if os.path.exists("cogs"):
        for filename in os.listdir("cogs"):
            if filename.endswith(".py"):
                await bot.load_extension(f"cogs.{filename[:-3]}")

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    print(f"✅ Bot logged in as {bot.user}")


def run_flask():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    import threading
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ Error: DISCORD_BOT_TOKEN environment variable is missing.")

import os
import glob
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
from utils.storage import get_tickets_data, get_blacklist_data, remove_from_blacklist, TRANSCRIPTS_DIR

# ---------------------------------------------------------------------------
# INITIALIZATION & ENVIRONMENT SETUP
# ---------------------------------------------------------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("OAUTH2_REDIRECT_URI", "https://ticket-bot-f184.onrender.com/callback")

AUTHORIZATION_BASE_URL = 'https://discord.com/api/oauth2/authorize'
TOKEN_URL = 'https://discord.com/api/oauth2/token'

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

# Flask Setup
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey123")

# Discord Bot Setup
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

# ---------------------------------------------------------------------------
# AUTHENTICATION DECORATOR (STRICT GATEKEEPER)
# ---------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user"):
            flash("🔒 You must log in with Discord to access the dashboard.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------------------------
# FLASK WEB DASHBOARD ROUTES
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    user_data = session.get("user", None)

    if not user_data:
        return render_template("dashboard.html", user=None)

    guild = bot.guilds[0] if bot.guilds else None
    channels = guild.text_channels if guild else []
    categories = guild.categories if guild else []
    roles = guild.roles if guild else []
    
    # Load dynamic data from storage
    tickets_info = get_tickets_data()
    blacklist_info = get_blacklist_data()
    
    transcripts = []
    if os.path.exists(TRANSCRIPTS_DIR):
        transcripts = [os.path.basename(f) for f in glob.glob(f"{TRANSCRIPTS_DIR}/*.html")]

    return render_template(
        "dashboard.html",
        user=user_data,
        channels=channels,
        categories=categories,
        roles=roles,
        total_tickets=tickets_info.get("ticket_counter", 0),
        active_tickets=tickets_info.get("active_tickets", []), # 👈 Updated
        transcripts=transcripts,
        cooldowns=tickets_info.get("cooldowns", []),           # 👈 Updated
        blacklisted_users=blacklist_info.get("blacklisted_users", []) # 👈 Updated
    )

# ---------------------------------------------------------------------------
# OAUTH2 AUTHENTICATION ROUTES
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# PROTECTED API & PANEL ROUTES
# ---------------------------------------------------------------------------
@app.route("/transcripts/<path:filename>")
@login_required
def get_transcript(filename):
    return send_from_directory(TRANSCRIPTS_DIR, filename)

@app.route("/api/unblacklist/<user_id>", methods=["POST"])
@login_required
def api_unblacklist(user_id):
    remove_from_blacklist(user_id)
    return jsonify({"success": True})

@app.route("/dashboard/tickets", methods=["POST"])
@login_required
def deploy_ticket_panel():
    channel_id = int(request.form.get("channel_id", 0))
    category_id = int(request.form.get("category_id", 0)) if request.form.get("category_id") else None
    support_role_id = int(request.form.get("support_role_id", 0)) if request.form.get("support_role_id") else None
    log_channel_id = int(request.form.get("log_channel_id", 0)) if request.form.get("log_channel_id") else None
    title = request.form.get("title", "Request Carry")
    description = request.form.get("description", "Click below to request a carry ticket!")

    cog = bot.get_cog("TicketsCog")
    if cog:
        future = asyncio.run_coroutine_threadsafe(
            cog.deploy_panel_from_dashboard(
                channel_id=channel_id,
                title=title,
                description=description,
                category_id=category_id,
                support_role_id=support_role_id,
                log_channel_id=log_channel_id
            ),
            bot.loop
        )
        try:
            success, msg = future.result(timeout=10)
            if success:
                flash("🎉 Carry panel successfully deployed to Discord!", "success")
            else:
                flash(f"❌ {msg}", "danger")
        except Exception as e:
            flash(f"❌ Failed to send panel: {e}", "danger")
    else:
        flash("❌ Tickets cog not loaded.", "danger")

    return redirect("/")

# ---------------------------------------------------------------------------
# STARTUP & RUNNERS
# ---------------------------------------------------------------------------
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
        print(f"Synced {len(synced)} application commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    print(f"✅ Bot logged in as {bot.user}")


def run_flask():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    import threading
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ Error: DISCORD_BOT_TOKEN environment variable is missing.")

import os
import io
import asyncio
import time
import json
import html
import requests
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Select, Modal, TextInput
from flask import Flask, render_template_string, jsonify, request, redirect, session, send_file
from threading import Thread

# Allowed Individual Users (Bot Owners, Developers, etc.)
ALLOWED_USER_IDS = [
    777341204047331348,   # Your Personal Discord User ID
    1012751329845841921   # Co-developer User ID
]

# --- CONFIGURATION ---
BLACKLIST_ROLE_ID = 1530330613029015704

# Access Control Config
GUILD_ID = 1530005676003426487         # Your Discord Server ID
ALLOWED_ROLE_IDS = [
    1530330612567904276               # Staff/Admin Role ID
]

# Discord OAuth2 Config
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = "https://ticket-bot-f184.onrender.com/callback"
DISCORD_API_URL = "https://discord.com/api/v10"

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Global trackers
user_cooldowns = {}  # Format: {user_id: timestamp_when_cooldown_expires}

# --- PERSISTENT DATA & TRANSCRIPT STORAGE ---
STORAGE_DIR = "/var/data" if os.path.exists("/var/data") else "."
DATA_FILE = os.path.join(STORAGE_DIR, "tickets.json")
TRANSCRIPT_DIR = os.path.join(STORAGE_DIR, "transcripts")
os.makedirs(TRANSCRIPT_DIR, exist_ok=True)

def load_data(file_path, default_data):
    if not os.path.exists(file_path):
        try:
            with open(file_path, "w") as f:
                json.dump(default_data, f, indent=4)
            return default_data
        except Exception as e:
            print(f"Error creating missing file {file_path}: {e}")
            return default_data

    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        return default_data

def save_data(file_path, data):
    try:
        with open(file_path, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error saving to {file_path}: {e}")

# Load initial ticket counter from file
ticket_counter = load_data(DATA_FILE, {"ticket_counter": 0}).get("ticket_counter", 0)

# --- PERMISSION HELPER FUNCTION ---
def can_user_close_ticket(user: discord.Member, channel: discord.TextChannel) -> bool:
    """Checks if a user is either the Ticket Owner or a Staff/Admin member."""
    if user.guild_permissions.administrator or any(role.id in ALLOWED_ROLE_IDS for role in user.roles):
        return True

    if channel.topic and f"OwnerID:{user.id}" in channel.topic:
        return True

    return False

# --- FLASK DASHBOARD & OAUTH2 SERVER ---
app = Flask('')
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24))

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Dashboard Login</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .login-card { background-color: #1e293b; padding: 40px; border-radius: 12px; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
        .btn { background-color: #5865F2; color: white; border: none; padding: 12px 24px; border-radius: 8px; font-size: 1rem; font-weight: bold; cursor: pointer; text-decoration: none; display: inline-block; margin-top: 20px; }
        .btn:hover { background-color: #4752C4; }
    </style>
</head>
<body>
    <div class="login-card">
        <h1>🎫 Carry Bot Dashboard</h1>
        <p>Please authorize with Discord to access the control panel.</p>
        <a href="/login" class="btn">🔑 Login with Discord</a>
    </div>
</body>
</html>
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ticket Bot Dashboard</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 40px; }
        .container { max-width: 900px; margin: 0 auto; }
        .header { display: flex; align-items: center; justify-content: space-between; border-bottom: 2px solid #1e293b; padding-bottom: 20px; margin-bottom: 30px; }
        .user-info { display: flex; align-items: center; gap: 12px; }
        .avatar { width: 40px; height: 40px; border-radius: 50%; }
        .logout-btn { background: #ef4444; color: white; padding: 6px 12px; border-radius: 6px; text-decoration: none; font-size: 0.85rem; }
        .status-badge { background-color: #10b981; color: #022c22; padding: 6px 16px; border-radius: 20px; font-weight: bold; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .card { background-color: #1e293b; border-radius: 12px; padding: 24px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }
        .card h3 { margin: 0 0 10px 0; color: #94a3b8; font-size: 0.95rem; text-transform: uppercase; }
        .card .value { font-size: 2.2rem; font-weight: bold; color: #38bdf8; }
        .section { background-color: #1e293b; border-radius: 12px; padding: 24px; margin-bottom: 20px; }
        ul { list-style-type: none; padding: 0; margin: 0; }
        li { background: #0f172a; padding: 12px; border-radius: 6px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; gap: 10px; }
        
        .btn-action { background: #f59e0b; color: #000; border: none; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-weight: bold; }
        .btn-action:hover { opacity: 0.9; }
        .btn-green { background: #10b981; color: #000; }
        .btn-discord { background: #5865F2; color: #fff; text-decoration: none; padding: 6px 12px; border-radius: 6px; font-weight: bold; font-size: 0.85rem; }
        .btn-view { background: #38bdf8; color: #0f172a; text-decoration: none; padding: 6px 12px; border-radius: 6px; font-weight: bold; font-size: 0.85rem; }

        /* Modal styling */
        .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 0, 0, 0.7); justify-content: center; align-items: center; z-index: 1000; }
        .modal { background: #1e293b; padding: 30px; border-radius: 12px; max-width: 400px; text-align: center; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        .modal h3 { margin-top: 0; color: #f8fafc; }
        .modal p { color: #94a3b8; font-size: 0.95rem; }
        .modal-buttons { display: flex; justify-content: center; gap: 12px; margin-top: 20px; }
        .btn-modal-confirm { background: #ef4444; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: bold; }
        .btn-modal-cancel { background: #64748b; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>🎫 Carry Bot Control Panel</h1>
                <span class="status-badge">🟢 Bot Online</span>
            </div>
            <div class="user-info">
                <img src="{{ user_avatar }}" class="avatar" alt="Avatar">
                <span>Welcome, <strong>{{ username }}</strong>!</span>
                <a href="/logout" class="logout-btn">Logout</a>
            </div>
        </div>
        
        <div class="grid">
            <div class="card">
                <h3>Total Tickets Created</h3>
                <div class="value">#{{ ticket_count }}</div>
            </div>
            <div class="card">
                <h3>Active User Cooldowns</h3>
                <div class="value">{{ active_cooldowns }}</div>
            </div>
        </div>

        <div class="section">
            <h3>📡 Live Active Ticket Channels</h3>
            <ul id="active-tickets-list">Loading...</ul>
        </div>

        <div class="section">
            <h3>📁 Past Closed Tickets & Message Logs</h3>
            <ul id="transcript-list">Loading...</ul>
        </div>

        <div class="section">
            <h3>⏳ Users Currently on Cooldown</h3>
            <ul id="cooldown-list">Loading...</ul>
        </div>

        <div class="section">
            <h3>🚫 Blacklisted Users</h3>
            <ul id="blacklist-list">Loading...</ul>
        </div>
    </div>

    <div id="confirmModal" class="modal-overlay">
        <div class="modal">
            <h3 id="modalTitle">Are you sure?</h3>
            <p id="modalDesc">This action cannot be undone.</p>
            <div class="modal-buttons">
                <button id="modalConfirmBtn" class="btn-modal-confirm">Confirm</button>
                <button onclick="closeModal()" class="btn-modal-cancel">Cancel</button>
            </div>
        </div>
    </div>

    <script>
        let pendingAction = null;

        function promptConfirmation(title, message, actionCallback) {
            document.getElementById('modalTitle').innerText = title;
            document.getElementById('modalDesc').innerText = message;
            pendingAction = actionCallback;
            document.getElementById('confirmModal').style.display = 'flex';
        }

        function closeModal() {
            document.getElementById('confirmModal').style.display = 'none';
            pendingAction = null;
        }

        document.getElementById('modalConfirmBtn').onclick = async function() {
            if (pendingAction) {
                await pendingAction();
            }
            closeModal();
        };

        function removeCooldownPrompt(userId) {
            promptConfirmation(
                "Clear Cooldown?",
                `Are you sure you want to remove the cooldown for User ID: ${userId}?`,
                async () => {
                    const res = await fetch('/api/remove_cooldown', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ user_id: userId })
                    });
                    const data = await res.json();
                    alert(data.message || 'Action executed.');
                    loadAdvancedData();
                }
            );
        }

        function removeBlacklistPrompt(userId, userName) {
            promptConfirmation(
                "Un-blacklist User?",
                `Are you sure you want to remove ${userName} (${userId}) from the blacklist?`,
                async () => {
                    const res = await fetch('/api/remove_blacklist', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ user_id: userId })
                    });
                    const data = await res.json();
                    alert(data.message || 'Action executed.');
                    loadAdvancedData();
                }
            );
        }

        async function loadAdvancedData() {
            try {
                const ticketsRes = await fetch('/api/active_tickets');
                const ticketsData = await ticketsRes.json();
                const ticketsList = document.getElementById('active-tickets-list');
                
                ticketsList.innerHTML = ticketsData.tickets.length ? '' : '<li>No active ticket channels</li>';
                
                ticketsData.tickets.forEach(ticket => {
                    ticketsList.innerHTML += `
                        <li>
                            <span><strong>#${ticket.name}</strong></span>
                            <a href="https://discord.com/channels/${ticketsData.guild_id}/${ticket.id}" target="_blank" class="btn-discord">↗️ Open in Discord</a>
                        </li>`;
                });

                const transRes = await fetch('/api/transcripts');
                const transData = await transRes.json();
                const transList = document.getElementById('transcript-list');
                transList.innerHTML = transData.length ? '' : '<li>No saved ticket transcripts yet.</li>';

                transData.forEach(item => {
                    transList.innerHTML += `
                        <li>
                            <span>📄 Ticket: <strong>#${item.channel_name}</strong></span>
                            <a href="/transcript/${item.channel_name}" target="_blank" class="btn-view">📄 View Message History</a>
                        </li>`;
                });

                const cdRes = await fetch('/api/cooldowns');
                const cdData = await cdRes.json();
                const cdList = document.getElementById('cooldown-list');
                cdList.innerHTML = cdData.length ? '' : '<li>No users on cooldown</li>';
                cdData.forEach(user => {
                    cdList.innerHTML += `
                        <li>
                            <span>User ID: ${user.user_id} (${user.expires_in_seconds}s remaining)</span>
                            <button onclick="removeCooldownPrompt('${user.user_id}')" class="btn-action">⚡ Clear Cooldown</button>
                        </li>`;
                });

                const blRes = await fetch('/api/blacklisted');
                const blData = await blRes.json();
                const blList = document.getElementById('blacklist-list');
                blList.innerHTML = blData.length ? '' : '<li>No blacklisted users found</li>';
                blData.forEach(user => {
                    blList.innerHTML += `
                        <li>
                            <span>${user.name} (${user.id})</span>
                            <button onclick="removeBlacklistPrompt('${user.id}', '${user.name}')" class="btn-action btn-green">✅ Un-blacklist</button>
                        </li>`;
                });
            } catch (e) {
                console.error(e);
            }
        }
        window.onload = loadAdvancedData;
    </script>
</body>
</html>
"""

# --- OAUTH2 & WEB ROUTES ---
@app.route('/')
def index():
    if 'user_id' not in session:
        return render_template_string(LOGIN_HTML)
    
    now = time.time()
    active_cooldown_count = sum(1 for expire in user_cooldowns.values() if now < expire)
    avatar_url = f"https://cdn.discordapp.com/avatars/{session['user_id']}/{session['avatar']}.png" if session.get('avatar') else "https://cdn.discordapp.com/embed/avatars/0.png"
    
    return render_template_string(
        DASHBOARD_HTML, 
        ticket_count=f"{ticket_counter:04d}", 
        active_cooldowns=active_cooldown_count,
        username=session.get('username', 'User'),
        user_avatar=avatar_url
    )

@app.route('/transcript/<channel_name>')
def view_transcript(channel_name):
    if 'user_id' not in session:
        return redirect('/login')

    filename = f"transcript-{channel_name}.html"
    filepath = os.path.join(TRANSCRIPT_DIR, filename)

    if not os.path.exists(filepath):
        # Fallback check for old plain text format
        txt_filepath = os.path.join(TRANSCRIPT_DIR, f"transcript-{channel_name}.txt")
        if os.path.exists(txt_filepath):
            return send_file(txt_filepath, mimetype='text/plain')
        return "<h2 style='color:red; font-family:sans-serif; text-align:center;'>Transcript file not found.</h2>", 404

    return send_file(filepath)

@app.route('/login')
def login():
    discord_auth_url = (
        f"{DISCORD_API_URL}/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20guilds.members.read"
    )
    return redirect(discord_auth_url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return "Authorization failed or access denied.", 400

    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    
    token_response = requests.post(f"{DISCORD_API_URL}/oauth2/token", data=data, headers=headers)
    token_json = token_response.json()
    access_token = token_json.get('access_token')

    if not access_token:
        return f"OAuth Error: {token_json}", 400

    user_headers = {'Authorization': f"Bearer {access_token}"}

    user_response = requests.get(f"{DISCORD_API_URL}/users/@me", headers=user_headers)
    user_data = user_response.json()
    user_id = int(user_data['id'])

    if user_id in ALLOWED_USER_IDS:
        session['user_id'] = user_data['id']
        session['username'] = user_data['username']
        session['avatar'] = user_data.get('avatar')
        return redirect('/')

    member_response = requests.get(
        f"{DISCORD_API_URL}/users/@me/guilds/{GUILD_ID}/member", 
        headers=user_headers
    )

    if member_response.status_code == 200:
        member_data = member_response.json()
        user_roles = [int(r) for r in member_data.get('roles', [])]
        
        if any(role_id in user_roles for role_id in ALLOWED_ROLE_IDS):
            session['user_id'] = user_data['id']
            session['username'] = user_data['username']
            session['avatar'] = user_data.get('avatar')
            return redirect('/')

    return """
    <div style="font-family: sans-serif; background: #0f172a; color: #ef4444; height: 100vh; display: flex; align-items: center; justify-content: center; text-align: center;">
        <div>
            <h1>🚫 Access Denied</h1>
            <p style="color: #94a3b8;">You do not have permission to access this dashboard.</p>
            <a href="/logout" style="color: #38bdf8;">Return to Login</a>
        </div>
    </div>
    """, 403

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# --- API ROUTES ---
@app.route('/api/stats')
def stats():
    now = time.time()
    return jsonify({
        "status": "online",
        "ticket_counter": ticket_counter,
        "active_cooldowns": sum(1 for expire in user_cooldowns.values() if now < expire)
    })

@app.route('/api/active_tickets')
def get_active_tickets():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return jsonify({"tickets": [], "guild_id": str(GUILD_ID)})

    ticket_prefixes = [
        "ticket-", "fallen-", "hidden-", "frost-", "event-", "pizza-", 
        "lost-", "badlands-", "quickdraw-", "polluted-", "trials-", "hardcore-", "other-"
    ]

    active_tickets = []
    for ch in guild.text_channels:
        if any(ch.name.startswith(p) for p in ticket_prefixes):
            active_tickets.append({
                "id": str(ch.id),
                "name": ch.name
            })

    return jsonify({
        "tickets": active_tickets,
        "guild_id": str(GUILD_ID)
    })

@app.route('/api/transcripts')
def get_transcripts():
    saved_transcripts = []
    if os.path.exists(TRANSCRIPT_DIR):
        for f in sorted(os.listdir(TRANSCRIPT_DIR), reverse=True):
            if f.startswith("transcript-") and (f.endswith(".html") or f.endswith(".txt")):
                channel_name = f.replace("transcript-", "").replace(".html", "").replace(".txt", "")
                if not any(t["channel_name"] == channel_name for t in saved_transcripts):
                    saved_transcripts.append({"channel_name": channel_name, "filename": f})
    return jsonify(saved_transcripts)

@app.route('/api/cooldowns')
def get_cooldowns():
    now = time.time()
    active_users = []
    for uid, expire in list(user_cooldowns.items()):
        if now < expire:
            active_users.append({
                "user_id": str(uid),
                "expires_in_seconds": int(expire - now)
            })
    return jsonify(active_users)

@app.route('/api/blacklisted')
def get_blacklisted():
    blacklisted_members = []
    for guild in bot.guilds:
        role = guild.get_role(BLACKLIST_ROLE_ID)
        if role:
            for member in role.members:
                blacklisted_members.append({
                    "id": str(member.id),
                    "name": str(member)
                })
    return jsonify(blacklisted_members)

@app.route('/api/remove_cooldown', methods=['POST'])
def remove_cooldown_api():
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
        
    data = request.get_json() or {}
    target_user_id = int(data.get('user_id', 0))
    
    if target_user_id in user_cooldowns:
        del user_cooldowns[target_user_id]
        return jsonify({"success": True, "message": f"Cooldown removed for User ID: {target_user_id}"})
    
    return jsonify({"success": False, "message": "User is not currently on cooldown"})

@app.route('/api/remove_blacklist', methods=['POST'])
def remove_blacklist_api():
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
        
    data = request.get_json() or {}
    target_user_id = int(data.get('user_id', 0))
    
    async def unblacklist():
        for guild in bot.guilds:
            role = guild.get_role(BLACKLIST_ROLE_ID)
            member = guild.get_member(target_user_id)
            if role and member and role in member.roles:
                await member.remove_roles(role)
                return True
        return False

    future = asyncio.run_coroutine_threadsafe(unblacklist(), bot.loop)
    try:
        success = future.result(timeout=5)
        if success:
            return jsonify({"success": True, "message": "User un-blacklisted successfully"})
        return jsonify({"success": False, "message": "User or role not found in connected server"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

keep_alive()

# --- HELPER FUNCTIONS ---
def get_log_channel(guild):
    log_channel_id = os.getenv("LOG_CHANNEL_ID")
    if log_channel_id and log_channel_id.isdigit():
        return guild.get_channel(int(log_channel_id))
    return None

def get_blacklist_role(guild):
    return guild.get_role(BLACKLIST_ROLE_ID)

# --- DISCORD-STYLED HTML TRANSCRIPT GENERATOR ---
async def generate_transcript(channel: discord.TextChannel) -> discord.File:
    messages_html = ""
    
    async for msg in channel.history(limit=None, oldest_first=True):
        timestamp = msg.created_at.strftime("%b %d, %Y, %I:%M %p")
        author_name = html.escape(str(msg.author))
        avatar_url = msg.author.display_avatar.url
        bot_badge = '<span class="bot-badge">APP</span>' if msg.author.bot else ''

        text_content = html.escape(msg.clean_content) if msg.clean_content else ''
        
        embeds_html = ""
        for embed in msg.embeds:
            fields_html = ""
            for field in embed.fields:
                val_formatted = html.escape(field.value).replace('```', '')
                fields_html += f"""
                <div class="embed-field">
                    <div class="embed-field-name">{html.escape(field.name)}</div>
                    <div class="embed-field-value">{val_formatted}</div>
                </div>
                """
            
            embed_title = f'<div class="embed-title">{html.escape(embed.title)}</div>' if embed.title else ''
            embed_desc = f'<div class="embed-description">{html.escape(embed.description)}</div>' if embed.description else ''
            
            embeds_html += f"""
            <div class="embed">
                {embed_title}
                {embed_desc}
                <div class="embed-fields">{fields_html}</div>
            </div>
            """

        attachments_html = ""
        for att in msg.attachments:
            attachments_html += f'<div class="attachment"><a href="{att.url}" target="_blank">📎 {html.escape(att.filename)}</a></div>'

        messages_html += f"""
        <div class="message">
            <img class="avatar" src="{avatar_url}" alt="Avatar">
            <div class="message-body">
                <div class="header">
                    <span class="author">{author_name}</span>
                    {bot_badge}
                    <span class="timestamp">{timestamp}</span>
                </div>
                {f'<div class="content">{text_content}</div>' if text_content else ''}
                {embeds_html}
                {attachments_html}
            </div>
        </div>
        """

    full_html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Transcript #{channel.name}</title>
        <style>
            body {{ background-color: #313338; color: #dbdee1; font-family: 'gg sans', 'Noto Sans', 'Helvetica Neue', Helvetica, Arial, sans-serif; margin: 0; padding: 20px; }}
            .container {{ max-width: 1000px; margin: 0 auto; background-color: #2b2d31; border-radius: 8px; padding: 20px; box-shadow: 0 4px 15px rgba(0,0,0,0.4); }}
            .channel-header {{ font-size: 1.4rem; font-weight: bold; color: #f2f3f5; border-bottom: 1px solid #3f4147; padding-bottom: 15px; margin-bottom: 20px; display: flex; align-items: center; justify-content: space-between; }}
            .message {{ display: flex; margin-bottom: 18px; }}
            .avatar {{ width: 40px; height: 40px; border-radius: 50%; margin-right: 16px; flex-shrink: 0; }}
            .message-body {{ flex-grow: 1; }}
            .header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }}
            .author {{ color: #f2f3f5; font-weight: 600; font-size: 1rem; }}
            .bot-badge {{ background-color: #5865f2; color: white; font-size: 0.65rem; font-weight: bold; padding: 1px 4px; border-radius: 3px; line-height: 1.2; }}
            .timestamp {{ color: #949ba4; font-size: 0.75rem; margin-left: 4px; }}
            .content {{ color: #dbdee1; font-size: 0.95rem; line-height: 1.375; word-wrap: break-word; white-space: pre-wrap; }}
            
            .embed {{ background-color: #2b2d31; border-left: 4px solid #1e1f22; border-radius: 4px; padding: 12px 16px; margin-top: 8px; max-width: 520px; background: #1e1f22; }}
            .embed-title {{ color: #f2f3f5; font-weight: 700; margin-bottom: 6px; font-size: 0.95rem; }}
            .embed-description {{ color: #dbdee1; font-size: 0.9rem; margin-bottom: 10px; line-height: 1.3; }}
            .embed-fields {{ display: flex; flex-direction: column; gap: 8px; }}
            .embed-field-name {{ color: #f2f3f5; font-weight: 600; font-size: 0.85rem; }}
            .embed-field-value {{ background-color: #2b2d31; color: #dbdee1; padding: 6px 10px; border-radius: 4px; font-family: monospace; font-size: 0.85rem; margin-top: 2px; white-space: pre-wrap; }}
            .attachment {{ margin-top: 6px; font-size: 0.85rem; }}
            .attachment a {{ color: #00a8fc; text-decoration: none; }}
            .attachment a:hover {{ text-decoration: underline; }}
            .back-btn {{ background-color: #5865f2; color: white; border: none; padding: 6px 12px; border-radius: 4px; text-decoration: none; font-size: 0.85rem; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="channel-header">
                <span>💬 #{html.escape(channel.name)}</span>
                <a href="/" class="back-btn">⬅️ Dashboard</a>
            </div>
            {messages_html}
        </div>
    </body>
    </html>
    """

    # Save HTML transcript locally
    html_filepath = os.path.join(TRANSCRIPT_DIR, f"transcript-{channel.name}.html")
    txt_filepath = os.path.join(TRANSCRIPT_DIR, f"transcript-{channel.name}.txt")

    try:
        with open(html_filepath, "w", encoding="utf-8") as f:
            f.write(full_html)
    except Exception as e:
        print(f"Error saving HTML transcript: {e}")

    # Plain text format for Discord file attachment
    txt_content = f"Transcript for #{channel.name}\n"
    async for msg in channel.history(limit=None, oldest_first=True):
        txt_content += f"[{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {msg.author}: {msg.clean_content}\n"
    
    with open(txt_filepath, "w", encoding="utf-8") as f:
        f.write(txt_content)

    buffer = io.BytesIO(full_html.encode('utf-8'))
    return discord.File(fp=buffer, filename=f"transcript-{channel.name}.html")

# --- CLOSE CONFIRMATION VIEW ---
class CloseConfirmView(View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="✅ Yes, Close", style=discord.ButtonStyle.danger, custom_id="confirm_close_btn")
    async def confirm_close(self, interaction: discord.Interaction, button: Button):
        if not can_user_close_ticket(interaction.user, interaction.channel):
            return await interaction.response.send_message(
                "❌ You cannot close this ticket! Only the ticket owner or staff members can close it.",
                ephemeral=True
            )

        await interaction.response.send_message("🔒 Ticket confirmed for closure. Generating transcript and deleting in 5 seconds...")

        user_cooldowns[interaction.user.id] = time.time() + 28800

        transcript_file = await generate_transcript(interaction.channel)

        log_channel = get_log_channel(interaction.guild)
        if log_channel:
            log_embed = discord.Embed(title="🔒 Ticket Closed", color=discord.Color.orange())
            log_embed.add_field(name="Closed By", value=f"{interaction.user.mention} (ID: {interaction.user.id})", inline=False)
            log_embed.add_field(name="Ticket Channel", value=interaction.channel.name, inline=False)
            await log_channel.send(embed=log_embed, file=transcript_file)

        await asyncio.sleep(5)
        await interaction.channel.delete()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary, custom_id="cancel_close_btn")
    async def cancel_close(self, interaction: discord.Interaction, button: Button):
        if not can_user_close_ticket(interaction.user, interaction.channel):
            return await interaction.response.send_message(
                "❌ You cannot cancel this close request as you do not own this ticket.",
                ephemeral=True
            )
        await interaction.message.delete()
        await interaction.response.send_message("Ticket closure canceled.", ephemeral=True)

# --- BUTTON INSIDE OPENED TICKET ---
class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.secondary, custom_id="close_ticket_btn")
    async def close_ticket_button(self, interaction: discord.Interaction, button: Button):
        if not can_user_close_ticket(interaction.user, interaction.channel):
            return await interaction.response.send_message(
                "❌ You cannot close this ticket! Only the ticket owner or staff members can close it.",
                ephemeral=True
            )

        embed = discord.Embed(
            title="Are you sure?",
            description="Are you sure you want to close this ticket? This action cannot be undone.",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, view=CloseConfirmView(), ephemeral=False)

# --- QUESTION POPUP MODAL ---
class CarryQuestionsModal(Modal, title="Carry Request Questions"):
    q1_timezone = TextInput(
        label="1. Which country and timezone are you from?",
        style=discord.TextStyle.short,
        placeholder="e.g. USA, EST",
        required=True,
        max_length=100
    )
    
    q2_roblox = TextInput(
        label="2. What is your roblox display name?",
        style=discord.TextStyle.short,
        placeholder="e.g. Player123",
        required=True,
        max_length=100
    )
    
    q3_private_server = TextInput(
        label="3. Are you able to join a private server?",
        style=discord.TextStyle.short,
        placeholder="e.g. Yes / No",
        required=True,
        max_length=100
    )

    def __init__(self, category_val: str):
        super().__init__()
        self.category_val = category_val

    async def on_submit(self, interaction: discord.Interaction):
        global ticket_counter
        guild = interaction.guild
        user = interaction.user

        ticket_counter += 1
        save_data(DATA_FILE, {"ticket_counter": ticket_counter})

        formatted_channel_name = f"{self.category_val}-{ticket_counter:04d}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        ticket_channel = await guild.create_text_channel(
            name=formatted_channel_name,
            overwrites=overwrites,
            topic=f"Ticket Owner: {user.name} | OwnerID:{user.id}",
            reason=f"Carry Ticket #{ticket_counter} opened by {user.name}"
        )

        await interaction.response.send_message(f"Ticket created! Check out {ticket_channel.mention}", ephemeral=True)

        welcome_embed = discord.Embed(
            title="Ticket Created",
            description=f"Welcome {user.mention}! Thank you for utilizing this carry service ticket. A Carry Team member will assist you shortly.",
            color=discord.Color.blue()
        )
        answers_embed = discord.Embed(color=discord.Color.dark_grey())
        answers_embed.add_field(name="1. ⏰ Which country and timezone are you from?", value=f"```{self.q1_timezone.value}```", inline=False)
        answers_embed.add_field(name="2. 🎮 What is your roblox display name?", value=f"```{self.q2_roblox.value}```", inline=False)
        answers_embed.add_field(name="3. 🎲 Are you able to join a private server?", value=f"```{self.q3_private_server.value}```", inline=False)
        answers_embed.set_footer(text="Ticket Bot | Carry System")

        await ticket_channel.send(content=f"{user.mention}", embeds=[welcome_embed, answers_embed], view=TicketControlView())

        log_channel = get_log_channel(guild)
        if log_channel:
            log_embed = discord.Embed(title="📝 New Carry Ticket Opened", color=discord.Color.blue())
            log_embed.add_field(name="Ticket Number", value=f"#{ticket_counter:04d}", inline=True)
            log_embed.add_field(name="Category", value=self.category_val.replace('-', ' ').title(), inline=True)
            log_embed.add_field(name="Opened By", value=f"{user.mention} (ID: {user.id})", inline=True)
            log_embed.add_field(name="Channel", value=f"{ticket_channel.mention}", inline=False)
            await log_channel.send(embed=log_embed)

# --- SELECT MENU ---
class CarryDropdown(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Fallen Carry", description="Request fallen mode carry", emoji="🛡️", value="fallen-carry"),
            discord.SelectOption(label="Hidden Wave Carry", description="Request hidden wave carry", emoji="🥷", value="hidden-wave-carry"),
            discord.SelectOption(label="Frost Carry", description="Request frost mode carry", emoji="❄️", value="frost-carry"),
            discord.SelectOption(label="Event Carry", description="Request current event carry", emoji="🎮", value="event-carry"),
            discord.SelectOption(label="Pizza Party Carry", description="Request pizza party carry", emoji="🍕", value="pizza-party-carry"),
            discord.SelectOption(label="Lost Soul Carry", description="Request lost soul carry", emoji="👻", value="lost-soul-carry"),
            discord.SelectOption(label="Badlands 2 Carry", description="Request badlands 2 carry", emoji="🤠", value="badlands-2-carry"),
            discord.SelectOption(label="Quickdraw Carry", description="Request quickdraw carry", emoji="🔫", value="quickdraw-carry"),
            discord.SelectOption(label="Polluted Wasteland 2 Carry", description="Request polluted wasteland 2 carry", emoji="☢️", value="polluted-wasteland-2-carry"),
            discord.SelectOption(label="Trials", description="Request carry for trials", emoji="👾", value="trials"),
            discord.SelectOption(label="Hardcore Carry", description="Request hardcore mode carry", emoji="💎", value="hardcore-carry"),
            discord.SelectOption(label="Other", description="Request carry for something that not named above", emoji="❓", value="other"),
        ]
        super().__init__(placeholder="Select a category...", min_values=1, max_values=1, options=options, custom_id="carry_dropdown")

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        is_admin = user.guild_permissions.administrator

        blacklist_role = get_blacklist_role(guild)
        if blacklist_role and blacklist_role in user.roles and not is_admin:
            return await interaction.response.send_message("❌ You are blacklisted from opening carry tickets.", ephemeral=True)

        if user.id in user_cooldowns and not is_admin:
            expire_timestamp = int(user_cooldowns[user.id])
            if time.time() < expire_timestamp:
                return await interaction.response.send_message(
                    f"⏳ You are on cooldown! Please wait until <t:{expire_timestamp}:R> before opening another ticket.",
                    ephemeral=True
                )
            else:
                del user_cooldowns[user.id]

        if not is_admin:
            for channel in guild.text_channels:
                if channel.overwrites_for(user).read_messages is True and channel.id != interaction.channel_id:
                    if any(channel.name.startswith(prefix) for prefix in [
                        "ticket-", "fallen-", "hidden-", "frost-", "event-", "pizza-", 
                        "lost-", "badlands-", "quickdraw-", "polluted-", "trials-", "hardcore-", "other-"
                    ]):
                        return await interaction.response.send_message(f"You already have an open ticket: {channel.mention}", ephemeral=True)

        await interaction.response.send_modal(CarryQuestionsModal(category_val=self.values[0]))

class TicketLauncher(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CarryDropdown())

# --- BOT EVENTS ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    print(f"Loaded starting ticket count: {ticket_counter}")
    bot.add_view(TicketLauncher())
    bot.add_view(TicketControlView())
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(e)

# --- SLASH COMMANDS ---

@bot.tree.command(name="setup_tickets", description="Spawns the Requesting Carry ticket panel")
@app_commands.checks.has_permissions(administrator=True)
async def setup_tickets(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Requesting Carry",
        description="Choose what you need help with",
        color=discord.Color.blue()
    )
    
    embed.add_field(name="Normal Game Modes 🥶", value="You can request a carry service for any normal game mode.", inline=False)
    embed.add_field(name="Special Game Modes 🥺", value="You can request carry service for any special game mode, including Quickdraw and Lost Soul.", inline=False)
    embed.add_field(name="Trials 🐹", value="You can request carry service for any trials, however requesting quarantine or jailed may result in long wait.", inline=False)
    embed.add_field(name="Hardcore 💎", value="You can request carry service for hardcore your first hardcore run, but not for gem grinding.", inline=False)
    embed.add_field(name="Others 🐱", value="Other is used for game modes below Fallen and for mission quests.", inline=False)
    
    embed.set_footer(text="Ticket Bot | Carry System")

    await interaction.channel.send(embed=embed, view=TicketLauncher())
    await interaction.response.send_message("Ticket panel posted!", ephemeral=True)

@bot.tree.command(name="set_ticket_count", description="Manually set or reset the ticket counter number")
@app_commands.checks.has_permissions(administrator=True)
async def set_ticket_count(interaction: discord.Interaction, number: int):
    global ticket_counter
    ticket_counter = number
    save_data(DATA_FILE, {"ticket_counter": ticket_counter})
    await interaction.response.send_message(
        embed=discord.Embed(
            description=f"⚙️ Ticket counter updated to **#{ticket_counter:04d}**.",
            color=discord.Color.green()
        ),
        ephemeral=True
    )

@bot.tree.command(name="bypass_cooldown", description="Removes the ticket creation cooldown for a specific user")
@app_commands.checks.has_permissions(manage_channels=True)
async def bypass_cooldown(interaction: discord.Interaction, member: discord.Member):
    if member.id in user_cooldowns:
        del user_cooldowns[member.id]
        embed = discord.Embed(
            description=f"⚡ Cooldown removed for {member.mention}. They can open a new ticket immediately!",
            color=discord.Color.green()
        )
    else:
        embed = discord.Embed(
            description=f"ℹ️ {member.mention} is not currently on a ticket cooldown.",
            color=discord.Color.blue()
        )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="add_user", description="Grant a user permission to view this ticket channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def add_user(interaction: discord.Interaction, member: discord.Member):
    await interaction.channel.set_permissions(member, read_messages=True, send_messages=True)
    await interaction.response.send_message(embed=discord.Embed(description=f"✅ {member.mention} added.", color=discord.Color.green()))

@bot.tree.command(name="remove_user", description="Remove a user's permission to view this ticket channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def remove_user(interaction: discord.Interaction, member: discord.Member):
    await interaction.channel.set_permissions(member, overwrite=None)
    await interaction.response.send_message(embed=discord.Embed(description=f"🚫 {member.mention} removed.", color=discord.Color.red()))

@bot.tree.command(name="close", description="Close this ticket channel")
async def close_ticket(interaction: discord.Interaction):
    if not can_user_close_ticket(interaction.user, interaction.channel):
        return await interaction.response.send_message(
            "❌ You cannot close this ticket! Only the ticket owner or staff members can close it.",
            ephemeral=True
        )

    embed = discord.Embed(
        title="Are you sure?",
        description="Are you sure you want to close this ticket? This action cannot be undone.",
        color=discord.Color.gold()
    )
    await interaction.response.send_message(embed=embed, view=CloseConfirmView(), ephemeral=False)

@bot.tree.command(name="force_close", description="Force close a ticket with a reason")
@app_commands.checks.has_permissions(manage_channels=True)
async def force_close(interaction: discord.Interaction, reason: str):
    await interaction.response.send_message(f"⚠️ **Ticket force closed** by {interaction.user.mention}.\n**Reason:** {reason}\n*Generating transcript and deleting in 5 seconds...*")
    
    user_cooldowns[interaction.user.id] = time.time() + 28800

    transcript_file = await generate_transcript(interaction.channel)

    log_channel = get_log_channel(interaction.guild)
    if log_channel:
        log_embed = discord.Embed(title="⚠️ Ticket Force Closed", color=discord.Color.red())
        log_embed.add_field(name="Closed By", value=f"{interaction.user.mention} (ID: {interaction.user.id})", inline=False)
        log_embed.add_field(name="Ticket Name", value=interaction.channel.name, inline=False)
        log_embed.add_field(name="Reason", value=reason, inline=False)
        await log_channel.send(embed=log_embed, file=transcript_file)

    await asyncio.sleep(5)
    await interaction.channel.delete(reason=reason)

@bot.tree.command(name="edit_ticket", description="Edit a ticket channel's name or topic")
@app_commands.checks.has_permissions(manage_channels=True)
async def edit_ticket(
    interaction: discord.Interaction, 
    target: discord.TextChannel, 
    new_name: str = None, 
    new_topic: str = None
):
    if not new_name and not new_topic:
        return await interaction.response.send_message(
            "Please provide at least a new name or a new topic to update!", 
            ephemeral=True
        )

    kwargs = {}
    if new_name:
        kwargs['name'] = new_name

    if new_topic:
        kwargs['topic'] = new_topic

    await target.edit(**kwargs)
    await interaction.response.send_message(
        f"✅ Successfully updated {target.mention}!", 
        ephemeral=True
    )

@bot.tree.command(name="blacklist_add", description="Gives the Blacklisted role to a user")
@app_commands.checks.has_permissions(manage_roles=True)
async def blacklist_add(interaction: discord.Interaction, member: discord.Member):
    role = get_blacklist_role(interaction.guild)
    if not role:
        return await interaction.response.send_message("❌ Blacklist role not found in server.", ephemeral=True)

    if role in member.roles:
        await interaction.response.send_message(f"ℹ️ {member.mention} already has the {role.mention} role.", ephemeral=True)
    else:
        await member.add_roles(role)
        await interaction.response.send_message(embed=discord.Embed(description=f"🚫 {member.mention} has been given the {role.mention} role.", color=discord.Color.red()))

@bot.tree.command(name="blacklist_remove", description="Removes the Blacklisted role from a user")
@app_commands.checks.has_permissions(manage_roles=True)
async def blacklist_remove(interaction: discord.Interaction, member: discord.Member):
    role = get_blacklist_role(interaction.guild)
    if not role:
        return await interaction.response.send_message("❌ Blacklist role not found in server.", ephemeral=True)

    if role not in member.roles:
        await interaction.response.send_message(f"ℹ️ {member.mention} does not have the {role.mention} role.", ephemeral=True)
    else:
        await member.remove_roles(role)
        await interaction.response.send_message(embed=discord.Embed(description=f"✅ Removed {role.mention} role from {member.mention}.", color=discord.Color.green()))

TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("Error: DISCORD_TOKEN environment variable is not set.")

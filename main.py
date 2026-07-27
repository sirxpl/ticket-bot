import base64
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
    777341204047331348,    # Your Personal Discord User ID
    1012751329845841921,
    1475866314911256648    # Co-developer User ID
]

# --- CONFIGURATION ---
BLACKLIST_ROLE_ID = 1530330613029015704

# Access Control Config
GUILD_ID = 1530005676003426487         # Your Discord Server ID
ALLOWED_ROLE_IDS = [
    1530330612567904276               # Staff/Admin Role ID
]

# Valid ticket prefixes for channel safety checks
TICKET_PREFIXES = (
    "ticket-", "fallen-", "hidden-", "frost-", "event-", "pizza-", 
    "lost-", "badlands-", "quickdraw-", "polluted-", "trials-", "hardcore-", "other-"
)

# Base URL for Web Dashboard Links
DOMAIN_URL = os.getenv("DOMAIN_URL", "https://ticket-bot-f184.onrender.com")

# Discord OAuth2 Config
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = f"{DOMAIN_URL}/callback"
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
ticket_data = load_data(DATA_FILE, {"ticket_counter": 0})
ticket_counter = ticket_data.get("ticket_counter", 0)

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
        <p>Please authorize with Discord to access transcripts and controls.</p>
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
        session['redirect_after_login'] = f"/transcript/{channel_name}"
        return redirect('/login')

    filepath = os.path.join(TRANSCRIPT_DIR, f"transcript-{channel_name}.html")

    if not os.path.exists(filepath):
        return "<h2 style='color:#ef4444; font-family:sans-serif; text-align:center; margin-top:50px;'>Transcript file not found.</h2>", 404

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

    target_redirect = session.pop('redirect_after_login', '/')

    if user_id in ALLOWED_USER_IDS:
        session['user_id'] = user_data['id']
        session['username'] = user_data['username']
        session['avatar'] = user_data.get('avatar')
        return redirect(target_redirect)

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
            return redirect(target_redirect)

    return """
    <div style="font-family: sans-serif; background: #0f172a; color: #ef4444; height: 100vh; display: flex; align-items: center; justify-content: center; text-align: center;">
        <div>
            <h1>🚫 Access Denied</h1>
            <p style="color: #94a3b8;">You do not have permission or proper roles to view this transcript.</p>
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

    active_tickets = []
    for ch in guild.text_channels:
        if ch.name.startswith(TICKET_PREFIXES):
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
            if f.startswith("transcript-") and f.endswith(".html"):
                channel_name = f.replace("transcript-", "").replace(".html", "")
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

# --- VIRUSTOTAL LINK BUTTON COMPONENT ---
class VirusTotalLinkView(View):
    def __init__(self, vt_url: str):
        super().__init__(timeout=None)
        self.add_item(Button(
            label="Open Full VirusTotal Report", 
            url=vt_url, 
            style=discord.ButtonStyle.link, 
            emoji="🔗"
        ))

# --- VIEW TRANSCRIPT LINK BUTTON COMPONENT ---
class TranscriptButtonView(View):
    def __init__(self, transcript_url: str):
        super().__init__(timeout=None)
        self.add_item(Button(label="View Transcript", url=transcript_url, style=discord.ButtonStyle.link, emoji="📄"))

# --- TICKET MODAL & QUESTION CLASS ---
class CarryQuestionsModal(Modal, title="Ticket Details"):
    roblox_user = TextInput(label="Roblox Username", placeholder="e.g. Builderman", required=True, max_length=50)
    details = TextInput(label="Details / Notes", style=discord.TextStyle.paragraph, placeholder="Explain what you need assistance with...", required=False, max_length=1000)

    def __init__(self, category_name: str, prefix: str):
        super().__init__()
        self.category_name = category_name
        self.prefix = prefix

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        user = interaction.user
        guild = interaction.guild
        now = time.time()

        # Check blacklist
        bl_role = get_blacklist_role(guild)
        if bl_role and bl_role in user.roles:
            return await interaction.followup.send("❌ You are blacklisted from opening support tickets.", ephemeral=True)

        # Check user cooldown
        if user.id in user_cooldowns and now < user_cooldowns[user.id]:
            remaining = int(user_cooldowns[user.id] - now)
            return await interaction.followup.send(f"⏳ You are on cooldown! Please wait `{remaining}` seconds before opening another ticket.", ephemeral=True)

        global ticket_counter
        ticket_counter += 1
        save_data(DATA_FILE, {"ticket_counter": ticket_counter})

        channel_name = f"{self.prefix}-{ticket_counter:04d}"

        # Channel permissions setup
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        for role_id in ALLOWED_ROLE_IDS:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)

        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            topic=f"Category: {self.category_name} | OwnerID:{user.id}"
        )

        # Set user cooldown (5 minutes)
        user_cooldowns[user.id] = now + 300

        # Send initial embed inside ticket
        embed = discord.Embed(
            title=f"🎫 {self.category_name} Ticket",
            description=f"Welcome {user.mention}! Support staff will be with you shortly.\n\n"
                        f"**Roblox Username:** `{self.roblox_user.value}`\n"
                        f"**Details:** {self.details.value or 'None provided'}",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Ticket #{ticket_counter:04d} | ID: {user.id}")

        await ticket_channel.send(content=f"{user.mention}", embed=embed, view=TicketControlView())
        await interaction.followup.send(f"✅ Ticket created! Head over to {ticket_channel.mention}", ephemeral=True)

# --- TICKET CONTROL VIEW (INSIDE CHANNEL) ---
class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="btn_close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        if not can_user_close_ticket(interaction.user, interaction.channel):
            return await interaction.response.send_message("❌ You do not have permission to close this ticket.", ephemeral=True)

        embed = discord.Embed(
            title="🔒 Close Confirmation",
            description="Are you sure you want to close and save a transcript for this ticket?",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, view=CloseConfirmView(), ephemeral=True)

# --- CLOSE CONFIRMATION VIEW ---
class CloseConfirmView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Confirm Close", style=discord.ButtonStyle.danger, custom_id="btn_confirm_close")
    async def confirm_close(self, interaction: discord.Interaction, button: Button):
        channel = interaction.channel
        guild = interaction.guild

        await interaction.response.send_message("⏳ Generating transcript and closing ticket...", ephemeral=True)

        # HTML Transcript Generation
        messages = []
        async for msg in channel.history(limit=500, oldest_first=True):
            messages.append(msg)

        transcript_filename = f"transcript-{channel.name}.html"
        file_path = os.path.join(TRANSCRIPT_DIR, transcript_filename)

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Transcript - #{channel.name}</title>
            <style>
                body {{ font-family: Arial, sans-serif; background-color: #36393f; color: #dcddde; padding: 20px; }}
                .msg {{ margin-bottom: 15px; border-bottom: 1px solid #4f545c; padding-bottom: 10px; }}
                .author {{ font-weight: bold; color: #7289da; }}
                .time {{ font-size: 0.8em; color: #72767d; margin-left: 10px; }}
                .content {{ margin-top: 5px; white-space: pre-wrap; }}
            </style>
        </head>
        <body>
            <h2>Ticket Transcript: #{html.escape(channel.name)}</h2>
            <hr>
        """

        for m in messages:
            timestamp = m.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')
            content = html.escape(m.content)
            html_content += f"""
            <div class="msg">
                <span class="author">{html.escape(str(m.author))}</span>
                <span class="time">{timestamp}</span>
                <div class="content">{content}</div>
            </div>
            """

        html_content += "</body></html>"

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        # Send transcript log to log channel if set
        log_ch = get_log_channel(guild)
        if log_ch:
            transcript_url = f"{DOMAIN_URL}/transcript/{channel.name}"
            log_embed = discord.Embed(
                title="📄 Ticket Closed",
                description=f"Ticket **#{channel.name}** was closed by {interaction.user.mention}.",
                color=discord.Color.red()
            )
            await log_ch.send(embed=log_embed, view=TranscriptButtonView(transcript_url))

        await asyncio.sleep(2)
        await channel.delete()

# --- DROPDOWN CATEGORY SELECTOR ---
class CarrySelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Fallen Carry", value="Fallen|fallen", emoji="⚔️", description="Request help with Fallen trials"),
            discord.SelectOption(label="Frost Carry", value="Frost|frost", emoji="❄️", description="Request help with Frost trials"),
            discord.SelectOption(label="Lost Carry", value="Lost|lost", emoji="🌀", description="Request help with Lost trials"),
            discord.SelectOption(label="Hardcore Carry", value="Hardcore|hardcore", emoji="💀", description="Request help with Hardcore mode"),
            discord.SelectOption(label="Other Support", value="Other Support|other", emoji="❓", description="General questions or other assistance")
        ]
        super().__init__(placeholder="Choose a category to open a ticket...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        category_name, prefix = self.values[0].split("|")
        modal = CarryQuestionsModal(category_name, prefix)
        await interaction.response.send_modal(modal)

class TicketDropdownView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CarrySelect())

# --- SLASH COMMANDS ---

# 1. VirusTotal URL Scanner
@bot.tree.command(name="scan_url", description="Check a URL or domain against VirusTotal for security threats")
@app_commands.describe(url="The web link or domain you want to check")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def scan_url(interaction: discord.Interaction, url: str):
    vt_key = os.getenv("VIRUSTOTAL_API_KEY")
    if not vt_key:
        return await interaction.response.send_message("❌ VirusTotal API key is missing from environment variables.", ephemeral=True)

    await interaction.response.defer(thinking=True)

    try:
        url_id = base64.urlsafe_b64encode(url.encode()).decode().strip("=")
        headers = {"x-apikey": vt_key}
        response = requests.get(f"https://www.virustotal.com/api/v3/urls/{url_id}", headers=headers)

        if response.status_code == 200:
            stats = response.json()['data']['attributes']['last_analysis_stats']
            malicious = stats.get('malicious', 0)
            suspicious = stats.get('suspicious', 0)
            harmless = stats.get('harmless', 0)

            color = discord.Color.red() if malicious > 0 else (discord.Color.gold() if suspicious > 0 else discord.Color.green())
            
            embed = discord.Embed(title="🛡️ VirusTotal Scan Results", color=color)
            embed.add_field(name="URL", value=f"`{url}`", inline=False)
            embed.add_field(name="🚨 Malicious", value=str(malicious), inline=True)
            embed.add_field(name="⚠️ Suspicious", value=str(suspicious), inline=True)
            embed.add_field(name="✅ Clean / Harmless", value=str(harmless), inline=True)

            vt_web_link = f"https://www.virustotal.com/gui/url/{url_id}"
            await interaction.followup.send(embed=embed, view=VirusTotalLinkView(vt_web_link))
        else:
            await interaction.followup.send(f"⚠️ Link not found in VirusTotal database or scan failed (Code {response.status_code}).")
    except Exception as e:
        await interaction.followup.send(f"❌ An error occurred during the scan: {e}")

# 2. Setup Ticket Panel
@bot.tree.command(name="setup_tickets", description="Send the interactive ticket menu to this channel")
@app_commands.checks.has_permissions(administrator=True)
async def setup_tickets(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎫 Carry & Support Ticket Hub",
        description="Select a category from the dropdown menu below to create a support channel!",
        color=discord.Color.blue()
    )
    await interaction.channel.send(embed=embed, view=TicketDropdownView())
    await interaction.response.send_message("✅ Ticket panel successfully deployed!", ephemeral=True)

# 3. Close Current Ticket
@bot.tree.command(name="close", description="Close the current ticket channel")
async def close_ticket_cmd(interaction: discord.Interaction):
    if not interaction.channel.name.startswith(TICKET_PREFIXES):
        return await interaction.response.send_message("❌ This command can only be used inside a ticket channel!", ephemeral=True)

    if not can_user_close_ticket(interaction.user, interaction.channel):
        return await interaction.response.send_message("❌ You do not have permission to close this ticket.", ephemeral=True)

    embed = discord.Embed(
        title="🔒 Confirm Closure",
        description="Are you sure you want to close and save a transcript for this ticket?",
        color=discord.Color.gold()
    )
    await interaction.response.send_message(embed=embed, view=CloseConfirmView(), ephemeral=True)

# 4. Blacklist User
@bot.tree.command(name="blacklist", description="Blacklist a user from creating tickets")
@app_commands.describe(user="The member to blacklist")
@app_commands.checks.has_permissions(administrator=True)
async def blacklist_user(interaction: discord.Interaction, user: discord.Member):
    role = get_blacklist_role(interaction.guild)
    if not role:
        return await interaction.response.send_message("❌ Blacklist role not found. Verify `BLACKLIST_ROLE_ID` in your script.", ephemeral=True)

    if role in user.roles:
        return await interaction.response.send_message(f"⚠️ {user.mention} is already blacklisted.", ephemeral=True)

    await user.add_roles(role)
    await interaction.response.send_message(f"🚫 {user.mention} has been added to the ticket blacklist.")

# 5. Un-blacklist User
@bot.tree.command(name="unblacklist", description="Remove a user from the ticket blacklist")
@app_commands.describe(user="The member to unblacklist")
@app_commands.checks.has_permissions(administrator=True)
async def unblacklist_user(interaction: discord.Interaction, user: discord.Member):
    role = get_blacklist_role(interaction.guild)
    if not role:
        return await interaction.response.send_message("❌ Blacklist role not found. Verify `BLACKLIST_ROLE_ID` in your script.", ephemeral=True)

    if role not in user.roles:
        return await interaction.response.send_message(f"⚠️ {user.mention} is not currently blacklisted.", ephemeral=True)

    await user.remove_roles(role)
    await interaction.response.send_message(f"✅ {user.mention} has been removed from the blacklist.")

# --- GLOBAL SLASH COMMAND ERROR HANDLER ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        embed = discord.Embed(
            title="🚫 Access Denied",
            description="You do not have permission to use this command!",
            color=discord.Color.red()
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        raise error

# --- BOT EVENT LISTENERS ---
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"📡 Synced {len(synced)} Slash Commands globally!")
    except Exception as e:
        print(f"❌ Failed to sync slash commands: {e}")

# --- START BOT ---
if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("❌ Error: DISCORD_BOT_TOKEN environment variable is not set.")
    else:
        bot.run(token)

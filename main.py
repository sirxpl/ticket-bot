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

# --- VIRUSTOTAL SCAN SLASH COMMAND ---
@bot.tree.command(name="scan_url", description="Check a URL or domain against VirusTotal for security threats")
@app_commands.describe(url="The web link or domain you want to check")
@app_commands.allowed_installs(guilds=True, users=True)  # Allows both Server & User installations
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)  # Allows use in Servers, DMs, & Group DMs
async def scan_url(interaction: discord.Interaction, url: str):
    vt_key = os.getenv("e5c88c020e603eb1eba33c58e2269004104f8f88d152f0d5c0d8a18860afba35")
    if not vt_key:
        return await interaction.response.send_message(
            "❌ **Error:** VirusTotal API key is not configured in environment variables.", 
            ephemeral=True
        )

    await interaction.response.defer(ephemeral=False)

    try:
        url_id = base64.urlsafe_b64encode(url.encode()).decode().strip("=")
        headers = {"accept": "application/json", "x-apikey": vt_key}
        
        response = requests.get(f"https://www.virustotal.com/api/v3/urls/{url_id}", headers=headers)
        
        if response.status_code == 404:
            requests.post("https://www.virustotal.com/api/v3/urls", headers=headers, data={"url": url})
            
            pending_embed = discord.Embed(
                title="🔍 Scan Submitted to VirusTotal",
                description=f"This URL wasn't found in VirusTotal's cache.\nIt has now been submitted for live analysis.\n\n**Target:** `{url}`",
                color=discord.Color.blue()
            )
            pending_embed.set_footer(text="Please run the command again in 1-2 minutes for full results.")
            return await interaction.followup.send(embed=pending_embed)

        elif response.status_code != 200:
            return await interaction.followup.send(
                embed=discord.Embed(
                    description=f"❌ **VirusTotal API Error:** Received status code `{response.status_code}`.",
                    color=discord.Color.red()
                )
            )

        attributes = response.json().get("data", {}).get("attributes", {})
        stats = attributes.get("last_analysis_stats", {})
        
        malicious = stats.get("malicious", 0)
        suspicious = stats.get("suspicious", 0)
        harmless = stats.get("harmless", 0)
        undetected = stats.get("undetected", 0)
        total_engines = malicious + suspicious + harmless + undetected

        if malicious > 0:
            color = discord.Color.red()
            verdict = "🚨 **FLAGGED AS MALICIOUS / PHISHING**"
        elif suspicious > 0:
            color = discord.Color.gold()
            verdict = "⚠️ **SUSPICIOUS LINK**"
        else:
            color = discord.Color.green()
            verdict = "✅ **CLEAN / SAFE**"

        vt_gui_link = f"https://www.virustotal.com/gui/url/{url_id}"

        embed = discord.Embed(title="🛡️ VirusTotal URL Scan Result", color=color)
        embed.add_field(name="🌐 Scanned Target", value=f"`{url}`", inline=False)
        embed.add_field(name="⚖️ Verdict", value=verdict, inline=False)
        embed.add_field(name="🔴 Malicious", value=f"**{malicious}** vendors", inline=True)
        embed.add_field(name="🟡 Suspicious", value=f"**{suspicious}** vendors", inline=True)
        embed.add_field(name="🟢 Clean / Safe", value=f"**{harmless + undetected}** / **{total_engines}**", inline=True)
        embed.set_footer(text="VirusTotal Threat Intelligence • Security Scan")

        await interaction.followup.send(embed=embed, view=VirusTotalLinkView(vt_gui_link))

    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(description=f"❌ **Error:** `{str(e)}`", color=discord.Color.red()))

# --- HELPER FUNCTIONS ---
def get_log_channel(guild):
    log_channel_id = os.getenv("LOG_CHANNEL_ID")
    if log_channel_id and log_channel_id.isdigit():
        return guild.get_channel(int(log_channel_id))
    return None

def get_blacklist_role(guild):
    return guild.get_role(BLACKLIST_ROLE_ID)

# --- VIEW TRANSCRIPT LINK BUTTON COMPONENT ---
class TranscriptButtonView(View):
    def __init__(self, transcript_url: str):
        super().__init__(timeout=None)
        self.add_item(Button(label="View Transcript", url=transcript_url, style=discord.ButtonStyle.link, emoji="📄"))

# --- DISCORD-STYLED HTML TRANSCRIPT GENERATOR ---
async def save_html_transcript(channel: discord.TextChannel) -> str:
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

    html_filepath = os.path.join(TRANSCRIPT_DIR, f"transcript-{channel.name}.html")

    try:
        with open(html_filepath, "w", encoding="utf-8") as f:
            f.write(full_html)
    except Exception as e:
        print(f"Error saving HTML transcript: {e}")

    return f"{DOMAIN_URL}/transcript/{channel.name}"

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

        transcript_url = await save_html_transcript(interaction.channel)

        log_channel = get_log_channel(interaction.guild)
        if log_channel:
            log_embed = discord.Embed(title="🔒 Ticket Closed", color=discord.Color.orange())
            log_embed.add_field(name="Closed By", value=f"{interaction.user.mention} (ID: {interaction.user.id})", inline=False)
            log_embed.add_field(name="Ticket Channel", value=interaction.channel.name, inline=False)
            await log_channel.send(embed=log_embed, view=TranscriptButtonView(transcript_url))

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
    def __init__(self, category_name: str):
        super().__init__()
        self.category_name = category_name

    q1_timezone = TextInput(
        label="1. Which country and timezone are you from?",
        style=discord.TextStyle.short,
        placeholder="e.g. USA, EST",
        required=True,
        max_length=100
    )
    
    q2_roblox = TextInput(
        label="2. What is your Roblox username?",
        style=discord.TextStyle.short,
        placeholder="e.g. RobloxUser123",
        required=True,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        global ticket_counter
        guild = interaction.guild

        # Check blacklist role
        blacklist_role = get_blacklist_role(guild)
        if blacklist_role and blacklist_role in interaction.user.roles:
            return await interaction.response.send_message("❌ You are blacklisted from opening tickets.", ephemeral=True)

        # Check user cooldown
        now = time.time()
        if interaction.user.id in user_cooldowns and now < user_cooldowns[interaction.user.id]:
            rem = int(user_cooldowns[interaction.user.id] - now)
            return await interaction.response.send_message(f"⏳ Please wait {rem}s before opening another ticket.", ephemeral=True)

        ticket_counter += 1
        save_data(DATA_FILE, {"ticket_counter": ticket_counter})

        prefix = self.category_name.lower().replace(" ", "-")
        ch_name = f"{prefix}-{ticket_counter:04d}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        for rid in ALLOWED_ROLE_IDS:
            r = guild.get_role(rid)
            if r:
                overwrites[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel = await guild.create_text_channel(
            name=ch_name,
            overwrites=overwrites,
            topic=f"OwnerID:{interaction.user.id} | Category: {self.category_name}"
        )

        embed = discord.Embed(
            title=f"🎫 Ticket Opened - {self.category_name}",
            description=f"Welcome {interaction.user.mention}! A staff member will be with you shortly.",
            color=discord.Color.blue()
        )
        embed.add_field(name="🌍 Location / Timezone", value=self.q1_timezone.value, inline=False)
        embed.add_field(name="🎮 Roblox Username", value=self.q2_roblox.value, inline=False)

        await channel.send(content=f"{interaction.user.mention}", embed=embed, view=TicketControlView())
        await interaction.response.send_message(f"✅ Ticket created! Head over to {channel.mention}", ephemeral=True)

# --- BOT EVENTS & STARTUP ---
@bot.event
async def on_ready():
    print(f"✅ Bot logged in as {bot.user} (ID: {bot.user.id})")
    
    # Sync slash commands globally
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"❌ Error syncing slash commands: {e}")

# Run Bot
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ Error: DISCORD_BOT_TOKEN environment variable is not set.")

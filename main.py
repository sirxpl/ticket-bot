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

# ==============================================================================
# ⚙️ CONFIGURATION & IDS (EDIT THESE FOR YOUR SERVER)
# ==============================================================================

# Allowed Individual Users (Bot Owners, Developers, etc.)
ALLOWED_USER_IDS = [
    777341204047331348,    # Your Personal Discord User ID
    1012751329845841921,   # Co-developer ID 1
    1475866314911256648    # Co-developer ID 2
]

GUILD_ID = 1530005676003426487         # Your Discord Server ID
BLACKLIST_ROLE_ID = 1530330613029015704 # Blacklist Role ID

ALLOWED_ROLE_IDS = [
    1530330612567904276               # Staff / Admin Role ID
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

# ==============================================================================
# 🤖 BOT & PERSISTENCE SETUP
# ==============================================================================

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

user_cooldowns = {}  # {user_id: timestamp_when_cooldown_expires}

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
            print(f"Error creating file {file_path}: {e}")
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
        print(f"Error saving {file_path}: {e}")

ticket_data = load_data(DATA_FILE, {"ticket_counter": 0})
ticket_counter = ticket_data.get("ticket_counter", 0)

def can_user_close_ticket(user: discord.Member, channel: discord.TextChannel) -> bool:
    if user.guild_permissions.administrator or any(role.id in ALLOWED_ROLE_IDS for role in user.roles):
        return True
    if channel.topic and f"OwnerID:{user.id}" in channel.topic:
        return True
    return False

# ==============================================================================
# 🌐 FLASK WEB DASHBOARD & OAUTH2
# ==============================================================================

app = Flask('')
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24))

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Dashboard Login</title>
    <style>
        body { font-family: sans-serif; background-color: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .login-card { background-color: #1e293b; padding: 40px; border-radius: 12px; text-align: center; }
        .btn { background-color: #5865F2; color: white; border: none; padding: 12px 24px; border-radius: 8px; font-weight: bold; cursor: pointer; text-decoration: none; display: inline-block; margin-top: 20px; }
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
    <title>Ticket Bot Dashboard</title>
    <style>
        body { font-family: sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 40px; }
        .container { max-width: 900px; margin: 0 auto; }
        .header { display: flex; align-items: center; justify-content: space-between; border-bottom: 2px solid #1e293b; padding-bottom: 20px; margin-bottom: 30px; }
        .user-info { display: flex; align-items: center; gap: 12px; }
        .avatar { width: 40px; height: 40px; border-radius: 50%; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .card { background-color: #1e293b; border-radius: 12px; padding: 24px; }
        .card .value { font-size: 2.2rem; font-weight: bold; color: #38bdf8; }
        .section { background-color: #1e293b; border-radius: 12px; padding: 24px; margin-bottom: 20px; }
        ul { list-style-type: none; padding: 0; }
        li { background: #0f172a; padding: 12px; border-radius: 6px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
        .btn-view { background: #38bdf8; color: #0f172a; text-decoration: none; padding: 6px 12px; border-radius: 6px; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎫 Carry Bot Dashboard</h1>
            <div class="user-info">
                <img src="{{ user_avatar }}" class="avatar">
                <span>Welcome, <strong>{{ username }}</strong>!</span>
                <a href="/logout" style="color: #ef4444; margin-left: 10px;">Logout</a>
            </div>
        </div>
        <div class="grid">
            <div class="card">
                <h3>Total Tickets</h3>
                <div class="value">#{{ ticket_count }}</div>
            </div>
            <div class="card">
                <h3>Active Cooldowns</h3>
                <div class="value">{{ active_cooldowns }}</div>
            </div>
        </div>
        <div class="section">
            <h3>📁 Message Transcripts</h3>
            <ul id="transcript-list">Loading...</ul>
        </div>
    </div>
    <script>
        async function loadTranscripts() {
            const res = await fetch('/api/transcripts');
            const data = await res.json();
            const list = document.getElementById('transcript-list');
            list.innerHTML = data.length ? '' : '<li>No saved transcripts found.</li>';
            data.forEach(item => {
                list.innerHTML += `
                    <li>
                        <span>📄 Ticket: <strong>#${item.channel_name}</strong></span>
                        <a href="/transcript/${item.channel_name}" target="_blank" class="btn-view">View Transcript</a>
                    </li>`;
            });
        }
        window.onload = loadTranscripts;
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    if 'user_id' not in session:
        return render_template_string(LOGIN_HTML)
    now = time.time()
    active_cooldown_count = sum(1 for expire in user_cooldowns.values() if now < expire)
    avatar_url = f"https://cdn.discordapp.com/avatars/{session['user_id']}/{session['avatar']}.png" if session.get('avatar') else "https://cdn.discordapp.com/embed/avatars/0.png"
    return render_template_string(DASHBOARD_HTML, ticket_count=f"{ticket_counter:04d}", active_cooldowns=active_cooldown_count, username=session.get('username', 'User'), user_avatar=avatar_url)

@app.route('/transcript/<channel_name>')
def view_transcript(channel_name):
    if 'user_id' not in session:
        session['redirect_after_login'] = f"/transcript/{channel_name}"
        return redirect('/login')
    filepath = os.path.join(TRANSCRIPT_DIR, f"transcript-{channel_name}.html")
    if not os.path.exists(filepath):
        return "Transcript not found", 404
    return send_file(filepath)

@app.route('/login')
def login():
    discord_auth_url = f"{DISCORD_API_URL}/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds.members.read"
    return redirect(discord_auth_url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code: return "Auth Failed", 400
    data = {'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET, 'grant_type': 'authorization_code', 'code': code, 'redirect_uri': REDIRECT_URI}
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    token_response = requests.post(f"{DISCORD_API_URL}/oauth2/token", data=data, headers=headers).json()
    access_token = token_response.get('access_token')
    if not access_token: return "OAuth Error", 400

    user_headers = {'Authorization': f"Bearer {access_token}"}
    user_data = requests.get(f"{DISCORD_API_URL}/users/@me", headers=user_headers).json()
    user_id = int(user_data['id'])
    target = session.pop('redirect_after_login', '/')

    if user_id in ALLOWED_USER_IDS:
        session['user_id'] = user_data['id']
        session['username'] = user_data['username']
        session['avatar'] = user_data.get('avatar')
        return redirect(target)

    return "Access Denied", 403

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

@app.route('/api/transcripts')
def get_transcripts():
    saved = []
    if os.path.exists(TRANSCRIPT_DIR):
        for f in sorted(os.listdir(TRANSCRIPT_DIR), reverse=True):
            if f.startswith("transcript-") and f.endswith(".html"):
                saved.append({"channel_name": f.replace("transcript-", "").replace(".html", "")})
    return jsonify(saved)

def run(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): Thread(target=run).start()
keep_alive()

# ==============================================================================
# 🎫 DISCORD UI & TICKETING SYSTEM
# ==============================================================================

class CarryQuestionsModal(Modal):
    def __init__(self, category_name: str, prefix: str):
        super().__init__(title=f"Details: {category_name}")
        self.category_name = category_name
        self.prefix = prefix

        self.roblox_user = TextInput(label="Roblox Username", placeholder="e.g., Builderman", required=True)
        self.notes = TextInput(label="Additional Details", style=discord.TextStyle.paragraph, placeholder="Explain what you need...", required=False)

        self.add_item(self.roblox_user)
        self.add_item(self.notes)

    async def on_submit(self, interaction: discord.Interaction):
        global ticket_counter
        guild = interaction.guild
        user = interaction.user

        ticket_counter += 1
        save_data(DATA_FILE, {"ticket_counter": ticket_counter})

        channel_name = f"{self.prefix}-{ticket_counter:04d}"
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        for role_id in ALLOWED_ROLE_IDS:
            role = guild.get_role(role_id)
            if role: overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites, topic=f"OwnerID:{user.id}")

        embed = discord.Embed(title=f"🎫 Ticket Opened: {self.category_name}", color=discord.Color.green())
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="Roblox Username", value=self.roblox_user.value, inline=True)
        if self.notes.value:
            embed.add_field(name="Notes", value=self.notes.value, inline=False)

        await channel.send(embed=embed, view=TicketControlView())
        await interaction.response.send_message(f"✅ Ticket created in {channel.mention}!", ephemeral=True)

class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket_btn", emoji="🔒")
    async def close_btn(self, interaction: discord.Interaction, button: Button):
        if not can_user_close_ticket(interaction.user, interaction.channel):
            return await interaction.response.send_message("❌ Only staff or ticket owners can close this channel.", ephemeral=True)
        await interaction.response.send_message("Are you sure you want to close this ticket?", view=CloseConfirmView(), ephemeral=True)

class CloseConfirmView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Yes, Close It", style=discord.ButtonStyle.danger, custom_id="confirm_close")
    async def confirm(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("⚙️ Generating transcript and closing channel...")
        channel = interaction.channel

        # Generate HTML Transcript
        messages = []
        async for msg in channel.history(limit=500, oldest_first=True):
            messages.append(f"<p><strong>{html.escape(msg.author.name)}:</strong> {html.escape(msg.content)}</p>")

        html_content = f"<html><body><h1>Transcript for #{channel.name}</h1>{''.join(messages)}</body></html>"
        filepath = os.path.join(TRANSCRIPT_DIR, f"transcript-{channel.name}.html")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)

        await asyncio.sleep(3)
        await channel.delete()

class TicketSelectMenu(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Fallen Carry", value="fallen", emoji="🔥"),
            discord.SelectOption(label="Frost Carry", value="frost", emoji="❄️"),
            discord.SelectOption(label="General Support", value="other", emoji="💬")
        ]
        super().__init__(placeholder="Select a category to open a ticket...", options=options)

    async def callback(self, interaction: discord.Interaction):
        cat = self.values[0]
        await interaction.response.send_modal(CarryQuestionsModal(cat.capitalize(), cat))

class TicketSelectView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelectMenu())

# ==============================================================================
# ⚡ SLASH COMMANDS
# ==============================================================================

@bot.tree.command(name="scan_url", description="Check a URL or domain against VirusTotal for security threats")
@app_commands.describe(url="The web link or domain you want to check")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def scan_url(interaction: discord.Interaction, url: str):
    await interaction.response.defer(ephemeral=False)
    vt_key = os.getenv("VIRUSTOTAL_API_KEY")
    if not vt_key:
        return await interaction.followup.send("❌ VirusTotal API key not configured.", ephemeral=True)

    headers = {"x-apikey": vt_key}
    try:
        req = requests.post("https://www.virustotal.com/api/v3/urls", data={"url": url}, headers=headers)
        if req.status_code == 200:
            analysis_id = req.json()['data']['id']
            res = requests.get(f"https://www.virustotal.com/api/v3/analyses/{analysis_id}", headers=headers).json()
            stats = res['data']['attributes']['stats']
            
            embed = discord.Embed(title="🛡️ VirusTotal Scan Results", color=discord.Color.blue())
            embed.add_field(name="URL", value=url, inline=False)
            embed.add_field(name="Harmless", value=str(stats.get('harmless', 0)), inline=True)
            embed.add_field(name="Malicious", value=str(stats.get('malicious', 0)), inline=True)
            embed.add_field(name="Suspicious", value=str(stats.get('suspicious', 0)), inline=True)
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("❌ Failed to scan URL with VirusTotal API.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error during scan: {e}")

@bot.tree.command(name="setup_tickets", description="Send the ticket creation menu to this channel")
@app_commands.checks.has_permissions(administrator=True)
async def setup_tickets(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎫 Carry & Support Tickets",
        description="Select an option from the menu below to open a ticket!",
        color=discord.Color.blue()
    )
    await interaction.channel.send(embed=embed, view=TicketSelectView())
    await interaction.response.send_message("✅ Ticket menu created successfully!", ephemeral=True)

@bot.tree.command(name="close", description="Close the current ticket channel")
async def close_ticket(interaction: discord.Interaction):
    if not interaction.channel.name.startswith(TICKET_PREFIXES):
        return await interaction.response.send_message("❌ You can only run this inside ticket channels!", ephemeral=True)
    
    if not can_user_close_ticket(interaction.user, interaction.channel):
        return await interaction.response.send_message("❌ You lack permission to close this ticket.", ephemeral=True)

    await interaction.response.send_message("Are you sure you want to close this ticket?", view=CloseConfirmView())

# ==============================================================================
# 🚀 BOT STARTUP
# ==============================================================================

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"🔁 Synced {len(synced)} Slash Commands with Discord!")
    except Exception as e:
        print(f"❌ Failed to sync tree commands: {e}")

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    print("❌ Error: DISCORD_BOT_TOKEN environment variable is not set.")
else:
    bot.run(TOKEN)

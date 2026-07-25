import os
import io
import asyncio
import time
import json
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Select, Modal, TextInput
from flask import Flask, render_template_string, jsonify
from threading import Thread

# --- CONFIGURATION ---
BLACKLIST_ROLE_ID = 1530330613029015704

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Global trackers
user_cooldowns = {}  # Format: {user_id: timestamp_when_cooldown_expires}

# --- PERSISTENT DATA SYSTEMS ---
STORAGE_DIR = "/var/data" if os.path.exists("/var/data") else "."
DATA_FILE = os.path.join(STORAGE_DIR, "tickets.json")

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

# --- FLASK DASHBOARD SERVER ---
app = Flask('')

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ticket Bot Dashboard</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #0f172a;
            color: #f8fafc;
            margin: 0;
            padding: 40px;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
        }
        .header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 2px solid #1e293b;
            padding-bottom: 20px;
            margin-bottom: 30px;
        }
        .status-badge {
            background-color: #10b981;
            color: #022c22;
            padding: 6px 16px;
            border-radius: 20px;
            font-weight: bold;
            font-size: 0.9rem;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .card {
            background-color: #1e293b;
            border-radius: 12px;
            padding: 24px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }
        .card h3 {
            margin: 0 0 10px 0;
            color: #94a3b8;
            font-size: 0.95rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .card .value {
            font-size: 2.2rem;
            font-weight: bold;
            color: #38bdf8;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎫 Carry Bot Control Panel</h1>
            <span class="status-badge">🟢 Bot Online</span>
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
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    # Count how many users are currently actively on cooldown
    now = time.time()
    active_cooldown_count = sum(1 for expire in user_cooldowns.values() if now < expire)
    return render_template_string(DASHBOARD_HTML, ticket_count=f"{ticket_counter:04d}", active_cooldowns=active_cooldown_count)

@app.route('/api/stats')
def stats():
    now = time.time()
    return jsonify({
        "status": "online",
        "ticket_counter": ticket_counter,
        "active_cooldowns": sum(1 for expire in user_cooldowns.values() if now < expire)
    })

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

async def generate_transcript(channel: discord.TextChannel) -> discord.File:
    """Fetches all channel messages and creates a .txt transcript file."""
    lines = [f"=== TRANSCRIPT FOR TICKET CHANNEL: #{channel.name} ===", ""]
    
    async for msg in channel.history(limit=None, oldest_first=True):
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        attachments = f" [Attachments: {', '.join([a.url for a in msg.attachments])}]" if msg.attachments else ""
        content = msg.clean_content if msg.clean_content else "(No text content)"
        lines.append(f"[{timestamp}] {msg.author} ({msg.author.id}): {content}{attachments}")
        
        # Format embed details if any
        for embed in msg.embeds:
            if embed.title:
                lines.append(f"   [Embed Title: {embed.title}]")
            if embed.description:
                lines.append(f"   [Embed Description: {embed.description}]")
            for field in embed.fields:
                lines.append(f"   [Embed Field: {field.name} -> {field.value}]")

    transcript_text = "\n".join(lines)
    buffer = io.BytesIO(transcript_text.encode('utf-8'))
    return discord.File(fp=buffer, filename=f"transcript-{channel.name}.txt")

# --- CLOSE CONFIRMATION VIEW ---
class CloseConfirmView(View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="✅ Yes, Close", style=discord.ButtonStyle.danger, custom_id="confirm_close_btn")
    async def confirm_close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🔒 Ticket confirmed for closure. Generating transcript and deleting in 5 seconds...")

        # Set 8-hour cooldown
        user_cooldowns[interaction.user.id] = time.time() + 28800

        # Generate transcript file
        transcript_file = await generate_transcript(interaction.channel)

        # Log ticket closure with transcript attached
        log_channel = get_log_channel(interaction.guild)
        if log_channel:
            log_embed = discord.Embed(title="🔒 Ticket Closed", color=discord.Color.orange())
            log_embed.add_field(name="Closed By", value=interaction.user.mention, inline=False)
            log_embed.add_field(name="Ticket Channel", value=interaction.channel.name, inline=False)
            await log_channel.send(embed=log_embed, file=transcript_file)

        await asyncio.sleep(5)
        await interaction.channel.delete()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary, custom_id="cancel_close_btn")
    async def cancel_close(self, interaction: discord.Interaction, button: Button):
        await interaction.message.delete()
        await interaction.response.send_message("Ticket closure canceled.", ephemeral=True)

# --- BUTTON INSIDE OPENED TICKET ---
class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.secondary, custom_id="close_ticket_btn")
    async def close_ticket_button(self, interaction: discord.Interaction, button: Button):
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

        # Log ticket creation
        log_channel = get_log_channel(guild)
        if log_channel:
            log_embed = discord.Embed(title="📝 New Carry Ticket Opened", color=discord.Color.blue())
            log_embed.add_field(name="Ticket Number", value=f"#{ticket_counter:04d}", inline=True)
            log_embed.add_field(name="Category", value=self.category_val.replace('-', ' ').title(), inline=True)
            log_embed.add_field(name="Opened By", value=user.mention, inline=True)
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

        # Blacklist check via Role ID
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

# --- ALL SLASH COMMANDS ---

@bot.tree.command(name="setup_tickets", description="Spawns the Requesting Carry ticket panel")
@app_commands.checks.has_permissions(administrator=True)
async def setup_tickets(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Requesting Carry",
        description="Choose what you need help with",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="Normal Game Modes 🥶",
        value="You can request a carry service for any normal game mode.",
        inline=False
    )
    embed.add_field(
        name="Special Game Modes 🥺",
        value="You can request carry service for any special game mode, including Quickdraw and Lost Soul.",
        inline=False
    )
    embed.add_field(
        name="Trials 🐹",
        value="You can request carry service for any trials, however requesting quarantine or jailed may result in long wait.",
        inline=False
    )
    embed.add_field(
        name="Hardcore 💎",
        value="You can request carry service for hardcore your first hardcore run, but not for gem grinding.",
        inline=False
    )
    embed.add_field(
        name="Others 🐱",
        value="Other is used for game modes below Fallen and for mission quests.",
        inline=False
    )
    
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
        log_embed.add_field(name="Closed By", value=interaction.user.mention, inline=False)
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

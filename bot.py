import os
import asyncio
import time
import json
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Select, Modal, TextInput
from flask import Flask
from threading import Thread

# --- MINI KEEP-ALIVE SERVER FOR RENDER ---
app = Flask('')
@app.route('/')
def home(): return "Bot is alive!"

def run(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): Thread(target=run).start()
keep_alive()

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

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

# Load initial ticket counter
ticket_counter = load_data(DATA_FILE, {"ticket_counter": 0}).get("ticket_counter", 0)
user_cooldowns = {}

# --- HELPER FUNCTIONS ---
def get_log_channel(guild):
    log_channel_id = os.getenv("LOG_CHANNEL_ID")
    if log_channel_id and log_channel_id.isdigit():
        return guild.get_channel(int(log_channel_id))
    return None

def get_blacklist_role(guild):
    role_id = os.getenv("BLACKLIST_ROLE_ID")
    if role_id and role_id.isdigit():
        role = guild.get_role(int(role_id))
        if role:
            return role
    return discord.utils.get(guild.roles, name="Blacklisted")

# --- CLOSE CONFIRMATION VIEW ---
class CloseConfirmView(View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="✅ Yes, Close", style=discord.ButtonStyle.danger, custom_id="confirm_close_btn")
    async def confirm_close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🔒 Ticket confirmed for closure. Deleting in 5 seconds...")

        user_cooldowns[interaction.user.id] = time.time() + 28800

        log_channel = get_log_channel(interaction.guild)
        if log_channel:
            log_embed = discord.Embed(title="🔒 Ticket Closed", color=discord.Color.orange())
            log_embed.add_field(name="Closed By", value=f"{interaction.user.mention} (ID: {interaction.user.id})", inline=False)
            log_embed.add_field(name="Ticket Channel", value=interaction.channel.name, inline=False)
            await log_channel.send(embed=log_embed)

        await asyncio.sleep(5)
        await interaction.channel.delete()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary, custom_id="cancel_close_btn")
    async def cancel_close(self, interaction: discord.Interaction, button: Button):
        await interaction.message.delete()
        await interaction.response.send_message("Ticket closure canceled.", ephemeral=True)

# --- TICKET CONTROL VIEW ---
class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close", style=discord.ButtonStyle.secondary, custom_id="close_ticket_btn")
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

        ticket_payload = {
            "flags": 32768,
            "content": f"{user.mention}",
            "components": [
                {
                    "type": 17,
                    "accent_color": 3447003,
                    "components": [
                        {
                            "type": 10,
                            "components": [
                                {
                                    "type": 12,
                                    "content": f"## Ticket Created\nWelcome {user.mention}! Thank you for utilizing this carry service ticket. A Carry Team member will assist you shortly."
                                }
                            ]
                        },
                        {"type": 14},
                        {
                            "type": 10,
                            "components": [
                                {
                                    "type": 12,
                                    "content": (
                                        f"**1. ⏰ Which country and timezone are you from?**\n```{self.q1_timezone.value}```\n\n"
                                        f"**2. 🎮 What is your roblox display name?**\n```{self.q2_roblox.value}```\n\n"
                                        f"**3. 🎲 Are you able to join a private server?**\n```{self.q3_private_server.value}```"
                                    )
                                }
                            ]
                        }
                    ]
                },
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 2,
                            "style": 2,
                            "label": "🔒 Close",
                            "custom_id": "close_ticket_btn"
                        }
                    ]
                }
            ]
        }

        try:
            await bot.http.request(
                discord.http.Route('POST', '/channels/{channel_id}/messages', channel_id=ticket_channel.id),
                json=ticket_payload
            )
        except Exception:
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
            await ticket_channel.send(embeds=[welcome_embed, answers_embed], view=TicketControlView())

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

        # Blacklist check via Role
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
    await interaction.response.send_message("Ticket panel posted!", ephemeral=True)

    dropdown_dict = {
        "type": 1,
        "components": [
            {
                "type": 3,
                "custom_id": "carry_dropdown",
                "placeholder": "Select a category...",
                "options": [
                    {"label": "Fallen Carry", "description": "Request fallen mode carry", "emoji": {"name": "🛡️"}, "value": "fallen-carry"},
                    {"label": "Hidden Wave Carry", "description": "Request hidden wave carry", "emoji": {"name": "🥷"}, "value": "hidden-wave-carry"},
                    {"label": "Frost Carry", "description": "Request frost mode carry", "emoji": {"name": "❄️"}, "value": "frost-carry"},
                    {"label": "Event Carry", "description": "Request current event carry", "emoji": {"name": "🎮"}, "value": "event-carry"},
                    {"label": "Pizza Party Carry", "description": "Request pizza party carry", "emoji": {"name": "🍕"}, "value": "pizza-party-carry"},
                    {"label": "Lost Soul Carry", "description": "Request lost soul carry", "emoji": {"name": "👻"}, "value": "lost-soul-carry"},
                    {"label": "Badlands 2 Carry", "description": "Request badlands 2 carry", "emoji": {"name": "🤠"}, "value": "badlands-2-carry"},
                    {"label": "Quickdraw Carry", "description": "Request quickdraw carry", "emoji": {"name": "🔫"}, "value": "quickdraw-carry"},
                    {"label": "Polluted Wasteland 2 Carry", "description": "Request polluted wasteland 2 carry", "emoji": {"name": "☢️"}, "value": "polluted-wasteland-2-carry"},
                    {"label": "Trials", "description": "Request carry for trials", "emoji": {"name": "👾"}, "value": "trials"},
                    {"label": "Hardcore Carry", "description": "Request hardcore mode carry", "emoji": {"name": "💎"}, "value": "hardcore-carry"},
                    {"label": "Other", "description": "Request carry for something that not named above", "emoji": {"name": "❓"}, "value": "other"}
                ]
            }
        ]
    }

    panel_payload = {
        "flags": 32768,
        "components": [
            {
                "type": 17,
                "accent_color": 3447003,
                "components": [
                    {
                        "type": 10,
                        "components": [
                            {
                                "type": 12,
                                "content": "## Requesting Carry\nChoose what you need help with"
                            }
                        ]
                    },
                    {"type": 14},
                    {
                        "type": 10,
                        "components": [
                            {
                                "type": 12,
                                "content": (
                                    "### Normal Game Modes 🥶\nYou can request a carry service for any normal game mode.\n\n"
                                    "### Special Game Modes 🥺\nYou can request carry service for any special game mode, including Quickdraw and Lost Soul.\n\n"
                                    "### Trials 🐹\nYou can request carry service for any trials, however requesting quarantine or jailed may result in long wait.\n\n"
                                    "### Hardcore 💎\nYou can request carry service for hardcore your first hardcore run, but not for gem grinding.\n\n"
                                    "### Others 🐱\nOther is used for game modes below Fallen and for mission quests."
                                )
                            }
                        ]
                    }
                ]
            },
            dropdown_dict
        ]
    }

    if interaction.channel:
        try:
            await bot.http.request(
                discord.http.Route('POST', '/channels/{channel_id}/messages', channel_id=interaction.channel.id),
                json=panel_payload
            )
        except Exception as e:
            print(f"Failed to post panel: {e}")

@bot.tree.command(name="blacklist_add", description="Gives the Blacklisted role to a user")
@app_commands.checks.has_permissions(manage_roles=True)
async def blacklist_add(interaction: discord.Interaction, member: discord.Member):
    role = get_blacklist_role(interaction.guild)
    if not role:
        return await interaction.response.send_message("❌ Role `Blacklisted` not found. Please create a role named `Blacklisted` first!", ephemeral=True)

    if role in member.roles:
        await interaction.response.send_message(f"ℹ️ {member.mention} already has the **{role.name}** role.", ephemeral=True)
    else:
        await member.add_roles(role)
        await interaction.response.send_message(embed=discord.Embed(description=f"🚫 {member.mention} has been given the **{role.name}** role.", color=discord.Color.red()))

@bot.tree.command(name="blacklist_remove", description="Removes the Blacklisted role from a user")
@app_commands.checks.has_permissions(manage_roles=True)
async def blacklist_remove(interaction: discord.Interaction, member: discord.Member):
    role = get_blacklist_role(interaction.guild)
    if not role:
        return await interaction.response.send_message("❌ Role `Blacklisted` not found.", ephemeral=True)

    if role not in member.roles:
        await interaction.response.send_message(f"ℹ️ {member.mention} does not have the **{role.name}** role.", ephemeral=True)
    else:
        await member.remove_roles(role)
        await interaction.response.send_message(embed=discord.Embed(description=f"✅ Removed **{role.name}** role from {member.mention}.", color=discord.Color.green()))

TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)

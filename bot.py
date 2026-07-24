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
DATA_FILE = "tickets.json"
BLACKLIST_FILE = "blacklist.json"

def load_data(file, default):
    if os.path.exists(file):
        try:
            with open(file, "r") as f: return json.load(f)
        except Exception as e:
            print(f"Error loading {file}: {e}")
            return default
    return default

def save_data(file, data):
    try:
        with open(file, "w") as f: json.dump(data, f)
    except Exception as e:
        print(f"Error saving {file}: {e}")

ticket_counter = load_data(DATA_FILE, {"ticket_counter": 0})["ticket_counter"]
blacklisted_users = load_data(BLACKLIST_FILE, [])
user_cooldowns = {}

# --- HELPER FUNCTION: GET LOG CHANNEL ---
def get_log_channel(guild):
    log_channel_id = os.getenv("LOG_CHANNEL_ID")
    if log_channel_id and log_channel_id.isdigit():
        return guild.get_channel(int(log_channel_id))
    return None

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

        welcome_embed = discord.Embed(
            title="Ticket Created",
            description=f"Welcome {user.mention}! Thank you for utilizing this carry service ticket. A Carry Team member will assist you within a considerable amount of time.",
            color=discord.Color.blue()
        )

        answers_embed = discord.Embed(color=discord.Color.dark_grey())
        answers_embed.add_field(
            name="1. ⏰ Which country and timezone are you from?",
            value=f"```{self.q1_timezone.value}```",
            inline=False
        )
        answers_embed.add_field(
            name="2. 🎮 What is your roblox display name?",
            value=f"```{self.q2_roblox.value}```",
            inline=False
        )
        answers_embed.add_field(
            name="3. 🎲 Are you able to join a private server?",
            value=f"```{self.q3_private_server.value}```",
            inline=False
        )
        answers_embed.set_footer(text="Ticket Bot | Carry System")

        await ticket_channel.send(content=user.mention, embeds=[welcome_embed, answers_embed], view=TicketControlView())

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

        if user.id in blacklisted_users and not is_admin:
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

@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    if isinstance(channel, discord.TextChannel):
        if any(channel.name.startswith(p) for p in [
            "ticket-", "fallen-", "hidden-", "frost-", "event-", "pizza-", 
            "lost-", "badlands-", "quickdraw-", "polluted-", "trials-", "hardcore-", "other-"
        ]):
            guild = channel.guild
            log_channel = get_log_channel(guild)
            
            if log_channel:
                deleter_text = "Unknown / Manual Deletion"
                try:
                    async for entry in guild.audit_logs(action=discord.AuditLogAction.channel_delete, limit=5):
                        if entry.target.id == channel.id:
                            deleter_text = f"{entry.user.mention} (ID: {entry.user.id})"
                            break
                except Exception as e:
                    print(f"Could not fetch audit logs: {e}")

                log_embed = discord.Embed(title="🗑️ Carry Ticket Channel Deleted", color=discord.Color.red())
                log_embed.add_field(name="Ticket Channel", value=f"`{channel.name}` (ID: {channel.id})", inline=False)
                log_embed.add_field(name="Deleted By", value=deleter_text, inline=False)
                await log_channel.send(embed=log_embed)

# --- SLASH COMMANDS ---
@bot.tree.command(name="setup_tickets", description="Spawns the Requesting Carry ticket panel")
@app_commands.checks.has_permissions(administrator=True)
async def setup_tickets(interaction: discord.Interaction):
    # Hidden command acknowledgement to prevent the header line from appearing
    await interaction.response.send_message("Ticket panel posted!", ephemeral=True)

    embed = discord.Embed(
        title="Requesting Carry",
        description="Choose what you need help with\n\n"
                    "### Normal Game Modes 🥶\n"
                    "You can request a carry service for any normal game mode.\n\n"
                    "### Special Game Modes 🥺\n"
                    "You can request carry service for any special game mode, including Quickdraw and Lost Soul.\n\n"
                    "### Trials 🐹\n"
                    "You can request carry service for any trials, however requesting quarantine or jailed may result in long wait.\n\n"
                    "### Hardcore 💎\n"
                    "You can request carry service for hardcore your first hardcore run, but not for gem grinding.\n\n"
                    "### Others 🐱\n"
                    "Other is used for game modes below Fallen and for mission quests.",
        color=discord.Color.blue()
    )
    embed.set_footer(text="Ticket Bot | Carry System")

    if interaction.channel:
        try:
            await interaction.channel.send(embed=embed, view=TicketLauncher())
        except discord.Forbidden:
            print("Error: Bot lacks 'Send Messages' or 'Embed Links' permissions in this channel.")
        except Exception as e:
            print(f"Error sending panel: {e}")

@bot.tree.command(name="blacklist_add", description="Prevent a user from opening carry tickets")
@app_commands.checks.has_permissions(manage_channels=True)
async def blacklist_add(interaction: discord.Interaction, member: discord.Member):
    if member.id not in blacklisted_users:
        blacklisted_users.append(member.id)
        save_data(BLACKLIST_FILE, blacklisted_users)
        await interaction.response.send_message(embed=discord.Embed(description=f"🚫 {member.mention} has been blacklisted from tickets.", color=discord.Color.red()))
    else:
        await interaction.response.send_message(embed=discord.Embed(description=f"ℹ️ {member.mention} is already blacklisted.", color=discord.Color.blue()), ephemeral=True)

@bot.tree.command(name="blacklist_remove", description="Remove a user from the ticket blacklist")
@app_commands.checks.has_permissions(manage_channels=True)
async def blacklist_remove(interaction: discord.Interaction, member: discord.Member):
    if member.id in blacklisted_users:
        blacklisted_users.remove(member.id)
        save_data(BLACKLIST_FILE, blacklisted_users)
        await interaction.response.send_message(embed=discord.Embed(description=f"✅ {member.mention} removed from blacklist.", color=discord.Color.green()))
    else:
        await interaction.response.send_message(embed=discord.Embed(description=f"ℹ️ {member.mention} is not blacklisted.", color=discord.Color.blue()), ephemeral=True)

@bot.tree.command(name="set_ticket_count", description="Manually set or reset the ticket counter number")
@app_commands.checks.has_permissions(administrator=True)
async def set_ticket_count(interaction: discord.Interaction, number: int):
    global ticket_counter
    ticket_counter = number
    save_data(DATA_FILE, {"ticket_counter": ticket_counter})
    await interaction.response.send_message(embed=discord.Embed(description=f"⚙️ Ticket counter updated to **#{ticket_counter:04d}**.", color=discord.Color.green()), ephemeral=True)

@bot.tree.command(name="bypass_cooldown", description="Removes the ticket creation cooldown for a specific user")
@app_commands.checks.has_permissions(manage_channels=True)
async def bypass_cooldown(interaction: discord.Interaction, member: discord.Member):
    if member.id in user_cooldowns:
        del user_cooldowns[member.id]
        embed = discord.Embed(description=f"⚡ Cooldown removed for {member.mention}.", color=discord.Color.green())
    else:
        embed = discord.Embed(description=f"ℹ️ {member.mention} is not currently on cooldown.", color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need **Administrator** permissions to run this command.", ephemeral=True)
    else:
        print(f"Command Error: {error}")

TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN:
    bot.run(TOKEN)
else:
    print("Error: DISCORD_TOKEN environment variable is not set.")

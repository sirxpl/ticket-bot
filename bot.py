import os
import asyncio
import time
import json
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View
from flask import Flask
from threading import Thread

# --- MINI KEEP-ALIVE WEB SERVER FOR RENDER FREE TIER ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

keep_alive()
# ------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Global trackers
user_cooldowns = {}      # Format: {user_id: timestamp_when_cooldown_expires}

# --- PERSISTENT COUNTER SYSTEM (JSON FILE) ---
DATA_FILE = "tickets.json"

def load_ticket_counter():
    """Loads the ticket count from a local JSON file."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                return data.get("ticket_counter", 0)
        except Exception as e:
            print(f"Error loading ticket counter: {e}")
            return 0
    return 0

def save_ticket_counter(count):
    """Saves the current ticket count to a local JSON file."""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump({"ticket_counter": count}, f)
    except Exception as e:
        print(f"Error saving ticket counter: {e}")

# Initialize ticket counter from file
ticket_counter = load_ticket_counter()

# --- HELPER FUNCTION: GET LOG CHANNEL ---
def get_log_channel(guild):
    log_channel_id = os.getenv("LOG_CHANNEL_ID")
    if log_channel_id and log_channel_id.isdigit():
        return guild.get_channel(int(log_channel_id))
    return None


# --- CONFIRMATION BUTTONS VIEW ---
class CloseConfirmView(View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="✅ Yes, Close", style=discord.ButtonStyle.danger, custom_id="confirm_close_btn")
    async def confirm_close(self, interaction: discord.Interaction, button: Button):
        if not interaction.channel.name.startswith("ticket-"):
            return await interaction.response.send_message("This action can only be used inside a ticket channel!", ephemeral=True)

        await interaction.response.send_message("🔒 Ticket confirmed for closure. Deleting in 5 seconds...")

        # Set 8-hour cooldown (8 hours * 3600 seconds = 28,800 seconds)
        user_cooldowns[interaction.user.id] = time.time() + 28800

        # Log ticket closure
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


# --- BUTTON INSIDE THE OPENED TICKET ---
class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket_btn")
    async def close_ticket_button(self, interaction: discord.Interaction, button: Button):
        if not interaction.channel.name.startswith("ticket-"):
            return await interaction.response.send_message("This action can only be used inside a ticket channel!", ephemeral=True)

        embed = discord.Embed(
            title="Are you sure?",
            description="Are you sure you want to close this ticket? This action cannot be undone.",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, view=CloseConfirmView(), ephemeral=False)


# --- TICKET LAUNCHER BUTTON (MAIN PANEL) ---
class TicketLauncher(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Open Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_button")
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        global ticket_counter
        guild = interaction.guild
        user = interaction.user

        # 1. CHECK COOLDOWN USING DISCORD TIMESTAMP
        if user.id in user_cooldowns:
            expire_timestamp = int(user_cooldowns[user.id])
            if time.time() < expire_timestamp:
                return await interaction.response.send_message(
                    f"⏳ You are on cooldown! Please wait until <t:{expire_timestamp}:R> before opening another ticket.",
                    ephemeral=True
                )
            else:
                del user_cooldowns[user.id]

        # 2. CHECK FOR EXISTING TICKET
        for channel in guild.text_channels:
            if channel.name.startswith("ticket-"):
                overwrites = channel.overwrites_for(user)
                if overwrites.read_messages is True:
                    return await interaction.response.send_message(
                        f"You already have an open ticket: {channel.mention}", ephemeral=True
                    )

        # 3. INCREMENT COUNTER & SAVE TO FILE
        ticket_counter += 1
        save_ticket_counter(ticket_counter)  # Writes new count to tickets.json
        
        formatted_ticket_name = f"ticket-{ticket_counter:04d}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        ticket_channel = await guild.create_text_channel(
            name=formatted_ticket_name,
            overwrites=overwrites,
            reason=f"Ticket #{ticket_counter} opened by {user.name}"
        )

        await interaction.response.send_message(f"Ticket created! Check out {ticket_channel.mention}", ephemeral=True)
        
        embed = discord.Embed(
            title=f"Welcome {user.name}!",
            description=f"Ticket **#{ticket_counter:04d}**\n\nSupport will be with you shortly. Please describe your issue in detail.\n\nClick the button below if you wish to close this ticket.",
            color=discord.Color.green()
        )
        await ticket_channel.send(content=user.mention, embed=embed, view=TicketControlView())

        # LOGGING
        log_channel = get_log_channel(guild)
        if log_channel:
            log_embed = discord.Embed(title="📝 New Ticket Opened", color=discord.Color.blue())
            log_embed.add_field(name="Ticket Number", value=f"#{ticket_counter:04d}", inline=True)
            log_embed.add_field(name="Opened By", value=f"{user.mention} (ID: {user.id})", inline=True)
            log_embed.add_field(name="Ticket Channel", value=f"{ticket_channel.mention} (ID: {ticket_channel.id})", inline=False)
            await log_channel.send(embed=log_embed)


# --- BOT EVENTS & COMMANDS ---
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


@bot.tree.command(name="setup_tickets", description="Spawns the ticket creation panel")
@commands.has_permissions(administrator=True)
async def setup_tickets(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Need Support?",
        description="Click the button below to open a private support ticket.",
        color=discord.Color.blue()
    )
    await interaction.response.send_message("Ticket panel posted!", ephemeral=True)
    await interaction.channel.send(embed=embed, view=TicketLauncher())


@bot.tree.command(name="set_ticket_count", description="Manually set or reset the ticket counter number")
@commands.has_permissions(administrator=True)
async def set_ticket_count(interaction: discord.Interaction, number: int):
    global ticket_counter
    ticket_counter = number
    save_ticket_counter(ticket_counter)
    await interaction.response.send_message(
        embed=discord.Embed(
            description=f"⚙️ Ticket counter updated to **#{ticket_counter:04d}**.",
            color=discord.Color.green()
        ),
        ephemeral=True
    )


@bot.tree.command(name="bypass_cooldown", description="Removes the ticket creation cooldown for a specific user")
@commands.has_permissions(manage_channels=True)
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


@bot.tree.command(name="remove_user", description="Remove a user's permission to view this ticket channel")
@commands.has_permissions(manage_channels=True)
async def remove_user(interaction: discord.Interaction, member: discord.Member):
    if not interaction.channel.name.startswith("ticket-"):
        return await interaction.response.send_message("This command can only be used inside a ticket channel!", ephemeral=True)
    await interaction.channel.set_permissions(member, overwrite=None)
    await interaction.response.send_message(embed=discord.Embed(description=f"🚫 {member.mention} removed.", color=discord.Color.red()))


@bot.tree.command(name="add_user", description="Grant a user permission to view this ticket channel")
@commands.has_permissions(manage_channels=True)
async def add_user(interaction: discord.Interaction, member: discord.Member):
    if not interaction.channel.name.startswith("ticket-"):
        return await interaction.response.send_message("This command can only be used inside a ticket channel!", ephemeral=True)
    await interaction.channel.set_permissions(member, read_messages=True, send_messages=True)
    await interaction.response.send_message(embed=discord.Embed(description=f"✅ {member.mention} added.", color=discord.Color.green()))


@bot.tree.command(name="close", description="Close this ticket channel")
async def close_ticket(interaction: discord.Interaction):
    if not interaction.channel.name.startswith("ticket-"):
        return await interaction.response.send_message("This command can only be used inside a ticket channel!", ephemeral=True)
    
    embed = discord.Embed(
        title="Are you sure?",
        description="Are you sure you want to close this ticket? This action cannot be undone.",
        color=discord.Color.gold()
    )
    await interaction.response.send_message(embed=embed, view=CloseConfirmView(), ephemeral=False)


@bot.tree.command(name="force_close", description="Force close a ticket with a reason")
@commands.has_permissions(manage_channels=True)
async def force_close(interaction: discord.Interaction, reason: str):
    if not interaction.channel.name.startswith("ticket-"):
        return await interaction.response.send_message("This command can only be used inside a ticket channel!", ephemeral=True)
    
    await interaction.response.send_message(f"⚠️ **Ticket force closed** by {interaction.user.mention}.\n**Reason:** {reason}\n*Deleting in 5 seconds...*")
    
    user_cooldowns[interaction.user.id] = time.time() + 28800

    log_channel = get_log_channel(interaction.guild)
    if log_channel:
        log_embed = discord.Embed(title="⚠️ Ticket Force Closed", color=discord.Color.red())
        log_embed.add_field(name="Closed By", value=f"{interaction.user.mention} (ID: {interaction.user.id})", inline=False)
        log_embed.add_field(name="Ticket Name", value=interaction.channel.name, inline=False)
        log_embed.add_field(name="Reason", value=reason, inline=False)
        await log_channel.send(embed=log_embed)

    await asyncio.sleep(5)
    await interaction.channel.delete(reason=reason)


# --- EDIT TICKET CHANNEL COMMAND ---
@bot.tree.command(name="edit_ticket", description="Edit a ticket channel's name and number")
@commands.has_permissions(manage_channels=True)
async def edit_ticket(
    interaction: discord.Interaction, 
    target: discord.TextChannel, 
    numbers: int,          # Required ticket number
    name: str,             # Required name
    new_topic: str = None  # Optional topic
):
    # Ensure the target channel being edited is a ticket channel
    if not target.name.startswith("ticket-"):
        return await interaction.response.send_message(
            f"⚠️ {target.mention} is not a valid ticket channel!", 
            ephemeral=True
        )

    # Clean up the name input if the user typed "ticket-" themselves
    clean_name = name.removeprefix("ticket-").strip()

    # Format the combined name: ticket-0005-yourname
    formatted_name = f"ticket-{numbers:04d}-{clean_name}"

    kwargs = {'name': formatted_name}

    if new_topic:
        kwargs['topic'] = new_topic

    await target.edit(**kwargs)
    await interaction.response.send_message(
        f"✅ Successfully updated {target.mention} to **{formatted_name}**!", 
        ephemeral=True
    )


TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN:
    bot.run(TOKEN)
else:
    print("Error: DISCORD_TOKEN environment variable is not set.")

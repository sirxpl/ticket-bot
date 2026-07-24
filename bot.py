import os
import asyncio
import time
import json
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Select, LayoutView, Container, TextDisplay, Separator, ActionRow
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

# --- CONFIRMATION BUTTONS VIEW ---
class CloseConfirmView(View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="✅ Yes, Close", style=discord.ButtonStyle.danger, custom_id="confirm_close_btn")
    async def confirm_close(self, interaction: discord.Interaction, button: Button):
        if not interaction.channel.name.startswith("ticket-"):
            return await interaction.response.send_message("This action can only be used inside a ticket channel!", ephemeral=True)

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

# --- SELECT MENU & MAIN TICKET PANEL (COMPONENTS V2) ---
class TicketDropdown(Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="General Support", 
                description="General questions or help", 
                emoji=discord.PartialEmoji.from_str("<:your_custom_emoji_1:123456789012345678>"), 
                value="general"
            ),
            discord.SelectOption(
                label="Bug Report", 
                description="Report a bug or issue", 
                emoji=discord.PartialEmoji.from_str("<:your_custom_emoji_2:123456789012345678>"), 
                value="bug"
            ),
            discord.SelectOption(
                label="Billing / Store", 
                description="Questions regarding payments or purchases", 
                emoji=discord.PartialEmoji.from_str("<:your_custom_emoji_3:123456789012345678>"), 
                value="billing"
            ),
        ]
        super().__init__(placeholder="Select the type of support you need...", min_values=1, max_values=1, options=options, custom_id="ticket_dropdown")

    async def callback(self, interaction: discord.Interaction):
        global ticket_counter
        guild = interaction.guild
        user = interaction.user

        is_admin = user.guild_permissions.administrator

        if user.id in blacklisted_users and not is_admin:
            return await interaction.response.send_message("❌ You are blacklisted from creating support tickets.", ephemeral=True)

        if user.id in user_cooldowns and not is_admin:
            expire_timestamp = int(user_cooldowns[user.id])
            if time.time() < expire_timestamp:
                return await interaction.response.send_message(f"⏳ You are on cooldown! Please wait until <t:{expire_timestamp}:R>.", ephemeral=True)
            else:
                del user_cooldowns[user.id]

        if not is_admin:
            for channel in guild.text_channels:
                if channel.name.startswith("ticket-"):
                    if channel.overwrites_for(user).read_messages is True:
                        return await interaction.response.send_message(f"You already have an open ticket: {channel.mention}", ephemeral=True)

        ticket_counter += 1
        save_data(DATA_FILE, {"ticket_counter": ticket_counter})

        ticket_type = self.values[0]
        formatted_ticket_name = f"ticket-{ticket_counter:04d}-{ticket_type}"

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
            description=f"Ticket **#{ticket_counter:04d}** ({ticket_type.capitalize()})\n\nSupport will be with you shortly. Please describe your issue in detail.\n\nClick the button below if you wish to close this ticket.",
            color=discord.Color.green()
        )
        await ticket_channel.send(content=user.mention, embed=embed, view=TicketControlView())

        log_channel = get_log_channel(guild)
        if log_channel:
            log_embed = discord.Embed(title="📝 New Ticket Opened", color=discord.Color.blue())
            log_embed.add_field(name="Ticket Number", value=f"#{ticket_counter:04d}", inline=True)
            log_embed.add_field(name="Category", value=ticket_type.capitalize(), inline=True)
            log_embed.add_field(name="Opened By", value=f"{user.mention} (ID: {user.id})", inline=True)
            log_embed.add_field(name="Ticket Channel", value=f"{ticket_channel.mention} (ID: {ticket_channel.id})", inline=False)
            await log_channel.send(embed=log_embed)

class TicketLauncherV2(LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        
        main_card = Container(accent_color=discord.Color.blue())
        main_card.add_item(TextDisplay("## 📬 Support Center\nWelcome to our official support portal. Please follow the instructions below."))
        main_card.add_item(Separator(visible=True))
        main_card.add_item(TextDisplay("- Select a **Category** from the menu below.\n- Provide as much detail as possible.\n- Staff will be with you shortly."))
        
        menu_row = ActionRow().add_item(TicketDropdown())
        main_card.add_item(menu_row)
        
        self.add_item(main_card)

# --- EVENT LISTENERS ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    print(f"Loaded starting ticket count: {ticket_counter}")
    bot.add_view(TicketLauncherV2())
    bot.add_view(TicketControlView())
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(e)

# --- MANUAL DELETION DETECTOR ---
@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    # Check if the deleted channel was a ticket
    if isinstance(channel, discord.TextChannel) and channel.name.startswith("ticket-"):
        guild = channel.guild
        log_channel = get_log_channel(guild)
        
        if log_channel:
            deleter_text = "Unknown / Manual Deletion"
            
            # Search audit logs to find who manually deleted the channel
            try:
                async for entry in guild.audit_logs(action=discord.AuditLogAction.channel_delete, limit=5):
                    if entry.target.id == channel.id:
                        deleter_text = f"{entry.user.mention} (ID: {entry.user.id})"
                        break
            except Exception as e:
                print(f"Could not fetch audit logs: {e}")

            log_embed = discord.Embed(title="🗑️ Ticket Channel Deleted Manually", color=discord.Color.red())
            log_embed.add_field(name="Ticket Channel", value=f"`{channel.name}` (ID: {channel.id})", inline=False)
            log_embed.add_field(name="Deleted By", value=deleter_text, inline=False)
            await log_channel.send(embed=log_embed)

# --- SLASH COMMANDS ---
@bot.tree.command(name="setup_tickets", description="Spawns the upgraded ticket creation panel")
@commands.has_permissions(administrator=True)
async def setup_tickets(interaction: discord.Interaction):
    await interaction.response.send_message("Ticket panel posted!", ephemeral=True)
    await interaction.channel.send(view=TicketLauncherV2())

@bot.tree.command(name="blacklist_add", description="Prevent a user from opening support tickets")
@commands.has_permissions(manage_channels=True)
async def blacklist_add(interaction: discord.Interaction, member: discord.Member):
    if member.id not in blacklisted_users:
        blacklisted_users.append(member.id)
        save_data(BLACKLIST_FILE, blacklisted_users)
        await interaction.response.send_message(embed=discord.Embed(description=f"🚫 {member.mention} has been blacklisted.", color=discord.Color.red()))
    else:
        await interaction.response.send_message(embed=discord.Embed(description=f"ℹ️ {member.mention} is already blacklisted.", color=discord.Color.blue()), ephemeral=True)

@bot.tree.command(name="blacklist_remove", description="Remove a user from the ticket blacklist")
@commands.has_permissions(manage_channels=True)
async def blacklist_remove(interaction: discord.Interaction, member: discord.Member):
    if member.id in blacklisted_users:
        blacklisted_users.remove(member.id)
        save_data(BLACKLIST_FILE, blacklisted_users)
        await interaction.response.send_message(embed=discord.Embed(description=f"✅ {member.mention} removed from blacklist.", color=discord.Color.green()))
    else:
        await interaction.response.send_message(embed=discord.Embed(description=f"ℹ️ {member.mention} is not blacklisted.", color=discord.Color.blue()), ephemeral=True)

@bot.tree.command(name="set_ticket_count", description="Manually set or reset the ticket counter number")
@commands.has_permissions(administrator=True)
async def set_ticket_count(interaction: discord.Interaction, number: int):
    global ticket_counter
    ticket_counter = number
    save_data(DATA_FILE, {"ticket_counter": ticket_counter})
    await interaction.response.send_message(embed=discord.Embed(description=f"⚙️ Ticket counter updated to **#{ticket_counter:04d}**.", color=discord.Color.green()), ephemeral=True)

@bot.tree.command(name="bypass_cooldown", description="Removes the ticket creation cooldown for a specific user")
@commands.has_permissions(manage_channels=True)
async def bypass_cooldown(interaction: discord.Interaction, member: discord.Member):
    if member.id in user_cooldowns:
        del user_cooldowns[member.id]
        embed = discord.Embed(description=f"⚡ Cooldown removed for {member.mention}.", color=discord.Color.green())
    else:
        embed = discord.Embed(description=f"ℹ️ {member.mention} is not currently on cooldown.", color=discord.Color.blue())
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
    embed = discord.Embed(title="Are you sure?", description="Are you sure you want to close this ticket?", color=discord.Color.gold())
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

@bot.tree.command(name="edit_ticket", description="Edit a ticket channel's name and number")
@commands.has_permissions(manage_channels=True)
async def edit_ticket(interaction: discord.Interaction, target: discord.TextChannel, numbers: int, name: str, new_topic: str = None):
    if not target.name.startswith("ticket-"):
        return await interaction.response.send_message(f"⚠️ {target.mention} is not a valid ticket channel!", ephemeral=True)

    clean_name = name.removeprefix("ticket-").strip()
    formatted_name = f"ticket-{numbers:04d}-{clean_name}"

    kwargs = {'name': formatted_name}
    if new_topic:
        kwargs['topic'] = new_topic

    await target.edit(**kwargs)
    await interaction.response.send_message(f"✅ Successfully updated {target.mention} to **{formatted_name}**!", ephemeral=True)

TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN:
    bot.run(TOKEN)
else:
    print("Error: DISCORD_TOKEN environment variable is not set.")

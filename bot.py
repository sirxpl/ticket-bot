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

# --- MINI KEEP-ALIVE SERVER ---
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

# --- PERSISTENT DATA ---
DATA_FILE = "tickets.json"
BLACKLIST_FILE = "blacklist.json"

def load_data(file, default):
    if os.path.exists(file):
        try:
            with open(file, "r") as f: return json.load(f)
        except: return default
    return default

def save_data(file, data):
    with open(file, "w") as f: json.dump(data, f)

ticket_counter = load_data(DATA_FILE, {"ticket_counter": 0})["ticket_counter"]
blacklisted_users = load_data(BLACKLIST_FILE, [])
user_cooldowns = {}

# --- COMPONENTS V2 UI ---

class TicketDropdown(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="General Support", emoji="💬", value="general"),
            discord.SelectOption(label="Bug Report", emoji="🐛", value="bug"),
            discord.SelectOption(label="Billing", emoji="💳", value="billing"),
        ]
        super().__init__(placeholder="Choose a category...", min_values=1, max_values=1, options=options, custom_id="ticket_dropdown")

    async def callback(self, interaction: discord.Interaction):
        global ticket_counter
        user = interaction.user
        is_admin = user.guild_permissions.administrator

        if user.id in blacklisted_users and not is_admin:
            return await interaction.response.send_message("❌ Blacklisted.", ephemeral=True)

        if user.id in user_cooldowns and not is_admin:
            expire = int(user_cooldowns[user.id])
            if time.time() < expire:
                return await interaction.response.send_message(f"⏳ Cooldown: <t:{expire}:R>", ephemeral=True)

        if not is_admin:
            for ch in interaction.guild.text_channels:
                if ch.name.startswith("ticket-") and ch.overwrites_for(user).read_messages:
                    return await interaction.response.send_message(f"You have a ticket: {ch.mention}", ephemeral=True)

        ticket_counter += 1
        save_data(DATA_FILE, {"ticket_counter": ticket_counter})
        
        category = self.values[0]
        name = f"ticket-{ticket_counter:04d}-{category}"
        
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        channel = await interaction.guild.create_text_channel(name=name, overwrites=overwrites)
        await interaction.response.send_message(f"Created! {channel.mention}", ephemeral=True)

        # Content for the inside of the ticket
        await channel.send(f"{user.mention} Welcome to your **{category.upper()}** ticket.", view=TicketControlView())

class TicketLauncherV2(LayoutView):
    """The Upgraded Setup Panel using Components V2 Layouts"""
    def __init__(self):
        super().__init__(timeout=None)
        
        # 1. Main UI Card (Container)
        main_card = Container(accent_color=discord.Color.blue())
        
        # 2. Rich Text Header
        main_card.add_item(TextDisplay("## 📬 Support Center\nWelcome to our official support portal. Please follow the instructions below."))
        
        # 3. Visual Divider
        main_card.add_item(Separator(visible=True))
        
        # 4. Instructions Block
        main_card.add_item(TextDisplay("- Select a **Category** from the menu below.\n- Provide as much detail as possible.\n- Staff will be with you shortly."))
        
        # 5. The Interactive Menu inside an ActionRow
        menu_row = ActionRow().add_item(TicketDropdown())
        main_card.add_item(menu_row)
        
        # Add the finished container to the View
        self.add_item(main_card)

# --- TICKET CONTROLS (Inside Ticket) ---

class CloseConfirmView(View):
    def __init__(self): super().__init__(timeout=60)
    @discord.ui.button(label="Confirm Close", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, btn):
        await interaction.response.send_message("🔒 Closing in 5s...")
        user_cooldowns[interaction.user.id] = time.time() + 28800
        await asyncio.sleep(5)
        await interaction.channel.delete()

class TicketControlView(View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.secondary, custom_id="close_btn")
    async def close(self, interaction: discord.Interaction, btn):
        await interaction.response.send_message("Are you sure?", view=CloseConfirmView(), ephemeral=True)

# --- SLASH COMMANDS ---

@bot.tree.command(name="setup_tickets", description="Spawns the V2 Components panel")
@app_commands.checks.has_permissions(administrator=True)
async def setup_tickets(interaction: discord.Interaction):
    # In V2, we don't send 'content' or 'embeds'; we just send the View
    await interaction.response.send_message(view=TicketLauncherV2())

@bot.tree.command(name="blacklist_add")
@app_commands.checks.has_permissions(manage_channels=True)
async def bl_add(interaction: discord.Interaction, member: discord.Member):
    if member.id not in blacklisted_users:
        blacklisted_users.append(member.id)
        save_data(BLACKLIST_FILE, blacklisted_users)
        await interaction.response.send_message(f"🚫 {member.mention} blacklisted.")

@bot.tree.command(name="edit_ticket")
@app_commands.checks.has_permissions(manage_channels=True)
async def edit_ticket(interaction: discord.Interaction, target: discord.TextChannel, numbers: int, name: str):
    clean = name.removeprefix("ticket-").strip()
    new_name = f"ticket-{numbers:04d}-{clean}"
    await target.edit(name=new_name)
    await interaction.response.send_message(f"✅ Renamed to {new_name}")

# --- BOT START ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    bot.add_view(TicketLauncherV2())
    bot.add_view(TicketControlView())
    await bot.tree.sync()

bot.run(os.getenv("DISCORD_TOKEN"))

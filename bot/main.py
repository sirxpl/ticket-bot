import os
import asyncio
from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask, render_template, request, redirect, flash, session
import chat_exporter

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
DASHBOARD_URL = os.getenv("OAUTH2_REDIRECT_URI", "https://ticket-bot-f184.onrender.com").replace("/callback", "")

# Directory setup for transcripts
TRANSCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "transcripts")
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)

# Flask App Initialisation
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key-change-this")

# Discord Bot Initialisation
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ---------------------------------------------------------------------------
# CARRY REQUEST MODAL
# ---------------------------------------------------------------------------
class CarryRequestModal(discord.ui.Modal, title="Request Carry"):
    game_mode = discord.ui.TextInput(
        label="What game mode?",
        placeholder="e.g. Normal / Hardcore / Insane",
        required=True,
        max_length=50
    )
    
    additional_info = discord.ui.TextInput(
        label="Additional Information",
        style=discord.TextStyle.paragraph,
        placeholder="Provide extra details (e.g. carry type, level, username)...",
        required=False,
        max_length=500
    )

    def __init__(self, category_id: int = None, support_role_id: int = None, log_channel_id: int = None):
        super().__init__()
        self.category_id = category_id
        self.support_role_id = support_role_id
        self.log_channel_id = log_channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        channel_name = f"carry-{interaction.user.name.lower()}"

        # Category Setup
        category = None
        if self.category_id:
            category = interaction.guild.get_channel(self.category_id)
        if not category:
            category = discord.utils.get(interaction.guild.categories, name="CARRY TICKETS")
            if not category:
                category = await interaction.guild.create_category("CARRY TICKETS")

        # Overwrites Setup
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        if self.support_role_id:
            role = interaction.guild.get_role(self.support_role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel = await interaction.guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites
        )

        ticket_embed = discord.Embed(
            title=f"🎫 Carry Ticket for {interaction.user.display_name}",
            description=f"Welcome {interaction.user.mention}! Our carry team will be with you shortly.",
            color=discord.Color.blurple()
        )
        ticket_embed.add_field(name="🎮 Game Mode", value=self.game_mode.value, inline=True)
        ticket_embed.add_field(
            name="📝 Additional Info", 
            value=self.additional_info.value if self.additional_info.value.strip() else "None provided.", 
            inline=False
        )

        ping_content = interaction.user.mention
        if self.support_role_id:
            role = interaction.guild.get_role(self.support_role_id)
            if role:
                ping_content += f" {role.mention}"

        await channel.send(content=ping_content, embed=ticket_embed, view=TicketControlView())
        await interaction.followup.send(f"✅ Ticket created! Check out {channel.mention}", ephemeral=True)


# ---------------------------------------------------------------------------
# TICKET CHANNEL CONTROLS
# ---------------------------------------------------------------------------
class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="ticket_close", emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        confirm_view = discord.ui.View()
        
        async def confirm_callback(inter: discord.Interaction):
            await inter.response.send_message("🔒 Closing ticket in 5 seconds...")
            await asyncio.sleep(5)
            await inter.channel.delete()

        confirm_btn = discord.ui.Button(label="Confirm Close", style=discord.ButtonStyle.danger)
        confirm_btn.callback = confirm_callback
        confirm_view.add_item(confirm_btn)

        await interaction.response.send_message("Are you sure you want to close this ticket?", view=confirm_view, ephemeral=True)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="ticket_claim", emoji="🙋‍♂️")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        await interaction.response.edit_message(view=self)
        
        embed = discord.Embed(description=f"🙋‍♂️ Ticket claimed by {interaction.user.mention}.", color=discord.Color.green())
        await interaction.channel.send(embed=embed)


# ---------------------------------------------------------------------------
# PUBLIC CARRY PANEL VIEW
# ---------------------------------------------------------------------------
class CarryPanelView(discord.ui.View):
    def __init__(self, category_id: int = 0, support_role_id: int = 0, log_channel_id: int = 0):
        super().__init__(timeout=None)
        cat = category_id or 0
        sup = support_role_id or 0
        log = log_channel_id or 0

        btn = discord.ui.Button(
            label="Request Carry",
            style=discord.ButtonStyle.primary,
            custom_id=f"req_carry:{cat}:{sup}:{log}",
            emoji="🛒"
        )
        btn.callback = self.request_carry_callback
        self.add_item(btn)

    async def request_carry_callback(self, interaction: discord.Interaction):
        custom_id = interaction.data.get("custom_id", "")
        parts = custom_id.split(":")
        
        category_id = int(parts[1]) if len(parts) > 1 and parts[1] != "0" else None
        support_role_id = int(parts[2]) if len(parts) > 2 and parts[2] != "0" else None
        log_channel_id = int(parts[3]) if len(parts) > 3 and parts[3] != "0" else None

        await interaction.response.send_modal(
            CarryRequestModal(category_id, support_role_id, log_channel_id)
        )


# ---------------------------------------------------------------------------
# FLASK DASHBOARD ROUTES
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    guild = bot.guilds[0] if bot.guilds else None
    channels = guild.text_channels if guild else []
    categories = guild.categories if guild else []
    roles = guild.roles if guild else []

    user_data = session.get("user", None)

    return render_template(
        "dashboard.html",
        user=user_data,
        channels=channels,
        categories=categories,
        roles=roles,
        total_tickets=0,
        active_tickets=[],
        transcripts=[],
        cooldowns=[],
        blacklisted_users=[]
    )

@app.route("/dashboard/tickets", methods=["POST"])
def deploy_ticket_panel():
    channel_id = int(request.form.get("channel_id", 0))
    category_id = int(request.form.get("category_id", 0)) if request.form.get("category_id") else None
    support_role_id = int(request.form.get("support_role_id", 0)) if request.form.get("support_role_id") else None
    log_channel_id = int(request.form.get("log_channel_id", 0)) if request.form.get("log_channel_id") else None
    title = request.form.get("title", "Request Carry")
    description = request.form.get("description", "Click below to request a carry ticket!")

    channel = bot.get_channel(channel_id)
    if channel:
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.blue()
        )
        view = CarryPanelView(category_id, support_role_id, log_channel_id)
        
        future = asyncio.run_coroutine_threadsafe(
            channel.send(embed=embed, view=view),
            bot.loop
        )
        try:
            future.result(timeout=10)
            flash("🎉 Carry panel successfully deployed to Discord!", "success")
        except Exception as e:
            flash(f"❌ Failed to send panel: {e}", "danger")
    else:
        flash("❌ Discord channel not found.", "danger")

    return redirect("/")


# ---------------------------------------------------------------------------
# BOT STARTUP & RUNNER
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    bot.add_view(TicketControlView())
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} application commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    print(f"✅ Bot logged in as {bot.user}")


def run_flask():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    import threading
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ Error: DISCORD_TOKEN environment variable is missing.")

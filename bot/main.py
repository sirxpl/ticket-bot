import os
import asyncio
from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask, render_template, request, redirect, flash
import chat_exporter
from utils.storage import increment_ticket_counter, is_blacklisted, TRANSCRIPTS_DIR

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
DASHBOARD_URL = os.getenv("OAUTH2_REDIRECT_URI", "https://ticket-bot-f184.onrender.com").replace("/callback", "")

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

        if is_blacklisted(interaction.user.id):
            return await interaction.followup.send("❌ You are blacklisted from opening carry tickets.", ephemeral=True)

        count = increment_ticket_counter()
        channel_name = f"carry-{count:04d}"

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
            title=f"🎫 Carry Ticket #{count:04d}",
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

        # Log Ticket Creation
        target_log_id = self.log_channel_id or LOG_CHANNEL_ID
        log_chan = interaction.guild.get_channel(target_log_id)
        if log_chan:
            log_embed = discord.Embed(title="🎫 New Carry Ticket Opened", color=discord.Color.green())
            log_embed.add_field(name="Ticket Number", value=f"#{count:04d}", inline=True)
            log_embed.add_field(name="User", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Channel", value=channel.mention, inline=True)
            log_embed.add_field(name="Game Mode", value=self.game_mode.value, inline=False)
            log_embed.add_field(
                name="Additional Info", 
                value=self.additional_info.value if self.additional_info.value.strip() else "None", 
                inline=False
            )
            await log_chan.send(embed=log_embed)


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

    @discord.ui.button(label="Website Transcript", style=discord.ButtonStyle.primary, custom_id="ticket_transcript", emoji="📜")
    async def export_transcript(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        filename = f"{interaction.channel.name}.html"
        filepath = os.path.join(TRANSCRIPTS_DIR, filename)

        transcript = await chat_exporter.export(interaction.channel)
        if transcript:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(transcript)

        view_url = f"{DASHBOARD_URL}/transcripts/{filename}"
        
        embed = discord.Embed(
            title="🌐 Transcript Saved to Website",
            description=f"The transcript for `{interaction.channel.name}` is now saved to the web dashboard.\n\n🔗 [Click Here to View Transcript]({view_url})",
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# PUBLIC CARRY PANEL VIEW (Encodes Config into Button custom_id)
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
# FLASK ROUTES
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    return "Bot and Web Dashboard are running!"

@app.route("/dashboard/tickets", methods=["GET", "POST"])
def ticket_panel_config():
    if request.method == "POST":
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

        return redirect("/dashboard/tickets")

    guild = bot.guilds[0] if bot.guilds else None
    channels = guild.text_channels if guild else []
    categories = guild.categories if guild else []
    roles = guild.roles if guild else []

    return render_template(
        "tickets_config.html",
        channels=channels,
        categories=categories,
        roles=roles
    )


# ---------------------------------------------------------------------------
# BOT EVENTS & STARTUP
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    # Only register persistent controls for active ticket channels
    bot.add_view(TicketControlView())
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} application commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    print(f"✅ Bot is online as {bot.user}")


# Entry point runner
def run_flask():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    import threading
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    bot.run(TOKEN)

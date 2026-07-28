import os
import asyncio
from dotenv import load_dotenv
import discord
from discord.ext import commands
from flask import Flask, render_template, request, redirect, flash, session

# ---------------------------------------------------------------------------
# INITIALIZATION & SETUP
# ---------------------------------------------------------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")

# Flask Setup
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key-change-this")

# Discord Bot Setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ---------------------------------------------------------------------------
# FLASK WEB DASHBOARD ROUTES
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

    # Retrieve the tickets cog
    cog = bot.get_cog("TicketsCog")
    if cog:
        future = asyncio.run_coroutine_threadsafe(
            cog.deploy_panel_from_dashboard(
                channel_id=channel_id,
                title=title,
                description=description,
                category_id=category_id,
                support_role_id=support_role_id,
                log_channel_id=log_channel_id
            ),
            bot.loop
        )
        try:
            success, msg = future.result(timeout=10)
            if success:
                flash("🎉 Carry panel successfully deployed to Discord!", "success")
            else:
                flash(f"❌ {msg}", "danger")
        except Exception as e:
            flash(f"❌ Failed to send panel: {e}", "danger")
    else:
        flash("❌ Tickets cog not loaded.", "danger")

    return redirect("/")


# ---------------------------------------------------------------------------
# STARTUP & RUNNERS
# ---------------------------------------------------------------------------
@bot.event
async def setup_hook():
    # Automatically load cogs from the cogs directory
    if os.path.exists("cogs"):
        for filename in os.listdir("cogs"):
            if filename.endswith(".py"):
                await bot.load_extension(f"cogs.{filename[:-3]}")

@bot.event
async def on_ready():
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
        print("❌ Error: DISCORD_BOT_TOKEN environment variable is missing.")

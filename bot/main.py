# main.py
import os
import threading
import discord
from discord.ext import commands
from dashboard import app as flask_app

# Start Flask in background thread
def run_flask():
    port = int(os.getenv("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

# Discord Bot Setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.load_extension("cogs.tickets")
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(os.getenv("DISCORD_BOT_TOKEN"))

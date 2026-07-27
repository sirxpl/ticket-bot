# main.py
import os
import sys
import threading
import asyncio
import discord
from discord.ext import commands, tasks

# Ensure Python can locate dashboard.py if main.py is inside a subfolder (e.g., /bot)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard
from dashboard import app as flask_app


# --- RUN FLASK WEB DASHBOARD IN BACKGROUND THREAD ---
def run_flask():
    port = int(os.getenv("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()


# --- BOT CLASS SETUP ---
class TicketBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def setup_hook(self):
        # Load cogs
        await self.load_extension("cogs.tickets")
        
        # Sync slash commands globally
        synced = await self.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s).")
        
        # Start background task to update dashboard cache
        self.update_active_tickets.start()

    async def on_ready(self):
        print(f"🤖 Bot is online and logged in as {self.user}")

    # Task running every 10 seconds to update live channels on the Flask website
    @tasks.loop(seconds=10)
    async def update_active_tickets(self):
        active_channels = []
        for guild in self.guilds:
            for channel in guild.text_channels:
                # Matches ticket channel naming conventions
                if "ticket-" in channel.name or "report-" in channel.name or "carry-" in channel.name:
                    active_channels.append({
                        "name": channel.name,
                        "id": channel.id,
                        "guild_id": guild.id
                    })
        dashboard.active_tickets_cache = active_channels


# --- INITIALIZE AND RUN BOT ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = TicketBot(command_prefix="!", intents=intents)

token = os.getenv("DISCORD_BOT_TOKEN")
if not token:
    print("❌ ERROR: 'DISCORD_BOT_TOKEN' environment variable is missing!")
else:
    bot.run(token)

# main.py
import os
import threading
import discord
from discord.ext import commands
from dashboard import app as flask_app

# --- RUN FLASK IN BACKGROUND THREAD ---
def run_flask():
    port = int(os.getenv("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()


# --- CUSTOM BOT CLASS WITH SETUP HOOK ---
class TicketBot(commands.Bot):
    async def setup_hook(self):
        # Load extensions before the bot logs in
        await self.load_extension("cogs.tickets")
        await self.tree.sync()
        print("✅ Cog loaded & slash commands synced!")

    async def on_ready(self):
        print(f"🤖 Bot is online as {self.user}")


# --- BOT INITIALIZATION ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = TicketBot(command_prefix="!", intents=intents)

bot.run(os.getenv("DISCORD_BOT_TOKEN"))

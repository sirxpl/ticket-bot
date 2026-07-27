# main.py
import os
import asyncio
import discord
from discord.ext import commands
from web.dashboard import start_dashboard

# --- BOT CONFIGURATION ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- ON READY EVENT ---
@bot.event
async def on_ready():
    print(f"🤖 Logged in as: {bot.user.name} ({bot.user.id})")
    print("--------------------------------------------------")
    
    # Sync slash commands globally
    try:
        synced = await bot.tree.sync()
        print(f"⚡ Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"❌ Failed to sync slash commands: {e}")

# --- COG LOADING FUNCTION ---
async def load_cogs():
    cogs_dir = "./cogs"
    if os.path.exists(cogs_dir):
        for filename in os.listdir(cogs_dir):
            if filename.endswith(".py") and not filename.startswith("__"):
                cog_name = f"cogs.{filename[:-3]}"
                try:
                    await bot.load_extension(cog_name)
                    print(f"📦 Loaded Cog: {cog_name}")
                except Exception as e:
                    print(f"❌ Failed to load Cog {cog_name}: {e}")

# --- MAIN ASYNC RUNNER ---
async def main():
    # 1. Start the Flask Web Dashboard in a background thread
    start_dashboard()
    print("🌐 Web Dashboard thread started.")

    async with bot:
        # 2. Load all modular Cogs
        await load_cogs()
        
        # 3. Retrieve Bot Token from environment variables
        token = os.getenv("DISCORD_BOT_TOKEN")
        if not token:
            print("❌ ERROR: 'DISCORD_BOT_TOKEN' not found in environment variables!")
            return

        # 4. Start the Bot
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())

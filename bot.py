import os
import discord
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

keep_alive()  # Starts the web server in the background
# ------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)


# --- TICKET BUTTON INTERACTION ---
class TicketLauncher(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Open Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_button")
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        user = interaction.user

        existing_channel = discord.utils.get(guild.text_channels, name=f"ticket-{user.name.lower()}")
        if existing_channel:
            await interaction.response.send_message(f"You already have an open ticket: {existing_channel.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        ticket_channel = await guild.create_text_channel(
            name=f"ticket-{user.name}",
            overwrites=overwrites,
            reason=f"Ticket opened by {user.name}"
        )

        await interaction.response.send_message(f"Ticket created! Check out {ticket_channel.mention}", ephemeral=True)
        
        embed = discord.Embed(
            title=f"Welcome {user.name}!",
            description="Support will be with you shortly. Please describe your issue in detail.",
            color=discord.Color.green()
        )
        await ticket_channel.send(content=user.mention, embed=embed)


# --- BOT EVENTS & COMMANDS ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    bot.add_view(TicketLauncher())
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


@bot.tree.command(name="remove_user", description="Remove a user's permission to view this ticket channel")
@commands.has_permissions(manage_channels=True)
async def remove_user(interaction: discord.Interaction, member: discord.Member):
    if not interaction.channel.name.startswith("ticket-"):
        await interaction.response.send_message("This command can only be used inside a ticket channel!", ephemeral=True)
        return

    await interaction.channel.set_permissions(member, overwrite=None)
    
    embed = discord.Embed(
        description=f"🚫 {member.mention} has been removed from this ticket.",
        color=discord.Color.red()
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="add_user", description="Grant a user permission to view this ticket channel")
@commands.has_permissions(manage_channels=True)
async def add_user(interaction: discord.Interaction, member: discord.Member):
    if not interaction.channel.name.startswith("ticket-"):
        await interaction.response.send_message("This command can only be used inside a ticket channel!", ephemeral=True)
        return

    await interaction.channel.set_permissions(member, read_messages=True, send_messages=True)
    
    embed = discord.Embed(
        description=f"✅ {member.mention} has been added to this ticket.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)


TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN:
    bot.run(TOKEN)
else:
    print("Error: DISCORD_TOKEN environment variable not set!")

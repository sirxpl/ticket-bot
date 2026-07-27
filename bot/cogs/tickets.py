# cogs/tickets.py
import os
import requests
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Select, Modal, TextInput
from utils.storage import (
    get_tickets_data, save_tickets_data, is_user_blacklisted,
    add_to_blacklist, remove_from_blacklist, create_html_transcript
)

BLACKLIST_ROLE_ID = 1530330613029015704

class CarryQuestionsModal(Modal, title="Ticket Details"):
    roblox_user = TextInput(label="Roblox Username", placeholder="e.g. Builderman", required=True)
    details = TextInput(label="Details / Notes", style=discord.TextStyle.paragraph, required=False)

    def __init__(self, category_name: str, prefix: str):
        super().__init__()
        self.category_name = category_name
        self.prefix = prefix

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user, guild = interaction.user, interaction.guild

        data = get_tickets_data()
        data["ticket_counter"] = data.get("ticket_counter", 0) + 1
        save_tickets_data(data)

        channel_name = f"{self.prefix}-{data['ticket_counter']:04d}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        ticket_channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites)
        embed = discord.Embed(
            title=f"🎫 {self.category_name} Ticket",
            description=f"Welcome {user.mention}!\n\n**Roblox User:** {self.roblox_user.value}\n**Notes:** {self.details.value or 'None'}",
            color=discord.Color.green()
        )
        await ticket_channel.send(content=f"{user.mention}", embed=embed, view=TicketControlView())
        await interaction.followup.send(f"✅ Ticket created in {ticket_channel.mention}", ephemeral=True)

class TicketControlView(View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("⏳ Closing channel...")
        await create_html_transcript(interaction.channel)
        await interaction.channel.delete()

class TicketDropdown(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="General Support", description="Get help", emoji="❓", value="general:ticket"),
            discord.SelectOption(label="Report User", description="Report a member", emoji="🚫", value="report:report")
        ]
        super().__init__(placeholder="Select a category...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if is_user_blacklisted(interaction.user.id):
            return await interaction.response.send_message("❌ You are blacklisted.", ephemeral=True)
        category_name, prefix = self.values[0].split(":")
        await interaction.response.send_modal(CarryQuestionsModal(category_name.capitalize(), prefix))

class TicketDropdownView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketDropdown())

class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot): self.bot = bot

    @app_commands.command(name="setup_tickets", description="Send ticket panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_tickets(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🎫 Support Tickets", description="Select an option below.", color=discord.Color.blue())
        await interaction.channel.send(embed=embed, view=TicketDropdownView())
        await interaction.response.send_message("✅ Panel sent!", ephemeral=True)

    @app_commands.command(name="blacklist", description="Blacklist a user")
    @app_commands.checks.has_permissions(administrator=True)
    async def blacklist(self, interaction: discord.Interaction, user: discord.Member):
        add_to_blacklist(user.id)
        await interaction.response.send_message(f"🚫 {user.mention} is blacklisted.", ephemeral=True)

    @app_commands.command(name="unblacklist", description="Unblacklist a user")
    @app_commands.checks.has_permissions(administrator=True)
    async def unblacklist(self, interaction: discord.Interaction, user: discord.Member):
        remove_from_blacklist(user.id)
        await interaction.response.send_message(f"✅ {user.mention} is unblacklisted.", ephemeral=True)

    @app_commands.command(name="scan_url", description="Scan a link on VirusTotal")
    async def scan_url(self, interaction: discord.Interaction, url: str):
        vt_key = os.getenv("VIRUSTOTAL_API_KEY")
        if not vt_key:
            return await interaction.response.send_message("❌ Missing VirusTotal API Key.", ephemeral=True)
        await interaction.response.defer()
        try:
            res = requests.post("https://www.virustotal.com/api/v3/urls", headers={"x-apikey": vt_key}, data={"url": url})
            if res.status_code == 200:
                await interaction.followup.send(f"🔍 Scanned `{url}` successfully via VirusTotal!")
            else:
                await interaction.followup.send("❌ Could not scan URL.")
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))

# cogs/utilities.py
import os
import base64
import requests
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View

class VirusTotalLinkView(View):
    def __init__(self, vt_url: str):
        super().__init__(timeout=None)
        self.add_item(Button(label="Open Full VirusTotal Report", url=vt_url, style=discord.ButtonStyle.link, emoji="🔗"))

class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="scan_url", description="Check a URL or domain against VirusTotal for security threats")
    @app_commands.describe(url="The web link or domain you want to check")
    async def scan_url(self, interaction: discord.Interaction, url: str):
        vt_key = os.getenv("VIRUSTOTAL_API_KEY")
        if not vt_key:
            return await interaction.response.send_message("❌ VirusTotal API key is missing in environment variables.", ephemeral=True)

        await interaction.response.defer(thinking=True)

        try:
            url_id = base64.urlsafe_b64encode(url.encode()).decode().strip("=")
            headers = {"x-apikey": vt_key}
            response = requests.get(f"https://www.virustotal.com/api/v3/urls/{url_id}", headers=headers)

            if response.status_code == 200:
                stats = response.json()['data']['attributes']['last_analysis_stats']
                malicious = stats.get('malicious', 0)
                suspicious = stats.get('suspicious', 0)
                harmless = stats.get('harmless', 0)

                color = discord.Color.red() if malicious > 0 else (discord.Color.gold() if suspicious > 0 else discord.Color.green())
                
                embed = discord.Embed(title="🛡️ VirusTotal Scan Results", color=color)
                embed.add_field(name="URL", value=f"`{url}`", inline=False)
                embed.add_field(name="🚨 Malicious", value=str(malicious), inline=True)
                embed.add_field(name="⚠️ Suspicious", value=str(suspicious), inline=True)
                embed.add_field(name="✅ Clean", value=str(harmless), inline=True)

                vt_web_link = f"https://www.virustotal.com/gui/url/{url_id}"
                await interaction.followup.send(embed=embed, view=VirusTotalLinkView(vt_web_link))
            else:
                await interaction.followup.send(f"⚠️ Link not found in database (Code {response.status_code}).")
        except Exception as e:
            await interaction.followup.send(f"❌ Scan error: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCog(bot))

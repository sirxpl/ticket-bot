# cogs/utilities.py
import os
import random
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

COFFEE_ROASTS = ["Light Roast", "Medium Roast", "Dark Roast", "Espresso Roast"]
COFFEE_STYLES = [
    "Black Coffee", "Latte", "Cappuccino", "Americano", "Mocha",
    "Flat White", "Cold Brew", "Iced Caramel Macchiato",
]
COFFEE_EXTRAS = [
    "a dash of cinnamon", "extra foam", "a swirl of caramel", "oat milk",
    "a shot of vanilla syrup", "whipped cream on top", "no sugar, just how you like it",
    "a sprinkle of cocoa powder",
]
COFFEE_FLAVOR_TEXT = [
    "Fresh off the machine and still steaming ☕",
    "Brewed slow and poured with care.",
    "Straight from the bean to your cup.",
    "Smells amazing already, doesn't it?",
    "Careful, it's hot!",
]

class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="coffee", description="Brew a coffee and have it delivered straight to your DMs")
    @app_commands.describe(user="Optional: brew a coffee for someone else instead of yourself")
    async def coffee(self, interaction: discord.Interaction, user: discord.Member = None):
        recipient = user or interaction.user
        gifting = recipient.id != interaction.user.id

        style = random.choice(COFFEE_STYLES)
        roast = random.choice(COFFEE_ROASTS)
        extra = random.choice(COFFEE_EXTRAS)
        flavor_text = random.choice(COFFEE_FLAVOR_TEXT)

        embed = discord.Embed(
            title="☕ Your Coffee Is Ready!",
            description=flavor_text,
            color=discord.Color.from_rgb(111, 78, 55),
        )
        embed.add_field(name="Style", value=style, inline=True)
        embed.add_field(name="Roast", value=roast, inline=True)
        embed.add_field(name="Extras", value=extra.capitalize(), inline=False)
        if gifting:
            embed.set_footer(text=f"Brewed for you by {interaction.user}")
        else:
            embed.set_footer(text="Enjoy! ☕")

        try:
            await recipient.send(embed=embed)
            dm_delivered = True
        except discord.Forbidden:
            dm_delivered = False

        if gifting:
            if dm_delivered:
                await interaction.response.send_message(
                    f"☕ {interaction.user.mention} brewed a **{style}** for {recipient.mention} and delivered it to their DMs!"
                )
            else:
                await interaction.response.send_message(
                    f"☕ {interaction.user.mention} brewed a **{style}** for {recipient.mention}, but their DMs are closed — here it is instead:",
                    embed=embed,
                )
        else:
            if dm_delivered:
                await interaction.response.send_message(
                    "☕ Brewing your coffee now... check your DMs!", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "☕ Your coffee's ready, but your DMs are closed — here it is instead:",
                    embed=embed,
                    ephemeral=True,
                )

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

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
COFFEE_MENU_ITEMS = [
    {"label": "Black Coffee", "emoji": "☕"},
    {"label": "Latte", "emoji": "🥛"},
    {"label": "Cappuccino", "emoji": "🍮"},
    {"label": "Americano", "emoji": "💧"},
    {"label": "Mocha", "emoji": "🍫"},
    {"label": "Flat White", "emoji": "🤍"},
    {"label": "Cold Brew", "emoji": "🧊"},
    {"label": "Iced Caramel Macchiato", "emoji": "🍯"},
]
COFFEE_STYLES = [item["label"] for item in COFFEE_MENU_ITEMS]
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


def roll_coffee_order(style: str):
    return {
        "style": style,
        "roast": random.choice(COFFEE_ROASTS),
        "extra": random.choice(COFFEE_EXTRAS),
        "flavor_text": random.choice(COFFEE_FLAVOR_TEXT),
    }


def build_coffee_embed(order: dict, gifted_by: discord.abc.User = None) -> discord.Embed:
    """Classic-embed version of the coffee delivery - always available,
    used as the fallback when Components V2 isn't supported."""
    embed = discord.Embed(
        title="☕ Your Coffee Is Ready!",
        description=order["flavor_text"],
        color=discord.Color.from_rgb(111, 78, 55),
    )
    embed.add_field(name="Style", value=order["style"], inline=True)
    embed.add_field(name="Roast", value=order["roast"], inline=True)
    embed.add_field(name="Extras", value=order["extra"].capitalize(), inline=False)
    embed.set_footer(text=f"Brewed for you by {gifted_by}" if gifted_by else "Enjoy! ☕")
    return embed


def build_coffee_delivery_view(order: dict, gifted_by: discord.abc.User = None):
    """Components V2 version of the coffee delivery. Returns None if the
    running discord.py doesn't support Components V2 yet, so callers can
    fall back to build_coffee_embed() instead."""
    try:
        layout_cls = getattr(discord.ui, "LayoutView", None)
        container_cls = getattr(discord.ui, "Container", None)
        text_display_cls = getattr(discord.ui, "TextDisplay", None)
        separator_cls = getattr(discord.ui, "Separator", None)
        if not (layout_cls and container_cls and text_display_cls):
            return None

        footer = f"-# Brewed for you by {gifted_by}" if gifted_by else "-# Enjoy! ☕"

        class CoffeeDeliveryView(layout_cls):
            def __init__(self):
                super().__init__()
                container = container_cls(accent_color=discord.Color.from_rgb(111, 78, 55))
                container.add_item(text_display_cls("### ☕ Your Coffee Is Ready!"))
                container.add_item(text_display_cls(order["flavor_text"]))
                if separator_cls:
                    container.add_item(separator_cls())
                container.add_item(
                    text_display_cls(
                        f"**Style:** {order['style']}\n"
                        f"**Roast:** {order['roast']}\n"
                        f"**Extras:** {order['extra'].capitalize()}"
                    )
                )
                if separator_cls:
                    container.add_item(separator_cls())
                container.add_item(text_display_cls(footer))
                self.add_item(container)

        return CoffeeDeliveryView()
    except Exception:
        return None


async def deliver_coffee(recipient: discord.abc.User, order: dict, gifted_by: discord.abc.User = None) -> bool:
    """Sends the coffee to the recipient's DMs, preferring Components V2
    and falling back to a classic embed. Returns True if delivered."""
    view = build_coffee_delivery_view(order, gifted_by)
    try:
        if view is not None:
            await recipient.send(view=view)
        else:
            await recipient.send(embed=build_coffee_embed(order, gifted_by))
        return True
    except discord.Forbidden:
        return False


def build_coffee_menu_view(on_order):
    """Interactive Components V2 coffee menu with one button per style.
    `on_order(interaction, style)` is called when a button is clicked.
    Returns None if Components V2 isn't supported (or anything about
    building it goes wrong), so callers can fall back to a classic view."""
    try:
        layout_cls = getattr(discord.ui, "LayoutView", None)
        container_cls = getattr(discord.ui, "Container", None)
        text_display_cls = getattr(discord.ui, "TextDisplay", None)
        separator_cls = getattr(discord.ui, "Separator", None)
        action_row_cls = getattr(discord.ui, "ActionRow", None)
        if not (layout_cls and container_cls and text_display_cls and action_row_cls):
            return None

        class CoffeeMenuView(layout_cls):
            def __init__(self):
                super().__init__(timeout=180)
                container = container_cls(accent_color=discord.Color.from_rgb(111, 78, 55))
                container.add_item(text_display_cls("### ☕ Coffee Menu\nPick your order below:"))
                if separator_cls:
                    container.add_item(separator_cls())

                for i in range(0, len(COFFEE_MENU_ITEMS), 5):
                    row = action_row_cls()
                    for item in COFFEE_MENU_ITEMS[i:i + 5]:
                        btn = discord.ui.Button(
                            label=item["label"],
                            emoji=item["emoji"],
                            style=discord.ButtonStyle.secondary,
                        )

                        async def _callback(inter: discord.Interaction, style=item["label"]):
                            await on_order(inter, style)

                        btn.callback = _callback
                        row.add_item(btn)
                    container.add_item(row)

                self.add_item(container)

        return CoffeeMenuView()
    except Exception:
        return None


class CoffeeMenuFallbackView(View):
    """Classic-View fallback menu (plain buttons under an embed) for when
    Components V2 isn't supported."""
    def __init__(self, on_order):
        super().__init__(timeout=180)
        for item in COFFEE_MENU_ITEMS:
            btn = Button(label=item["label"], emoji=item["emoji"], style=discord.ButtonStyle.secondary)

            async def _callback(inter: discord.Interaction, style=item["label"]):
                await on_order(inter, style)

            btn.callback = _callback
            self.add_item(btn)


class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="coffee", description="Brew a random coffee and have it delivered straight to your DMs")
    @app_commands.describe(user="Optional: brew a coffee for someone else instead of yourself")
    async def coffee(self, interaction: discord.Interaction, user: discord.Member = None):
        recipient = user or interaction.user
        gifting = recipient.id != interaction.user.id

        order = roll_coffee_order(random.choice(COFFEE_STYLES))
        dm_delivered = await deliver_coffee(recipient, order, gifted_by=interaction.user if gifting else None)

        if gifting:
            if dm_delivered:
                await interaction.response.send_message(
                    f"☕ {interaction.user.mention} brewed a **{order['style']}** for {recipient.mention} and delivered it to their DMs!"
                )
            else:
                await interaction.response.send_message(
                    f"☕ {interaction.user.mention} brewed a **{order['style']}** for {recipient.mention}, but their DMs are closed — here it is instead:",
                    embed=build_coffee_embed(order, gifted_by=interaction.user),
                )
        else:
            if dm_delivered:
                await interaction.response.send_message(
                    "☕ Brewing your coffee now... check your DMs!", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "☕ Your coffee's ready, but your DMs are closed — here it is instead:",
                    embed=build_coffee_embed(order),
                    ephemeral=True,
                )

    @app_commands.command(name="coffeemenu", description="Open an interactive coffee menu and pick exactly what you want")
    @app_commands.describe(user="Optional: order for someone else instead of yourself")
    async def coffeemenu(self, interaction: discord.Interaction, user: discord.Member = None):
        recipient = user or interaction.user
        gifting = recipient.id != interaction.user.id

        async def on_order(inter: discord.Interaction, style: str):
            order = roll_coffee_order(style)
            dm_delivered = await deliver_coffee(recipient, order, gifted_by=inter.user if gifting else None)

            if gifting:
                if dm_delivered:
                    msg = f"☕ Order placed! A **{style}** is brewing for {recipient.mention} and on its way to their DMs."
                else:
                    msg = f"☕ Order placed for {recipient.mention}, but their DMs are closed."
            else:
                if dm_delivered:
                    msg = f"☕ Order placed! Your **{style}** is on its way to your DMs."
                else:
                    msg = "☕ Order placed, but your DMs are closed."

            await inter.response.edit_message(content=msg, view=None, embed=None)

        view = build_coffee_menu_view(on_order)
        if view is not None:
            await interaction.response.send_message(view=view, ephemeral=True)
        else:
            embed = discord.Embed(
                title="☕ Coffee Menu",
                description="Pick your order below:",
                color=discord.Color.from_rgb(111, 78, 55),
            )
            await interaction.response.send_message(
                embed=embed, view=CoffeeMenuFallbackView(on_order), ephemeral=True
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

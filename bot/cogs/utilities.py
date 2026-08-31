# cogs/utilities.py
import os
import random
import base64
import requests
import discord
from typing import Literal
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View

from utils.storage import get_dashboard_base_url, get_coffee_dm_enabled, set_coffee_dm_enabled

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


def build_simple_v2_text(text: str):
    """A minimal one-line Components V2 message (just a TextDisplay inside
    a LayoutView). Used to edit an existing Components V2 message with new
    text - you can NEVER edit a V2 message back to plain content=, that's
    permanently disabled for it once sent, so edits must stay in
    components-land too. Returns None if V2 isn't supported."""
    layout_cls = getattr(discord.ui, "LayoutView", None)
    text_display_cls = getattr(discord.ui, "TextDisplay", None)
    if not (layout_cls and text_display_cls):
        return None

    class SimpleTextView(layout_cls):
        def __init__(self):
            super().__init__()
            self.add_item(text_display_cls(text))

    return SimpleTextView()


def components_v2_supported() -> bool:
    """Cheap check for whether the running discord.py has Components V2
    classes available, without constructing anything."""
    return all(
        getattr(discord.ui, name, None)
        for name in ("LayoutView", "Container", "TextDisplay", "ActionRow")
    )


SITE_ABOUT_TEXT = (
    "This bot runs a full support-ticket system for the server — open a "
    "ticket, get matched with the carry team, and everything's tracked on "
    "a web dashboard staff can review anytime."
)

def _site_links():
    base = get_dashboard_base_url()
    return [
        {"label": "Dashboard", "emoji": "🔗", "url": f"{base}/"},
        {"label": "Docs", "emoji": "📘", "url": f"{base}/docs"},
        {"label": "Rules & Regulations", "emoji": "📋", "url": f"{base}/rules"},
        {"label": "Carry Guidelines", "emoji": "📖", "url": f"{base}/guidelines"},
        {"label": "Privacy Policy", "emoji": "🔒", "url": f"{base}/privacy"},
        {"label": "Status", "emoji": "🟢", "url": f"{base}/status"},
    ]


def build_panel_view():
    """Components V2 layout introducing the site with a link button per
    page. Returns None if Components V2 isn't supported."""
    try:
        layout_cls = getattr(discord.ui, "LayoutView", None)
        container_cls = getattr(discord.ui, "Container", None)
        text_display_cls = getattr(discord.ui, "TextDisplay", None)
        separator_cls = getattr(discord.ui, "Separator", None)
        action_row_cls = getattr(discord.ui, "ActionRow", None)
        if not (layout_cls and container_cls and text_display_cls and action_row_cls):
            return None

        links = _site_links()

        class PanelView(layout_cls):
            def __init__(self):
                super().__init__(timeout=None)
                container = container_cls(accent_color=discord.Color.blurple())
                container.add_item(text_display_cls("### 🎫 Carry Ticket Bot"))
                container.add_item(text_display_cls(SITE_ABOUT_TEXT))
                if separator_cls:
                    container.add_item(separator_cls())

                for i in range(0, len(links), 5):
                    row = action_row_cls()
                    for link in links[i:i + 5]:
                        row.add_item(
                            Button(
                                label=link["label"],
                                emoji=link["emoji"],
                                url=link["url"],
                                style=discord.ButtonStyle.link,
                            )
                        )
                    container.add_item(row)

                self.add_item(container)

        return PanelView()
    except Exception:
        return None


def build_panel_embed_and_view():
    """Classic embed + link-button View fallback for when Components V2
    isn't supported."""
    embed = discord.Embed(
        title="🎫 Carry Ticket Bot",
        description=SITE_ABOUT_TEXT,
        color=discord.Color.blurple(),
    )
    view = View(timeout=None)
    for link in _site_links():
        view.add_item(
            Button(label=link["label"], emoji=link["emoji"], url=link["url"], style=discord.ButtonStyle.link)
        )
    return embed, view


def build_coffee_menu_view(on_order, on_custom_order):
    """Interactive Components V2 coffee menu with one button per style, plus
    a "Custom" button that opens a modal for a fully custom order.
    `on_order(interaction, style)` is called when a preset button is
    clicked; `on_custom_order` is passed straight to CustomCoffeeModal.
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

                if separator_cls:
                    container.add_item(separator_cls())
                custom_row = action_row_cls()
                custom_btn = discord.ui.Button(label="Custom", emoji="📝", style=discord.ButtonStyle.primary)

                async def _custom_callback(inter: discord.Interaction):
                    await inter.response.send_modal(CustomCoffeeModal(on_custom_order))

                custom_btn.callback = _custom_callback
                custom_row.add_item(custom_btn)
                container.add_item(custom_row)

                self.add_item(container)

        return CoffeeMenuView()
    except Exception:
        return None


class CoffeeMenuFallbackView(View):
    """Classic-View fallback menu (plain buttons under an embed) for when
    Components V2 isn't supported. Includes the same "Custom" button as the
    V2 menu, opening a modal for a fully custom order."""
    def __init__(self, on_order, on_custom_order):
        super().__init__(timeout=180)
        for item in COFFEE_MENU_ITEMS:
            btn = Button(label=item["label"], emoji=item["emoji"], style=discord.ButtonStyle.secondary)

            async def _callback(inter: discord.Interaction, style=item["label"]):
                await on_order(inter, style)

            btn.callback = _callback
            self.add_item(btn)

        custom_btn = Button(label="Custom", emoji="📝", style=discord.ButtonStyle.primary)

        async def _custom_callback(inter: discord.Interaction):
            await inter.response.send_modal(CustomCoffeeModal(on_custom_order))

        custom_btn.callback = _custom_callback
        self.add_item(custom_btn)


class CustomCoffeeModal(discord.ui.Modal, title="Custom Coffee Order"):
    """Prompts for style/roast/extras so someone can build an order that
    isn't one of the preset menu buttons. Roast and extras are optional —
    left blank, they're rolled the same way a preset order's would be."""
    style_input = discord.ui.TextInput(
        label="Style", placeholder="e.g. Iced Vanilla Latte", max_length=100, required=True
    )
    roast_input = discord.ui.TextInput(
        label="Roast", placeholder="e.g. Medium Roast (leave blank for a surprise)",
        max_length=100, required=False,
    )
    extras_input = discord.ui.TextInput(
        label="Extras", placeholder="e.g. oat milk, extra shot (leave blank for a surprise)",
        max_length=200, required=False,
    )

    def __init__(self, on_custom_order):
        super().__init__()
        self._on_custom_order = on_custom_order

    async def on_submit(self, interaction: discord.Interaction):
        style = str(self.style_input.value).strip()
        roast = str(self.roast_input.value).strip() or random.choice(COFFEE_ROASTS)
        extras = str(self.extras_input.value).strip() or random.choice(COFFEE_EXTRAS)
        await self._on_custom_order(interaction, style, roast, extras)


class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _make_on_order(self, recipient: discord.abc.User, invoker: discord.abc.User, gifting: bool, is_v2: bool):
        """Builds the button-click handler for a coffee menu. Always edits the
        original (ephemeral) menu message, so nothing about the order — or
        whether delivery succeeded — is ever posted where anyone else can see it.

        is_v2 matters because once a message is sent using Components V2, it
        can NEVER be edited back to plain content= — that's permanently
        disabled for that message. So V2 menus must be edited with another
        V2 view, while the classic fallback menu edits with plain content=.
        """
        async def on_order(inter: discord.Interaction, style: str):
            if gifting and not get_coffee_dm_enabled(recipient.id):
                msg = f"🚫 {recipient.mention} has disabled receiving coffee gifts."
                if is_v2:
                    await inter.response.edit_message(view=build_simple_v2_text(msg))
                else:
                    await inter.response.edit_message(content=msg, view=None, embed=None)
                return

            interim = (
                f"☕ Delivering to {recipient.mention}..." if gifting else "☕ Check your DMs!"
            )
            if is_v2:
                await inter.response.edit_message(view=build_simple_v2_text(interim))
            else:
                await inter.response.edit_message(content=interim, view=None, embed=None)

            order = roll_coffee_order(style)
            dm_delivered = await deliver_coffee(recipient, order, gifted_by=invoker if gifting else None)

            if gifting:
                if dm_delivered:
                    msg = f"☕ Order placed! A **{style}** is brewing for {recipient.mention} and on its way to their DMs."
                else:
                    msg = f"❌ Couldn't deliver the coffee — {recipient.mention} has their DMs closed."
            else:
                if dm_delivered:
                    msg = f"☕ Order placed! Your **{style}** is on its way to your DMs."
                else:
                    msg = "❌ Couldn't deliver your coffee — your DMs are closed."

            if is_v2:
                await inter.edit_original_response(view=build_simple_v2_text(msg))
            else:
                await inter.edit_original_response(content=msg, view=None, embed=None)

        return on_order

    def _make_on_custom_order(self, recipient: discord.abc.User, invoker: discord.abc.User, gifting: bool, is_v2: bool):
        """Same as _make_on_order, but for the "Custom" modal — takes an
        already-built style/roast/extras instead of rolling one from a
        preset. The modal submission is itself the first response to this
        interaction, so the edit_message/edit_original_response sequence
        below works the same way as the button flow."""
        async def on_custom_order(inter: discord.Interaction, style: str, roast: str, extras: str):
            if gifting and not get_coffee_dm_enabled(recipient.id):
                msg = f"🚫 {recipient.mention} has disabled receiving coffee gifts."
                if is_v2:
                    await inter.response.edit_message(view=build_simple_v2_text(msg))
                else:
                    await inter.response.edit_message(content=msg, view=None, embed=None)
                return

            interim = (
                f"☕ Delivering to {recipient.mention}..." if gifting else "☕ Check your DMs!"
            )
            if is_v2:
                await inter.response.edit_message(view=build_simple_v2_text(interim))
            else:
                await inter.response.edit_message(content=interim, view=None, embed=None)

            order = {
                "style": style,
                "roast": roast,
                "extra": extras,
                "flavor_text": random.choice(COFFEE_FLAVOR_TEXT),
            }
            dm_delivered = await deliver_coffee(recipient, order, gifted_by=invoker if gifting else None)

            if gifting:
                if dm_delivered:
                    msg = f"☕ Order placed! A custom **{style}** is brewing for {recipient.mention} and on its way to their DMs."
                else:
                    msg = f"❌ Couldn't deliver the coffee — {recipient.mention} has their DMs closed."
            else:
                if dm_delivered:
                    msg = f"☕ Order placed! Your custom **{style}** is on its way to your DMs."
                else:
                    msg = "❌ Couldn't deliver your coffee — your DMs are closed."

            if is_v2:
                await inter.edit_original_response(view=build_simple_v2_text(msg))
            else:
                await inter.edit_original_response(content=msg, view=None, embed=None)

        return on_custom_order

    async def _send_coffee_menu(self, interaction: discord.Interaction, recipient: discord.abc.User):
        gifting = recipient.id != interaction.user.id
        title = f"Pick a coffee for {recipient.mention}:" if gifting else "Pick your order below:"

        # Build with is_v2=True only tentatively - confirm the view actually
        # got built before trusting that, so on_order's edit style always
        # matches how the message was really sent, never just how we hoped.
        view = None
        if components_v2_supported():
            on_order = self._make_on_order(recipient, interaction.user, gifting, is_v2=True)
            on_custom_order = self._make_on_custom_order(recipient, interaction.user, gifting, is_v2=True)
            view = build_coffee_menu_view(on_order, on_custom_order)

        if view is not None:
            await interaction.response.send_message(view=view, ephemeral=True)
        else:
            on_order = self._make_on_order(recipient, interaction.user, gifting, is_v2=False)
            on_custom_order = self._make_on_custom_order(recipient, interaction.user, gifting, is_v2=False)
            embed = discord.Embed(
                title="☕ Coffee Menu",
                description=title,
                color=discord.Color.from_rgb(111, 78, 55),
            )
            await interaction.response.send_message(
                embed=embed, view=CoffeeMenuFallbackView(on_order, on_custom_order), ephemeral=True
            )

    @app_commands.command(name="panel", description="Get a link to the dashboard and site")
    async def panel(self, interaction: discord.Interaction):
        view = build_panel_view()
        if view is not None:
            await interaction.response.send_message(view=view, ephemeral=True)
        else:
            embed, fallback_view = build_panel_embed_and_view()
            await interaction.response.send_message(embed=embed, view=fallback_view, ephemeral=True)

    @app_commands.command(name="coffee", description="Open the coffee menu and order something, delivered straight to DMs")
    @app_commands.describe(user="Optional: order a coffee for someone else instead of yourself")
    async def coffee(self, interaction: discord.Interaction, user: discord.Member = None):
        recipient = user or interaction.user
        await self._send_coffee_menu(interaction, recipient)

    @app_commands.command(name="coffeesettings", description="Control whether other people can gift you coffee via DM")
    @app_commands.describe(dms="Whether other people can send you a gifted coffee straight to your DMs")
    async def coffeesettings(self, interaction: discord.Interaction, dms: Literal["enable", "disable"]):
        enabled = dms == "enable"
        set_coffee_dm_enabled(interaction.user.id, enabled)

        if enabled:
            msg = (
                "☕ Coffee gifts are now **enabled** — other people can send you a coffee "
                "straight to your DMs. Ordering for yourself always works regardless of this setting."
            )
        else:
            msg = (
                "🚫 Coffee gifts are now **disabled** — other people won't be able to DM you a "
                "gifted coffee. You can still order coffee for yourself anytime."
            )

        if components_v2_supported():
            view = build_simple_v2_text(f"### ☕ Coffee Settings\n{msg}")
            if view is not None:
                await interaction.response.send_message(view=view, ephemeral=True)
                return

        embed = discord.Embed(title="☕ Coffee Settings", description=msg, color=discord.Color.from_rgb(111, 78, 55))
        await interaction.response.send_message(embed=embed, ephemeral=True)

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

    @app_commands.command(name="say", description="Send a message as the bot")
    @app_commands.describe(
        message="The message you want the bot to send",
        message_link="Optional message link to reply to"
    )
    async def say(
        self,
        interaction: discord.Interaction,
        message: str,
        message_link: str = None
    ):
        # If a message link was provided, try to reply to that message
        if message_link:
            try:
                # Expected Discord message link:
                # https://discord.com/channels/GUILD_ID/CHANNEL_ID/MESSAGE_ID
                parts = message_link.rstrip("/").split("/")

                if len(parts) < 3:
                    raise ValueError("Invalid Discord message link.")

                channel_id = int(parts[-2])
                message_id = int(parts[-1])

                channel = self.bot.get_channel(channel_id)

                if channel is None:
                    channel = await self.bot.fetch_channel(channel_id)

                target_message = await channel.fetch_message(message_id)

                await target_message.reply(message)
                await interaction.response.send_message(
                    "✅ Message sent as a reply.",
                    ephemeral=True
                )
                return

            except (ValueError, discord.NotFound, discord.Forbidden, discord.HTTPException):
                await interaction.response.send_message(
                    "❌ I couldn't use that message link. Make sure it's a valid Discord message link that I can access.",
                    ephemeral=True
                )
                return

        # No message link = normal message
        await interaction.channel.send(message)

        await interaction.response.send_message(
            "✅ Message sent.",
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCog(bot))

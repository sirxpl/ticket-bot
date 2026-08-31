# cogs/utilities.py

import os
import random
import base64
import asyncio
from typing import Literal
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View

from utils.storage import (
    get_dashboard_base_url,
    get_coffee_dm_enabled,
    set_coffee_dm_enabled,
)
from utils.access import is_admin


# ============================================================
# VirusTotal
# ============================================================

class VirusTotalLinkView(View):
    def __init__(self, vt_url: str):
        super().__init__(timeout=None)

        self.add_item(
            Button(
                label="Open Full VirusTotal Report",
                url=vt_url,
                style=discord.ButtonStyle.link,
                emoji="🔗",
            )
        )


# ============================================================
# Coffee System
# ============================================================

COFFEE_ROASTS = [
    "Light Roast",
    "Medium Roast",
    "Dark Roast",
    "Espresso Roast",
]

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
    "a dash of cinnamon",
    "extra foam",
    "a swirl of caramel",
    "oat milk",
    "a shot of vanilla syrup",
    "whipped cream on top",
    "no sugar, just how you like it",
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


def build_coffee_embed(
    order: dict,
    gifted_by: discord.abc.User = None,
) -> discord.Embed:
    """Classic embed fallback for coffee delivery."""

    embed = discord.Embed(
        title="☕ Your Coffee Is Ready!",
        description=order["flavor_text"],
        color=discord.Color.from_rgb(111, 78, 55),
    )

    embed.add_field(
        name="Style",
        value=order["style"],
        inline=True,
    )

    embed.add_field(
        name="Roast",
        value=order["roast"],
        inline=True,
    )

    embed.add_field(
        name="Extras",
        value=order["extra"].capitalize(),
        inline=False,
    )

    embed.set_footer(
        text=(
            f"Brewed for you by {gifted_by}"
            if gifted_by
            else "Enjoy! ☕"
        )
    )

    return embed


def build_coffee_delivery_view(
    order: dict,
    gifted_by: discord.abc.User = None,
):
    """Build Components V2 coffee delivery view.

    Returns None if Components V2 is unavailable.
    """

    try:
        layout_cls = getattr(discord.ui, "LayoutView", None)
        container_cls = getattr(discord.ui, "Container", None)
        text_display_cls = getattr(discord.ui, "TextDisplay", None)
        separator_cls = getattr(discord.ui, "Separator", None)

        if not (layout_cls and container_cls and text_display_cls):
            return None

        footer = (
            f"-# Brewed for you by {gifted_by}"
            if gifted_by
            else "-# Enjoy! ☕"
        )

        class CoffeeDeliveryView(layout_cls):
            def __init__(self):
                super().__init__()

                container = container_cls(
                    accent_color=discord.Color.from_rgb(111, 78, 55)
                )

                container.add_item(
                    text_display_cls("### ☕ Your Coffee Is Ready!")
                )

                container.add_item(
                    text_display_cls(order["flavor_text"])
                )

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


async def deliver_coffee(
    recipient: discord.abc.User,
    order: dict,
    gifted_by: discord.abc.User = None,
) -> bool:
    """Send coffee to a user's DMs."""

    view = build_coffee_delivery_view(order, gifted_by)

    try:
        if view is not None:
            await recipient.send(view=view)
        else:
            await recipient.send(
                embed=build_coffee_embed(order, gifted_by)
            )

        return True

    except discord.Forbidden:
        return False

    except discord.HTTPException:
        return False


def build_simple_v2_text(text: str):
    """Build a minimal Components V2 text view."""

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
    """Check whether the installed discord.py supports Components V2."""

    return all(
        getattr(discord.ui, name, None)
        for name in (
            "LayoutView",
            "Container",
            "TextDisplay",
            "ActionRow",
        )
    )


# ============================================================
# Dashboard / Panel
# ============================================================

SITE_ABOUT_TEXT = (
    "This bot runs a full support-ticket system for the server — open a "
    "ticket, get matched with the carry team, and everything's tracked on "
    "a web dashboard staff can review anytime."
)


def _site_links():
    base = get_dashboard_base_url().rstrip("/")

    return [
        {
            "label": "Dashboard",
            "emoji": "🔗",
            "url": f"{base}/",
        },
        {
            "label": "Docs",
            "emoji": "📘",
            "url": f"{base}/docs",
        },
        {
            "label": "Rules & Regulations",
            "emoji": "📋",
            "url": f"{base}/rules",
        },
        {
            "label": "Carry Guidelines",
            "emoji": "📖",
            "url": f"{base}/guidelines",
        },
        {
            "label": "Privacy Policy",
            "emoji": "🔒",
            "url": f"{base}/privacy",
        },
        {
            "label": "Status",
            "emoji": "🟢",
            "url": f"{base}/status",
        },
    ]


def build_panel_view():
    """Build Components V2 dashboard panel."""

    try:
        layout_cls = getattr(discord.ui, "LayoutView", None)
        container_cls = getattr(discord.ui, "Container", None)
        text_display_cls = getattr(discord.ui, "TextDisplay", None)
        separator_cls = getattr(discord.ui, "Separator", None)
        action_row_cls = getattr(discord.ui, "ActionRow", None)

        if not (
            layout_cls
            and container_cls
            and text_display_cls
            and action_row_cls
        ):
            return None

        links = _site_links()

        class PanelView(layout_cls):
            def __init__(self):
                super().__init__(timeout=None)

                container = container_cls(
                    accent_color=discord.Color.blurple()
                )

                container.add_item(
                    text_display_cls("### 🎫 Carry Ticket Bot")
                )

                container.add_item(
                    text_display_cls(SITE_ABOUT_TEXT)
                )

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
    """Classic embed + button fallback."""

    embed = discord.Embed(
        title="🎫 Carry Ticket Bot",
        description=SITE_ABOUT_TEXT,
        color=discord.Color.blurple(),
    )

    view = View(timeout=None)

    for link in _site_links():
        view.add_item(
            Button(
                label=link["label"],
                emoji=link["emoji"],
                url=link["url"],
                style=discord.ButtonStyle.link,
            )
        )

    return embed, view


# ============================================================
# Coffee Menu
# ============================================================

def build_coffee_menu_view(on_order, on_custom_order):
    """Build Components V2 coffee menu."""

    try:
        layout_cls = getattr(discord.ui, "LayoutView", None)
        container_cls = getattr(discord.ui, "Container", None)
        text_display_cls = getattr(discord.ui, "TextDisplay", None)
        separator_cls = getattr(discord.ui, "Separator", None)
        action_row_cls = getattr(discord.ui, "ActionRow", None)

        if not (
            layout_cls
            and container_cls
            and text_display_cls
            and action_row_cls
        ):
            return None

        class CoffeeMenuView(layout_cls):
            def __init__(self):
                super().__init__(timeout=180)

                container = container_cls(
                    accent_color=discord.Color.from_rgb(111, 78, 55)
                )

                container.add_item(
                    text_display_cls(
                        "### ☕ Coffee Menu\nPick your order below:"
                    )
                )

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

                        async def _callback(
                            inter: discord.Interaction,
                            style=item["label"],
                        ):
                            await on_order(inter, style)

                        btn.callback = _callback
                        row.add_item(btn)

                    container.add_item(row)

                if separator_cls:
                    container.add_item(separator_cls())

                custom_row = action_row_cls()

                custom_btn = discord.ui.Button(
                    label="Custom",
                    emoji="📝",
                    style=discord.ButtonStyle.primary,
                )

                async def _custom_callback(
                    inter: discord.Interaction,
                ):
                    await inter.response.send_modal(
                        CustomCoffeeModal(on_custom_order)
                    )

                custom_btn.callback = _custom_callback

                custom_row.add_item(custom_btn)
                container.add_item(custom_row)

                self.add_item(container)

        return CoffeeMenuView()

    except Exception:
        return None


class CoffeeMenuFallbackView(View):
    """Classic View fallback."""

    def __init__(self, on_order, on_custom_order):
        super().__init__(timeout=180)

        for item in COFFEE_MENU_ITEMS:
            btn = Button(
                label=item["label"],
                emoji=item["emoji"],
                style=discord.ButtonStyle.secondary,
            )

            async def _callback(
                inter: discord.Interaction,
                style=item["label"],
            ):
                await on_order(inter, style)

            btn.callback = _callback
            self.add_item(btn)

        custom_btn = Button(
            label="Custom",
            emoji="📝",
            style=discord.ButtonStyle.primary,
        )

        async def _custom_callback(
            inter: discord.Interaction,
        ):
            await inter.response.send_modal(
                CustomCoffeeModal(on_custom_order)
            )

        custom_btn.callback = _custom_callback
        self.add_item(custom_btn)


class CustomCoffeeModal(
    discord.ui.Modal,
    title="Custom Coffee Order",
):
    """Modal for custom coffee orders."""

    style_input = discord.ui.TextInput(
        label="Style",
        placeholder="e.g. Iced Vanilla Latte",
        max_length=100,
        required=True,
    )

    roast_input = discord.ui.TextInput(
        label="Roast",
        placeholder="e.g. Medium Roast (leave blank for a surprise)",
        max_length=100,
        required=False,
    )

    extras_input = discord.ui.TextInput(
        label="Extras",
        placeholder="e.g. oat milk, extra shot (leave blank for a surprise)",
        max_length=200,
        required=False,
    )

    def __init__(self, on_custom_order):
        super().__init__()
        self._on_custom_order = on_custom_order

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        style = str(self.style_input.value).strip()

        roast = (
            str(self.roast_input.value).strip()
            or random.choice(COFFEE_ROASTS)
        )

        extras = (
            str(self.extras_input.value).strip()
            or random.choice(COFFEE_EXTRAS)
        )

        await self._on_custom_order(
            interaction,
            style,
            roast,
            extras,
        )


# ============================================================
# Utility Cog
# ============================================================

class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --------------------------------------------------------
    # Coffee handlers
    # --------------------------------------------------------

    def _make_on_order(
        self,
        recipient: discord.abc.User,
        invoker: discord.abc.User,
        gifting: bool,
        is_v2: bool,
    ):
        async def on_order(
            inter: discord.Interaction,
            style: str,
        ):
            if gifting and not get_coffee_dm_enabled(recipient.id):
                msg = (
                    f"🚫 {recipient.mention} has disabled "
                    "receiving coffee gifts."
                )

                if is_v2:
                    await inter.response.edit_message(
                        view=build_simple_v2_text(msg)
                    )
                else:
                    await inter.response.edit_message(
                        content=msg,
                        view=None,
                        embed=None,
                    )

                return

            interim = (
                f"☕ Delivering to {recipient.mention}..."
                if gifting
                else "☕ Check your DMs!"
            )

            if is_v2:
                await inter.response.edit_message(
                    view=build_simple_v2_text(interim)
                )
            else:
                await inter.response.edit_message(
                    content=interim,
                    view=None,
                    embed=None,
                )

            order = roll_coffee_order(style)

            dm_delivered = await deliver_coffee(
                recipient,
                order,
                gifted_by=invoker if gifting else None,
            )

            if gifting:
                if dm_delivered:
                    msg = (
                        f"☕ Order placed! A **{style}** is brewing for "
                        f"{recipient.mention} and on its way to their DMs."
                    )
                else:
                    msg = (
                        f"❌ Couldn't deliver the coffee — "
                        f"{recipient.mention} has their DMs closed."
                    )
            else:
                if dm_delivered:
                    msg = (
                        f"☕ Order placed! Your **{style}** is on "
                        "its way to your DMs."
                    )
                else:
                    msg = (
                        "❌ Couldn't deliver your coffee — "
                        "your DMs are closed."
                    )

            if is_v2:
                await inter.edit_original_response(
                    view=build_simple_v2_text(msg)
                )
            else:
                await inter.edit_original_response(
                    content=msg,
                    view=None,
                    embed=None,
                )

        return on_order

    def _make_on_custom_order(
        self,
        recipient: discord.abc.User,
        invoker: discord.abc.User,
        gifting: bool,
        is_v2: bool,
    ):
        async def on_custom_order(
            inter: discord.Interaction,
            style: str,
            roast: str,
            extras: str,
        ):
            if gifting and not get_coffee_dm_enabled(recipient.id):
                msg = (
                    f"🚫 {recipient.mention} has disabled "
                    "receiving coffee gifts."
                )

                if is_v2:
                    await inter.response.edit_message(
                        view=build_simple_v2_text(msg)
                    )
                else:
                    await inter.response.edit_message(
                        content=msg,
                        view=None,
                        embed=None,
                    )

                return

            interim = (
                f"☕ Delivering to {recipient.mention}..."
                if gifting
                else "☕ Check your DMs!"
            )

            if is_v2:
                await inter.response.edit_message(
                    view=build_simple_v2_text(interim)
                )
            else:
                await inter.response.edit_message(
                    content=interim,
                    view=None,
                    embed=None,
                )

            order = {
                "style": style,
                "roast": roast,
                "extra": extras,
                "flavor_text": random.choice(COFFEE_FLAVOR_TEXT),
            }

            dm_delivered = await deliver_coffee(
                recipient,
                order,
                gifted_by=invoker if gifting else None,
            )

            if gifting:
                if dm_delivered:
                    msg = (
                        f"☕ Order placed! A custom **{style}** is brewing "
                        f"for {recipient.mention} and on its way to their DMs."
                    )
                else:
                    msg = (
                        f"❌ Couldn't deliver the coffee — "
                        f"{recipient.mention} has their DMs closed."
                    )
            else:
                if dm_delivered:
                    msg = (
                        f"☕ Order placed! Your custom **{style}** "
                        "is on its way to your DMs."
                    )
                else:
                    msg = (
                        "❌ Couldn't deliver your coffee — "
                        "your DMs are closed."
                    )

            if is_v2:
                await inter.edit_original_response(
                    view=build_simple_v2_text(msg)
                )
            else:
                await inter.edit_original_response(
                    content=msg,
                    view=None,
                    embed=None,
                )

        return on_custom_order

    async def _send_coffee_menu(
        self,
        interaction: discord.Interaction,
        recipient: discord.abc.User,
    ):
        gifting = recipient.id != interaction.user.id

        title = (
            f"Pick a coffee for {recipient.mention}:"
            if gifting
            else "Pick your order below:"
        )

        view = None

        if components_v2_supported():
            on_order = self._make_on_order(
                recipient,
                interaction.user,
                gifting,
                is_v2=True,
            )

            on_custom_order = self._make_on_custom_order(
                recipient,
                interaction.user,
                gifting,
                is_v2=True,
            )

            view = build_coffee_menu_view(
                on_order,
                on_custom_order,
            )

        if view is not None:
            await interaction.response.send_message(
                view=view,
                ephemeral=True,
            )
            return

        on_order = self._make_on_order(
            recipient,
            interaction.user,
            gifting,
            is_v2=False,
        )

        on_custom_order = self._make_on_custom_order(
            recipient,
            interaction.user,
            gifting,
            is_v2=False,
        )

        embed = discord.Embed(
            title="☕ Coffee Menu",
            description=title,
            color=discord.Color.from_rgb(111, 78, 55),
        )

        await interaction.response.send_message(
            embed=embed,
            view=CoffeeMenuFallbackView(
                on_order,
                on_custom_order,
            ),
            ephemeral=True,
        )

    # --------------------------------------------------------
    # /panel
    # --------------------------------------------------------

    @app_commands.command(
        name="panel",
        description="Get a link to the dashboard and site",
    )
    async def panel(
        self,
        interaction: discord.Interaction,
    ):
        view = build_panel_view()

        if view is not None:
            await interaction.response.send_message(
                view=view,
                ephemeral=True,
            )
            return

        embed, fallback_view = build_panel_embed_and_view()

        await interaction.response.send_message(
            embed=embed,
            view=fallback_view,
            ephemeral=True,
        )

    # --------------------------------------------------------
    # /coffee
    # --------------------------------------------------------

    @app_commands.command(
        name="coffee",
        description=(
            "Open the coffee menu and order something, "
            "delivered straight to DMs"
        ),
    )
    @app_commands.describe(
        user=(
            "Optional: order a coffee for someone else "
            "instead of yourself"
        )
    )
    async def coffee(
        self,
        interaction: discord.Interaction,
        user: discord.Member = None,
    ):
        recipient = user or interaction.user

        await self._send_coffee_menu(
            interaction,
            recipient,
        )

    # --------------------------------------------------------
    # /coffeesettings
    # --------------------------------------------------------

    @app_commands.command(
        name="coffeesettings",
        description=(
            "Control whether other people can gift "
            "you coffee via DM"
        ),
    )
    @app_commands.describe(
        dms=(
            "Whether other people can send you "
            "a gifted coffee straight to your DMs"
        )
    )
    async def coffeesettings(
        self,
        interaction: discord.Interaction,
        dms: Literal["enable", "disable"],
    ):
        enabled = dms == "enable"

        set_coffee_dm_enabled(
            interaction.user.id,
            enabled,
        )

        if enabled:
            msg = (
                "☕ Coffee gifts are now **enabled** — other people "
                "can send you a coffee straight to your DMs. "
                "Ordering for yourself always works regardless "
                "of this setting."
            )
        else:
            msg = (
                "🚫 Coffee gifts are now **disabled** — other people "
                "won't be able to DM you a gifted coffee. "
                "You can still order coffee for yourself anytime."
            )

        if components_v2_supported():
            view = build_simple_v2_text(
                f"### ☕ Coffee Settings\n{msg}"
            )

            if view is not None:
                await interaction.response.send_message(
                    view=view,
                    ephemeral=True,
                )
                return

        embed = discord.Embed(
            title="☕ Coffee Settings",
            description=msg,
            color=discord.Color.from_rgb(111, 78, 55),
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )

    # --------------------------------------------------------
    # /scan_url
    # --------------------------------------------------------

    @app_commands.command(
        name="scan_url",
        description=(
            "Check a URL or domain against "
            "VirusTotal for security threats"
        ),
    )
    @app_commands.describe(
        url="The web link or domain you want to check",
    )
    async def scan_url(
        self,
        interaction: discord.Interaction,
        url: str,
    ):
        """Scan a URL using VirusTotal without blocking the bot."""

        vt_key = os.getenv("VIRUSTOTAL_API_KEY")

        if not vt_key:
            await interaction.response.send_message(
                "❌ VirusTotal API key is missing in environment variables.",
                ephemeral=True,
            )
            return

        # Basic URL validation.
        url = url.strip()

        if not url:
            await interaction.response.send_message(
                "❌ Please provide a URL or domain.",
                ephemeral=True,
            )
            return

        # Add https:// when a user enters a bare domain.
        if "://" not in url:
            url = f"https://{url}"

        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            await interaction.response.send_message(
                "❌ That doesn't look like a valid HTTP/HTTPS URL.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            thinking=True,
            ephemeral=True,
        )

        try:
            # VirusTotal URL identifier.
            url_id = (
                base64.urlsafe_b64encode(url.encode())
                .decode()
                .rstrip("=")
            )

            headers = {
                "x-apikey": vt_key,
                "accept": "application/json",
            }

            timeout = aiohttp.ClientTimeout(total=15)

            async with aiohttp.ClientSession(
                timeout=timeout
            ) as session:

                async with session.get(
                    f"https://www.virustotal.com/api/v3/urls/{url_id}",
                    headers=headers,
                ) as response:

                    if response.status == 200:
                        data = await response.json()

                        stats = (
                            data.get("data", {})
                            .get("attributes", {})
                            .get("last_analysis_stats", {})
                        )

                        malicious = stats.get("malicious", 0)
                        suspicious = stats.get("suspicious", 0)
                        harmless = stats.get("harmless", 0)
                        undetected = stats.get("undetected", 0)

                        if malicious > 0:
                            color = discord.Color.red()
                        elif suspicious > 0:
                            color = discord.Color.gold()
                        else:
                            color = discord.Color.green()

                        embed = discord.Embed(
                            title="🛡️ VirusTotal Scan Results",
                            color=color,
                        )

                        # Prevent an extremely long URL from breaking
                        # the embed layout.
                        display_url = url

                        if len(display_url) > 1000:
                            display_url = display_url[:997] + "..."

                        embed.add_field(
                            name="URL",
                            value=f"`{display_url}`",
                            inline=False,
                        )

                        embed.add_field(
                            name="🚨 Malicious",
                            value=str(malicious),
                            inline=True,
                        )

                        embed.add_field(
                            name="⚠️ Suspicious",
                            value=str(suspicious),
                            inline=True,
                        )

                        embed.add_field(
                            name="✅ Clean",
                            value=str(harmless),
                            inline=True,
                        )

                        embed.add_field(
                            name="❔ Undetected",
                            value=str(undetected),
                            inline=True,
                        )

                        vt_web_link = (
                            f"https://www.virustotal.com/gui/url/{url_id}"
                        )

                        await interaction.followup.send(
                            embed=embed,
                            view=VirusTotalLinkView(vt_web_link),
                            ephemeral=True,
                        )

                        return

                    if response.status == 404:
                        await interaction.followup.send(
                            "⚠️ VirusTotal doesn't currently have a "
                            "report for that URL. Try submitting/scanning "
                            "the URL on VirusTotal directly.",
                            ephemeral=True,
                        )
                        return

                    if response.status == 401:
                        await interaction.followup.send(
                            "❌ VirusTotal rejected the API key. "
                            "Check the `VIRUSTOTAL_API_KEY` environment variable.",
                            ephemeral=True,
                        )
                        return

                    if response.status == 429:
                        await interaction.followup.send(
                            "⏳ VirusTotal rate limit reached. "
                            "Please try again later.",
                            ephemeral=True,
                        )
                        return

                    await interaction.followup.send(
                        f"⚠️ VirusTotal returned HTTP {response.status}.",
                        ephemeral=True,
                    )

        except asyncio.TimeoutError:
            await interaction.followup.send(
                "⏳ VirusTotal took too long to respond. Please try again.",
                ephemeral=True,
            )

        except aiohttp.ClientError:
            await interaction.followup.send(
                "❌ I couldn't connect to VirusTotal. Please try again later.",
                ephemeral=True,
            )

        except Exception:
            # Don't expose internal exception details to Discord users.
            await interaction.followup.send(
                "❌ An unexpected error occurred while scanning the URL.",
                ephemeral=True,
            )

    # --------------------------------------------------------
    # /say
    # --------------------------------------------------------

    @app_commands.command(
        name="say",
        description="Send a message as the bot",
    )
    @app_commands.describe(
        message="The message you want the bot to send",
        message_link="Optional message link to reply to",
    )
    async def say(
        self,
        interaction: discord.Interaction,
        message: str,
        message_link: str = None,
    ):
        """Send a message as the bot or reply to an existing message."""

        # Admin-only.
        if not is_admin(interaction.user.id):
            await interaction.response.send_message(
                "❌ You do not have permission to use `/say`.",
                ephemeral=True,
            )
            return

        message = message.strip()

        if not message:
            await interaction.response.send_message(
                "❌ The message cannot be empty.",
                ephemeral=True,
            )
            return

        # Discord messages have a 2000-character limit.
        if len(message) > 2000:
            await interaction.response.send_message(
                "❌ Your message is too long. Discord messages "
                "can contain up to 2000 characters.",
                ephemeral=True,
            )
            return

        # ----------------------------------------------------
        # Reply to an existing message
        # ----------------------------------------------------

        if message_link:
            try:
                message_link = message_link.strip()
                parsed = urlparse(message_link)

                # Require an actual Discord message URL.
                if parsed.scheme not in ("http", "https"):
                    raise ValueError("Invalid URL scheme.")

                if parsed.netloc not in (
                    "discord.com",
                    "www.discord.com",
                    "discordapp.com",
                    "www.discordapp.com",
                ):
                    raise ValueError("Not a Discord URL.")

                parts = [
                    part
                    for part in parsed.path.strip("/").split("/")
                    if part
                ]

                # Expected:
                # /channels/GUILD_ID/CHANNEL_ID/MESSAGE_ID
                if len(parts) != 4 or parts[0] != "channels":
                    raise ValueError("Invalid Discord message link.")

                guild_id = int(parts[1])
                channel_id = int(parts[2])
                message_id = int(parts[3])

                # If this command is being used inside a guild,
                # prevent accidentally replying to another guild.
                if interaction.guild_id is not None:
                    if guild_id != interaction.guild_id:
                        await interaction.response.send_message(
                            "❌ That message belongs to a different server.",
                            ephemeral=True,
                        )
                        return

                channel = self.bot.get_channel(channel_id)

                if channel is None:
                    channel = await self.bot.fetch_channel(
                        channel_id
                    )

                if not hasattr(channel, "fetch_message"):
                    raise ValueError(
                        "That channel cannot contain messages."
                    )

                target_message = await channel.fetch_message(
                    message_id
                )

                await target_message.reply(
                    message,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False,
                        roles=False,
                        users=True,
                    ),
                )

                await interaction.response.send_message(
                    "✅ Message sent as a reply.",
                    ephemeral=True,
                )

                return

            except ValueError:
                await interaction.response.send_message(
                    "❌ Invalid Discord message link. "
                    "Use a link like "
                    "`https://discord.com/channels/server/channel/message`.",
                    ephemeral=True,
                )
                return

            except discord.NotFound:
                await interaction.response.send_message(
                    "❌ I couldn't find that channel or message.",
                    ephemeral=True,
                )
                return

            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ I don't have permission to access or reply "
                    "to that message.",
                    ephemeral=True,
                )
                return

            except discord.HTTPException:
                await interaction.response.send_message(
                    "❌ Discord rejected the message. "
                    "Check my permissions and try again.",
                    ephemeral=True,
                )
                return

        # ----------------------------------------------------
        # Normal /say
        # ----------------------------------------------------

        try:
            if interaction.channel is None:
                await interaction.response.send_message(
                    "❌ I couldn't determine which channel to send to.",
                    ephemeral=True,
                )
                return

            await interaction.channel.send(
                message,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    roles=False,
                    users=True,
                ),
            )

            await interaction.response.send_message(
                "✅ Message sent.",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to send messages in this channel.",
                ephemeral=True,
            )

        except discord.HTTPException:
            await interaction.response.send_message(
                "❌ Discord rejected the message. Please try again.",
                ephemeral=True,
            )


# ============================================================
# Setup
# ============================================================

async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCog(bot))

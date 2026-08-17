import asyncio
import datetime
import html
import json
import logging
import os
import time
from typing import Literal
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.storage import (
    add_active_ticket,
    add_cooldown,
    add_to_blacklist,
    remove_from_blacklist,
    append_ticket_log,
    get_active_ticket,
    get_active_ticket_for_user,
    get_blacklist_data,
    get_category_counter,
    get_redirect_message,
    get_settings,
    get_tickets_data,
    get_welcome_message,
    increment_category_counter,
    is_on_cooldown,
    is_ticket_channel,
    remove_active_ticket,
    render_ticket_template,
    set_autoclose_disabled,
    set_category_counter,
    slugify,
    touch_ticket_activity,
    update_active_ticket,
)

# logger for Render stdout/stderr so platform logs capture ticket close/delete events
logger = logging.getLogger("tickets")

# ⚠️ TEST MODE: when True, autoclose_watcher uses seconds instead of hours
# (30s reminder / 1min autoclose) so the feature can be verified quickly.
# Set back to False for real 12h/24h behavior once confirmed working.
AUTOCLOSE_TEST_MODE = False

# Role granted/removed alongside the ticket blacklist when the "voidcore"
# type is selected on /addticketblacklist and /removeticketblacklist.
VOIDCORE_BLACKLIST_ROLE_ID = 1535963860203470858
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


async def send_ticket_log(bot, action, **fields):
    """Post a ticket-activity embed to the configured Access Control log
    channel, if one is set. Silently does nothing if no channel is configured
    or the bot can't find/post to it."""
    try:
        from utils.access import get_log_channel_id

        channel_id = get_log_channel_id()
        if not channel_id:
            return
        channel = bot.get_channel(int(channel_id))
        if not channel:
            return

        colors = {
            "opened": discord.Color.green(),
            "closed": discord.Color.red(),
        }
        titles = {
            "opened": "🎫 Ticket Opened",
            "closed": "🔒 Ticket Closed",
        }

        embed = discord.Embed(
            title=titles.get(action, action.title()),
            color=colors.get(action, discord.Color.greyple()),
            timestamp=datetime.datetime.utcnow(),
        )
        for key, value in fields.items():
            if value is not None:
                embed.add_field(
                    name=key.replace("_", " ").title(), value=str(value), inline=True
                )

        await channel.send(embed=embed)
    except Exception:
        logger.exception(f"Failed to send ticket log embed for action={action}")


async def send_blacklist_log(bot, action, **fields):
    """Post an embed to the separate Blacklist Log Channel (Access Control),
    used only by /addticketblacklist and /removeticketblacklist — kept apart
    from the general ticket activity log channel above. Silently does
    nothing if no channel is configured or the bot can't post to it."""
    try:
        from utils.access import get_blacklist_log_channel_id

        channel_id = get_blacklist_log_channel_id()
        if not channel_id:
            return
        channel = bot.get_channel(int(channel_id))
        if not channel:
            return

        colors = {
            "blacklist_added": discord.Color.red(),
            "blacklist_removed": discord.Color.green(),
        }
        titles = {
            "blacklist_added": "🚫 User Blacklisted",
            "blacklist_removed": "✅ Blacklist Removed",
        }

        embed = discord.Embed(
            title=titles.get(action, action.title()),
            color=colors.get(action, discord.Color.greyple()),
            timestamp=datetime.datetime.utcnow(),
        )
        for key, value in fields.items():
            if value is not None:
                embed.add_field(
                    name=key.replace("_", " ").title(), value=str(value), inline=True
                )

        await channel.send(embed=embed)
    except Exception:
        logger.exception(f"Failed to send blacklist log embed for action={action}")


def build_discord_like_transcript(
    messages, channel_name, ticket_meta, generated_at_iso, filename
):
    """Render a dark-themed Discord-like HTML transcript, including embeds,
    buttons, and image/file attachments styled to match Discord's real UI."""
    safe = html.escape

    def render_embed(embed):
        color_hex = "#5865f2"
        if embed.get("color") is not None:
            try:
                color_hex = f"#{int(embed['color']):06x}"
            except Exception:
                pass

        out = [f'<div class="discord-embed" style="border-left-color:{color_hex}">']

        if embed.get("thumbnail_url"):
            out.append(f'<img class="embed-thumb" src="{safe(embed["thumbnail_url"])}"/>')

        if embed.get("author_name"):
            out.append('<div class="embed-author">')
            if embed.get("author_icon"):
                out.append(f'<img src="{safe(embed["author_icon"])}"/>')
            out.append(f'<span>{safe(embed["author_name"])}</span>')
            out.append("</div>")

        if embed.get("title"):
            title_html = safe(embed["title"])
            if embed.get("url"):
                title_html = f'<a href="{safe(embed["url"])}" target="_blank">{title_html}</a>'
            out.append(f'<div class="embed-title">{title_html}</div>')

        if embed.get("description"):
            out.append(f'<div class="embed-desc">{safe(embed["description"])}</div>')

        fields = embed.get("fields") or []
        if fields:
            out.append('<div class="embed-fields">')
            for f in fields:
                cls = "embed-field" if f.get("inline") else "embed-field full"
                out.append(f'<div class="{cls}">')
                out.append(f'<div class="embed-field-name">{safe(f.get("name",""))}</div>')
                out.append(f'<div class="embed-field-value">{safe(f.get("value",""))}</div>')
                out.append("</div>")
            out.append("</div>")

        if embed.get("image_url"):
            out.append(f'<img class="embed-image" src="{safe(embed["image_url"])}"/>')

        if embed.get("footer_text"):
            out.append('<div class="embed-footer">')
            if embed.get("footer_icon"):
                out.append(f'<img src="{safe(embed["footer_icon"])}"/>')
            out.append(f'<span>{safe(embed["footer_text"])}</span>')
            out.append("</div>")

        out.append("</div>")
        return "".join(out)

    def render_buttons(rows):
        out = []
        for row in rows:
            out.append('<div class="discord-buttons">')
            for btn in row:
                style = btn.get("style") or "secondary"
                label = safe(btn.get("label") or "")
                emoji = btn.get("emoji")
                prefix = f"{safe(emoji)} " if emoji else ""
                link_icon = " ↗" if style == "link" else ""
                out.append(
                    f'<span class="discord-btn style-{safe(style)}">{prefix}{label}{link_icon}</span>'
                )
            out.append("</div>")
        return "".join(out)

    parts = []
    parts.append("<!doctype html>")
    parts.append(
        '<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    )
    parts.append(f"<title>Transcript - {safe(channel_name)}</title>")
    parts.append(
        "<style>"
        "body{background:#0f1114;color:#e6eef8;font-family:Inter,Segoe UI,Roboto,Arial,sans-serif;margin:0}"
        ".container{max-width:900px;margin:20px auto;padding:18px}"
        ".embed{background:#2f3136;border-left:4px solid #2a9df4;padding:12px;border-radius:6px;margin-bottom:16px}"
        ".embed h2{margin:0 0 6px 0}"
        ".field{margin:6px 0;padding:6px 10px;background:#222326;border-radius:6px}"
        ".msg{display:flex;gap:12px;padding:10px;border-radius:8px;background:linear-gradient(180deg,#0f1114,#0f1114);margin-bottom:6px}"
        ".avatar{width:42px;height:42px;border-radius:50%;flex:0 0 42px}"
        ".msg-body{flex:1}"
        ".meta{color:#9aa5b1;font-size:13px;margin-bottom:6px}"
        ".content{white-space:pre-wrap;color:#dbe7ef}"
        ".attachments{margin-top:6px}"
        ".file-card{display:flex;align-items:center;gap:8px;background:#2b2d31;border:1px solid #3a3c42;border-radius:6px;padding:8px 12px;margin-top:6px;max-width:360px}"
        ".file-card a{color:#00a8fc;text-decoration:none;font-weight:600}"
        ".footer{margin-top:18px;padding:10px;color:#9aa5b1;font-size:13px;border-top:1px solid #1b1d20}"
        ".discord-embed{background:#2b2d31;border-left:4px solid #5865f2;border-radius:4px;padding:12px 16px;margin:6px 0 6px 0;max-width:520px}"
        ".discord-embed .embed-author{display:flex;align-items:center;gap:6px;font-size:14px;font-weight:600;margin-bottom:6px}"
        ".discord-embed .embed-author img{width:20px;height:20px;border-radius:50%}"
        ".discord-embed .embed-title{font-weight:700;font-size:15px;margin-bottom:4px;color:#fff}"
        ".discord-embed .embed-title a{color:#00a8fc;text-decoration:none}"
        ".discord-embed .embed-desc{font-size:14px;color:#dbdee1;white-space:pre-wrap;margin-bottom:8px}"
        ".discord-embed .embed-fields{display:flex;flex-wrap:wrap;gap:8px}"
        ".discord-embed .embed-field{flex:1 1 auto;min-width:100px}"
        ".discord-embed .embed-field.full{flex-basis:100%}"
        ".discord-embed .embed-field-name{font-weight:600;font-size:13px;color:#fff;margin-bottom:2px}"
        ".discord-embed .embed-field-value{font-size:13px;color:#dbdee1;white-space:pre-wrap}"
        ".discord-embed .embed-thumb{float:right;width:80px;height:80px;border-radius:4px;margin-left:12px;object-fit:cover}"
        ".discord-embed .embed-image{max-width:100%;border-radius:4px;margin-top:8px;display:block}"
        ".discord-embed .embed-footer{display:flex;align-items:center;gap:6px;font-size:12px;color:#949ba4;margin-top:8px}"
        ".discord-embed .embed-footer img{width:16px;height:16px;border-radius:50%}"
        ".discord-buttons{display:flex;gap:8px;margin:8px 0 4px 0;flex-wrap:wrap}"
        ".discord-btn{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;border-radius:3px;font-size:14px;font-weight:500;color:#fff;cursor:default}"
        ".discord-btn.style-primary{background:#5865f2}"
        ".discord-btn.style-secondary{background:#4e5058}"
        ".discord-btn.style-success{background:#248046}"
        ".discord-btn.style-danger{background:#da373c}"
        ".discord-btn.style-link{background:#4e5058}"
        "</style>"
    )
    parts.append("</head><body>")
    parts.append('<div class="container">')

    # ticket embed
    parts.append('<div class="embed">')
    parts.append(f"<h2>🎫 {safe(channel_name)}</h2>")
    creator = ticket_meta.get("creator") if ticket_meta else None
    if creator:
        parts.append(
            f'<div style="font-size:14px;color:#9aa5b1">Created by: {safe(creator.get("name",""))}</div>'
        )
    parts.append(
        '<div style="margin-top:8px;display:flex;gap:10px;flex-wrap:wrap">'
    )
    fields = ticket_meta.get("fields") if ticket_meta else {}
    if fields:
        for k in ("timezone", "display_name", "can_join"):
            v = fields.get(k)
            if v:
                parts.append(
                    f'<div class="field"><strong>{safe(k.replace("_"," ").title())}:</strong> {safe(v)}</div>'
                )
    parts.append("</div>")
    parts.append("</div>")

    # messages
    parts.append("<div>")
    for m in messages:
        parts.append('<div class="msg">')
        parts.append(
            f'<img class="avatar" src="{safe(m.get("avatar_url") or "https://cdn.discordapp.com/embed/avatars/0.png")}" alt="avatar"/>'
        )
        parts.append('<div class="msg-body">')
        parts.append(
            f'<div class="meta"><strong>{safe(m.get("author_name","Unknown"))}</strong> <span style="margin-left:8px">{safe(m.get("ts",""))}</span></div>'
        )
        if m.get("content"):
            parts.append(
                f'<div class="content">{safe(m.get("content",""))}</div>'
            )

        attachments = m.get("attachments") or []
        if attachments:
            parts.append('<div class="attachments">')
            for a in attachments:
                if a.get("is_image"):
                    parts.append(
                        f'<div><img src="{safe(a["url"])}" style="max-width:360px;border-radius:6px;margin-top:6px"/></div>'
                    )
                else:
                    parts.append(
                        '<div class="file-card">📎 '
                        f'<a href="{safe(a["url"])}" target="_blank">{safe(a.get("filename") or a["url"])}</a>'
                        "</div>"
                    )
            parts.append("</div>")

        for embed in m.get("embeds") or []:
            parts.append(render_embed(embed))

        components = m.get("components") or []
        if components:
            parts.append(render_buttons(components))

        parts.append("</div>")
        parts.append("</div>")
    parts.append("</div>")

    # footer
    parts.append('<div class="footer">')
    parts.append(f"Transcript generated on {safe(generated_at_iso)}")
    parts.append(
        f'&nbsp; • &nbsp;<a href="{safe(filename)}" download>Download HTML</a>'
    )
    parts.append("</div>")

    parts.append("</div></body></html>")
    return "\n".join(parts)

# --- PERSISTENT CLOSE BUTTON & CONFIRMATION ---
class ConfirmView(discord.ui.View):
    def __init__(self, cog, target_channel):
        super().__init__(timeout=60)
        self.cog = cog
        self.target_channel = target_channel

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(
        self, inter: discord.Interaction, btn: discord.ui.Button
    ):
        await inter.response.defer(ephemeral=True)
        try:
            await self.cog.do_close(
                self.target_channel, inter.user, "Closed via button"
            )
        except Exception as e:
            try:
                await inter.followup.send(
                    f"❌ Failed to close ticket: {e}", ephemeral=True
                )
            except Exception:
                pass

        for child in self.children:
            child.disabled = True
        try:
            await inter.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, inter: discord.Interaction, btn: discord.ui.Button
    ):
        await inter.response.edit_message(
            content="Cancelled.", view=None, ephemeral=True
        )


class RequestCloseView(discord.ui.View):
    """Sent when staff run /requestclose. Only the ticket opener can respond
    — staff cannot force the close through this view."""

    def __init__(self, cog, target_channel, creator_id, reason: str = None):
        super().__init__(timeout=600)
        self.cog = cog
        self.target_channel = target_channel
        self.creator_id = str(creator_id)
        self.reason = reason

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.creator_id:
            await interaction.response.send_message(
                "❌ Only the person who opened this ticket can respond to this request.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Yes, close it", style=discord.ButtonStyle.danger)
    async def confirm(self, inter: discord.Interaction, btn: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await inter.response.edit_message(content="✅ Closing ticket...", view=self)
        close_reason = (
            f"Closed by opener via /requestclose — reason: {self.reason}"
            if self.reason
            else "Closed by opener via /requestclose"
        )
        try:
            await self.cog.do_close(self.target_channel, inter.user, close_reason)
        except Exception as e:
            try:
                await inter.followup.send(
                    f"❌ Failed to close ticket: {e}", ephemeral=True
                )
            except Exception:
                pass

    @discord.ui.button(label="No, keep it open", style=discord.ButtonStyle.secondary)
    async def decline(self, inter: discord.Interaction, btn: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await inter.response.edit_message(
            content="Ticket will stay open.", view=self
        )


class CloseConfirmView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_close_btn",
        emoji="🔒",
    )
    async def close_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        cog = self.bot.get_cog("TicketsCog")
        if not cog:
            await interaction.response.send_message(
                "❌ Close functionality is not available right now.",
                ephemeral=True,
            )
            return

        settings = get_settings()
        allowed_category = settings.get("default_category_id")
        if (
            allowed_category
            and interaction.channel.category
            and str(interaction.channel.category.id) != str(allowed_category)
        ):
            await interaction.response.send_message(
                "❌ This command only works in ticket channels.",
                ephemeral=True,
            )
            return

        perms = interaction.user.guild_permissions
        from utils.storage import get_logs_for_ticket

        creator_id = None
        try:
            logs = get_logs_for_ticket(str(interaction.channel.id))
            created = next(
                (l for l in logs if l.get("action") == "created"), None
            )
            if created and created.get("creator"):
                creator_id = created.get("creator").get("id")
        except Exception:
            creator_id = None

        if not (
            perms.manage_channels
            or (creator_id and str(interaction.user.id) == str(creator_id))
        ):
            await interaction.response.send_message(
                "❌ You don't have permission to close this ticket.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Are you sure you want to close this ticket?",
            view=ConfirmView(cog, interaction.channel),
            ephemeral=True,
        )


# --- MAIN TICKET CREATION SELECT MENU ---
class TicketView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

        from utils.storage import get_ticket_categories

        categories = get_ticket_categories()
        options = [
            discord.SelectOption(
                label=c.get("label", "Ticket")[:100],
                description=(c.get("description") or "")[:100] or None,
                emoji=c.get("emoji") or None,
            )
            for c in categories[:25]
        ]

        select = discord.ui.Select(
            placeholder="Choose ticket type...",
            min_values=1,
            max_values=1,
            custom_id="create_ticket_select",
            options=options,
        )

        async def _select_callback(interaction: discord.Interaction):
            await self.ticket_select(interaction, select)

        select.callback = _select_callback
        self.add_item(select)

    async def ticket_select(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ):
        settings = get_settings()
        if not settings.get("tickets_enabled", True):
            await interaction.response.send_message(
                "🚫 Ticket creation is currently disabled by administrators.",
                ephemeral=True,
            )
            return

        selection = select.values[0] if select.values else "General Support"
        outer_view = self

        # check blacklist (individually-blacklisted user IDs)
        try:
            bl = get_blacklist_data()
            my_entry = next(
                (
                    e for e in bl.get("blacklisted_users", [])
                    if str(e.get("user_id")) == str(interaction.user.id)
                ),
                None,
            )
            if my_entry:
                reason = my_entry.get("reason") or "No reason provided."
                emb = discord.Embed(
                    title="❌ You're Blacklisted",
                    description=f"You cannot create tickets on this panel.\n**Reason:** {reason}",
                    color=discord.Color.red(),
                )
                emb.set_footer(text="Tickety | Tickety.top")
                await interaction.response.send_message(
                    embed=emb, ephemeral=True
                )
                return
        except Exception:
            pass

        # check ticket blacklist roles (set from the dashboard's Access Control page)
        try:
            from utils.access import get_blacklist_role_ids

            blacklist_role_ids = {str(r) for r in get_blacklist_role_ids()}
            if blacklist_role_ids:
                member_role_ids = {
                    str(r.id) for r in getattr(interaction.user, "roles", [])
                }
                matched_ids = member_role_ids.intersection(blacklist_role_ids)
                if matched_ids:
                    guild = interaction.guild
                    matched_role = (
                        guild.get_role(int(next(iter(matched_ids))))
                        if guild
                        else None
                    )
                    role_mention = (
                        matched_role.mention if matched_role else "a blacklisted role"
                    )
                    emb = discord.Embed(
                        title="❌ Blocked Role",
                        description=f"You cannot create tickets on this panel because you have the {role_mention} role.",
                        color=discord.Color.red(),
                    )
                    emb.set_footer(text="Tickety | Tickety.top")
                    await interaction.response.send_message(
                        embed=emb, ephemeral=True
                    )
                    return
        except Exception:
            pass

        # check per-category blacklist roles (set per dropdown option)
        try:
            from utils.storage import get_ticket_categories

            categories = get_ticket_categories()
            matched_category = next(
                (c for c in categories if c.get("label") == selection), None
            )
            cat_blacklist_ids = {
                str(r) for r in (matched_category.get("blacklist_roles", []) if matched_category else [])
            }
            if cat_blacklist_ids:
                member_role_ids = {
                    str(r.id) for r in getattr(interaction.user, "roles", [])
                }
                matched_ids = member_role_ids.intersection(cat_blacklist_ids)
                if matched_ids:
                    guild = interaction.guild
                    matched_role = (
                        guild.get_role(int(next(iter(matched_ids))))
                        if guild
                        else None
                    )
                    role_mention = (
                        matched_role.mention if matched_role else "a blacklisted role"
                    )
                    emb = discord.Embed(
                        title="❌ Blocked Category",
                        description=f"You cannot open a **{selection}** ticket because you have the {role_mention} role.",
                        color=discord.Color.red(),
                    )
                    emb.set_footer(text="Tickety | Tickety.top")
                    await interaction.response.send_message(
                        embed=emb, ephemeral=True
                    )
                    return
        except Exception:
            pass

        # check for an already-open ticket (any category) before letting them make another
        try:
            existing = get_active_ticket_for_user(interaction.user.id)
            if existing:
                existing_channel_id = existing.get("channel_id")
                existing_channel = (
                    interaction.guild.get_channel(int(existing_channel_id))
                    if interaction.guild and existing_channel_id
                    else None
                )
                if existing_channel is None:
                    # the channel is gone (deleted manually, or while the bot
                    # was offline) but the record was never cleaned up — heal
                    # it here instead of permanently blocking this user
                    remove_active_ticket(str(existing_channel_id))
                    logger.info(
                        f"Cleared stale active-ticket record for missing channel={existing_channel_id} (self-heal on new ticket attempt)"
                    )
                else:
                    channel_mention = f"<#{existing_channel_id}>"
                    await interaction.response.send_message(
                        f"❌ You already have an open ticket: {channel_mention}. "
                        f"Please close it before opening a new one.",
                        ephemeral=True,
                    )
                    return
        except Exception:
            pass

        # check cooldown
        try:
            on_cd, cd = is_on_cooldown(str(interaction.user.id))
            if on_cd and cd:
                try:
                    expires_ts = int(cd.get("expires_ts"))
                    await interaction.response.send_message(
                        f"⏳ You are on cooldown until <t:{expires_ts}:F> (<t:{expires_ts}:R>).",
                        ephemeral=True,
                    )
                except Exception:
                    await interaction.response.send_message(
                        f"⏳ You are on cooldown until {cd.get('expires_at')}.",
                        ephemeral=True,
                    )
                return
        except Exception:
            pass

        class TicketModal(discord.ui.Modal, title=f"{selection}"):
            def __init__(self, author, selection):
                super().__init__()
                self.author = author
                self.selection = selection

                self.timezone = discord.ui.TextInput(
                    label="⏰ Which country and timezone are you from? *",
                    placeholder="e.g. UTC, PST, CET",
                    required=True,
                    style=discord.TextStyle.short,
                    max_length=100,
                )

                self.display_name = discord.ui.TextInput(
                    label="🎮 What is your roblox display name? *",
                    placeholder="Provide your display name (not username)",
                    required=True,
                    style=discord.TextStyle.short,
                    max_length=100,
                )

                self.can_join = discord.ui.TextInput(
                    label="🎲 Are you able to join a private server? *",
                    placeholder="Yes or No",
                    required=True,
                    style=discord.TextStyle.short,
                    max_length=10,
                )

                self.add_item(self.timezone)
                self.add_item(self.display_name)
                self.add_item(self.can_join)

            async def on_submit(
                self, modal_interaction: discord.Interaction
            ):
                settings = get_settings()
                guild = modal_interaction.guild
                user = self.author

                await modal_interaction.response.defer(ephemeral=True)

                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(
                        read_messages=False
                    ),
                    user: discord.PermissionOverwrite(
                        read_messages=True,
                        send_messages=True,
                        attach_files=True,
                    ),
                    guild.me: discord.PermissionOverwrite(
                        read_messages=True,
                        send_messages=True,
                        manage_channels=True,
                    ),
                }

                support_role_id = settings.get("support_role_id")
                if support_role_id:
                    support_role = guild.get_role(int(support_role_id))
                    if support_role:
                        overwrites[support_role] = discord.PermissionOverwrite(
                            read_messages=True, send_messages=True
                        )

                try:
                    from utils.access import get_ticket_viewer_role_ids

                    for rid in get_ticket_viewer_role_ids():
                        viewer_role = guild.get_role(int(rid))
                        if viewer_role:
                            overwrites[viewer_role] = discord.PermissionOverwrite(
                                read_messages=True,
                                send_messages=True,
                                embed_links=True,
                                attach_files=True,
                            )
                except Exception:
                    pass

                from utils.storage import get_ticket_categories

                categories = get_ticket_categories()
                category_cfg = next(
                    (c for c in categories if c.get("label") == self.selection),
                    None,
                )

                category_id = (
                    (category_cfg or {}).get("discord_category_id")
                    or settings.get("default_category_id")
                )
                category = (
                    guild.get_channel(int(category_id))
                    if category_id
                    else None
                )

                prefix = (category_cfg or {}).get("name_prefix") or slugify(
                    self.selection
                )
                category_number = increment_category_counter(prefix)
                channel_name = f"{prefix}-{category_number:04d}"

                try:
                    ticket_channel = await guild.create_text_channel(
                        name=channel_name,
                        category=category,
                        overwrites=overwrites,
                        reason=f"Ticket created by {user.name}",
                    )

                    # Build the placeholder set available to both editable
                    # templates (welcome message + redirect message)
                    template_vars = {
                        "user": user.mention,
                        "user_mention": user.mention,
                        "user_name": user.name,
                        "channel": ticket_channel.mention,
                        "channel_mention": ticket_channel.mention,
                        "channel_name": ticket_channel.name,
                        "category": self.selection,
                        "timezone": self.timezone.value or "",
                        "display_name": self.display_name.value or "",
                        "can_join": self.can_join.value or "",
                    }

                    welcome_cfg = get_welcome_message()

                    if welcome_cfg.get("use_embed", True):
                        embed_color = discord.Color.blue()
                        try:
                            color_hex = (welcome_cfg.get("color") or "").lstrip("#")
                            if color_hex:
                                embed_color = discord.Color(int(color_hex, 16))
                        except Exception:
                            pass

                        embed = discord.Embed(
                            title=render_ticket_template(
                                welcome_cfg.get("title") or "", **template_vars
                            )
                            or None,
                            description=render_ticket_template(
                                welcome_cfg.get("description") or "", **template_vars
                            )
                            or "",
                            color=embed_color,
                        )

                        if welcome_cfg.get("show_timezone", True) and self.timezone.value:
                            embed.add_field(
                                name="Timezone",
                                value=self.timezone.value,
                                inline=False,
                            )
                        if welcome_cfg.get("show_display_name", True) and self.display_name.value:
                            embed.add_field(
                                name="Display name",
                                value=self.display_name.value,
                                inline=False,
                            )
                        if welcome_cfg.get("show_can_join", True):
                            embed.add_field(
                                name="Can join private server?",
                                value=self.can_join.value,
                                inline=False,
                            )
                        if category_cfg and category_cfg.get("open_note"):
                            embed.add_field(
                                name="Note",
                                value=category_cfg["open_note"],
                                inline=False,
                            )
                        if welcome_cfg.get("footer"):
                            embed.set_footer(
                                text=render_ticket_template(
                                    welcome_cfg["footer"], **template_vars
                                )
                            )

                        await ticket_channel.send(
                            content=render_ticket_template(
                                welcome_cfg.get("content") or "", **template_vars
                            )
                            or None,
                            embed=embed,
                        )
                    else:
                        # Plain-text mode: no embed, just the rendered message
                        plain_parts = [
                            render_ticket_template(
                                welcome_cfg.get("content") or "{user_mention}",
                                **template_vars,
                            )
                        ]
                        if welcome_cfg.get("show_timezone", True) and self.timezone.value:
                            plain_parts.append(f"**Timezone:** {self.timezone.value}")
                        if welcome_cfg.get("show_display_name", True) and self.display_name.value:
                            plain_parts.append(f"**Display name:** {self.display_name.value}")
                        if welcome_cfg.get("show_can_join", True):
                            plain_parts.append(f"**Can join private server?** {self.can_join.value}")
                        if category_cfg and category_cfg.get("open_note"):
                            plain_parts.append(f"**Note:** {category_cfg['open_note']}")
                        await ticket_channel.send(content="\n".join(plain_parts))

                    # Attach close button view
                    await ticket_channel.send(
                        view=CloseConfirmView(outer_view.bot)
                    )

                    # Log creation
                    ticket_id = str(ticket_channel.id)
                    timestamp = datetime.datetime.utcnow().isoformat() + "Z"

                    try:
                        from utils.storage import increment_ticket_counter

                        ticket_number = increment_ticket_counter()
                    except Exception as e:
                        logger.exception(
                            f"Failed to increment ticket counter for channel={ticket_channel.id}: {e}"
                        )
                        ticket_number = None

                    # Record the active ticket in its OWN try/except, separate from
                    # logging below - this is what powers the "you already have a
                    # ticket open" block, so a logging/counter failure must never
                    # silently skip it too.
                    try:
                        add_active_ticket(
                            str(ticket_channel.id),
                            str(ticket_channel.id),
                            str(user.id),
                            ticket_number,
                        )
                        update_active_ticket(
                            str(ticket_channel.id),
                            category_label=self.selection,
                            category_prefix=prefix,
                            category_number=category_number,
                        )
                    except Exception as e:
                        logger.exception(
                            f"Failed to record active ticket for channel={ticket_channel.id} user={user.id}: {e}"
                        )

                    try:
                        from utils.storage import append_ticket_log

                        append_ticket_log({
                            "ticket_id": ticket_id,
                            "ticket_name": ticket_channel.name,
                            "action": "created",
                            "timestamp": timestamp,
                            "ticket_number": ticket_number,
                            "category_label": self.selection,
                            "category_prefix": prefix,
                            "category_number": category_number,
                            "creator": {
                                "id": str(user.id),
                                "name": str(user),
                            },
                            "fields": {
                                "timezone": self.timezone.value,
                                "display_name": self.display_name.value,
                                "can_join": self.can_join.value,
                            },
                        })
                    except Exception as e:
                        logger.exception(
                            f"Failed to append ticket creation log for channel={ticket_channel.id}: {e}"
                        )

                    await modal_interaction.followup.send(
                        render_ticket_template(
                            get_redirect_message().get("content")
                            or "✅ Ticket created! Please head over to {channel}.",
                            **template_vars,
                        ),
                        ephemeral=True,
                    )

                    await send_ticket_log(
                        outer_view.bot,
                        "opened",
                        ticket=ticket_channel.mention,
                        opened_by=f"{user} ({user.id})",
                        type=self.selection,
                    )

                except discord.Forbidden:
                    await modal_interaction.followup.send(
                        "❌ I lack permissions to create channels or set permissions in this server.",
                        ephemeral=True,
                    )
                except Exception as e:
                    await modal_interaction.followup.send(
                        f"❌ Failed to create ticket channel: {str(e)}",
                        ephemeral=True,
                    )

        modal = TicketModal(interaction.user, selection)
        await interaction.response.send_modal(modal)


# --- TICKETS COG & SLASH COMMANDS ---
class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def get_ticket_view(self):
        return TicketView(self.bot)

    async def cog_load(self):
        self.autoclose_watcher.start()

    def cog_unload(self):
        self.autoclose_watcher.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Whenever the ticket opener posts in their own ticket, reset the
        24h autoclose clock and clear the 12h reminder flag so it can fire
        again on the next quiet stretch."""
        if message.author.bot or not message.guild:
            return
        try:
            ticket = get_active_ticket(message.channel.id)
            if ticket and str(ticket.get("user_id")) == str(message.author.id):
                touch_ticket_activity(message.channel.id)
        except Exception:
            logger.exception(
                f"Failed to update ticket activity for channel={message.channel.id}"
            )

    @tasks.loop(minutes=10)
    async def autoclose_watcher(self):
        """Every 10 minutes: ping openers who've gone quiet for 12h with a
        heads-up, then close tickets that hit 24h of inactivity with nobody
        having disabled it via /disableautoclose."""
        try:
            data = get_tickets_data()
        except Exception:
            logger.exception("autoclose_watcher: failed to load tickets data")
            return

        now = time.time()
        if AUTOCLOSE_TEST_MODE:
            REMINDER_AFTER = 30            # 30 seconds
            CLOSE_AFTER = 60                # 1 minute
        else:
            REMINDER_AFTER = 12 * 3600
            CLOSE_AFTER = 24 * 3600

        for ticket in list(data.get("active_tickets", [])):
            if ticket.get("autoclose_disabled"):
                continue

            channel_id = ticket.get("channel_id")
            last_activity = ticket.get("last_activity") or ticket.get("created_at") or now
            elapsed = now - float(last_activity)

            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                continue

            if elapsed >= CLOSE_AFTER:
                try:
                    await channel.send(
                        "🔒 This ticket is being automatically closed after 24 hours "
                        "of inactivity from the person who opened it."
                    )
                    await self.do_close(
                        channel,
                        self.bot.user,
                        reason="Automatically closed — opener inactive for 24 hours.",
                    )
                except Exception:
                    logger.exception(
                        f"autoclose_watcher: failed to auto-close channel={channel_id}"
                    )
                continue

            if elapsed >= REMINDER_AFTER and not ticket.get("reminder_sent"):
                try:
                    user_id = ticket.get("user_id")
                    await channel.send(
                        content=(
                            f"<@{user_id}> ⏳ This ticket will **automatically close in 12 hours** "
                            "due to inactivity. Send a message here to keep it open, or ask staff "
                            "to run `/disableautoclose` to stop autoclose entirely."
                        ),
                    )
                    update_active_ticket(channel_id, reminder_sent=True)
                except Exception:
                    logger.exception(
                        f"autoclose_watcher: failed to send reminder for channel={channel_id}"
                    )

    @autoclose_watcher.before_loop
    async def before_autoclose_watcher(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        """If a ticket channel gets deleted directly in Discord (instead of
        via /close, close-with-reason, or request-close), do_close() never
        runs and the active-ticket record is left behind — permanently
        blocking that user from opening a new ticket. Clean it up here so a
        manual delete behaves the same as a normal close, storage-wise."""
        try:
            if is_ticket_channel(channel.id):
                remove_active_ticket(str(channel.id))
                logger.info(
                    f"Cleared stale active-ticket record for manually deleted channel={channel.id}"
                )
        except Exception:
            pass

    async def _ticket_command_check(self, interaction: discord.Interaction) -> bool:
        """Shared guard for all ticket-management slash commands: requires
        Manage Channels and only works inside an active ticket channel."""
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "❌ You need the **Manage Channels** permission to use this command.",
                ephemeral=True,
            )
            return False
        if not interaction.channel or not is_ticket_channel(interaction.channel.id):
            await interaction.response.send_message(
                "❌ This command can only be used inside an active ticket channel.",
                ephemeral=True,
            )
            return False
        return True

    async def _powerful_command_check(self, interaction: discord.Interaction) -> bool:
        """Extra guard for /move and /ticketnumber on top of the normal
        ticket-command check — restricts these two specifically to roles/
        users configured in Access Control, once configured."""
        if not await self._ticket_command_check(interaction):
            return False

        from utils.access import has_powerful_command_access

        member_role_ids = [str(r.id) for r in getattr(interaction.user, "roles", [])]
        if not has_powerful_command_access(interaction.user.id, member_role_ids):
            await interaction.response.send_message(
                "❌ You don't have permission to use this command. Ask an admin to "
                "add your role or user ID to the Powerful Command Access list in "
                "Access Control.",
                ephemeral=True,
            )
            return False
        return True

    async def _dangerous_command_check(self, interaction: discord.Interaction) -> bool:
        """Guard for standalone dangerous commands that aren't tied to a
        ticket channel (/addticketblacklist, /removeticketblacklist).
        Allowed for real Discord Administrators, dashboard-configured
        admins, and anyone in the Powerful Command Access list. Unlike
        _powerful_command_check, this does NOT fall back to "anyone" when
        that list is empty - blacklisting is high-impact enough that it
        should stay admin-only until an admin explicitly grants access."""
        from utils.access import (
            get_powerful_command_role_ids,
            get_powerful_command_user_ids,
            is_admin,
        )

        if interaction.user.guild_permissions.administrator:
            return True
        if is_admin(interaction.user.id):
            return True

        member_role_ids = {str(r.id) for r in getattr(interaction.user, "roles", [])}
        if str(interaction.user.id) in set(get_powerful_command_user_ids()):
            return True
        if member_role_ids.intersection(get_powerful_command_role_ids()):
            return True

        await interaction.response.send_message(
            "❌ Only administrators, or roles/users granted Powerful Command "
            "Access in the dashboard's Access Control page, can use this command.",
            ephemeral=True,
        )
        return False

    # Slash Command: /close [reason] — closes immediately, no opener confirmation
    @app_commands.command(
        name="close",
        description="Close this ticket immediately, optionally with a reason",
    )
    @app_commands.describe(reason="Reason for closing this ticket (logged in the transcript)")
    async def close_command(
        self, interaction: discord.Interaction, reason: str = None
    ):
        if not await self._ticket_command_check(interaction):
            return

        await interaction.response.send_message(
            f"🔒 Closing this ticket"
            f"{f' — reason: {reason}' if reason else ''}...",
            ephemeral=True,
        )
        try:
            await self.do_close(
                interaction.channel, interaction.user, reason or "No reason provided."
            )
        except Exception as e:
            try:
                await interaction.followup.send(
                    f"❌ Failed to close ticket: {e}", ephemeral=True
                )
            except Exception:
                pass

    # Slash Command: /requestclose — asks the opener to confirm; staff can't force it
    @app_commands.command(
        name="requestclose",
        description="Ask the ticket opener to confirm closing this ticket",
    )
    @app_commands.describe(reason="Reason for requesting this ticket be closed (shown to the opener)")
    async def request_close_command(
        self, interaction: discord.Interaction, reason: str = None
    ):
        if not await self._ticket_command_check(interaction):
            return

        ticket = get_active_ticket(interaction.channel.id)
        creator_id = ticket.get("user_id") if ticket else None
        if not creator_id:
            await interaction.response.send_message(
                "❌ Couldn't find who opened this ticket.", ephemeral=True
            )
            return

        message = (
            f"<@{creator_id}>, {interaction.user.mention} has requested to close "
            f"this ticket"
            f"{f' — reason: {reason}' if reason else ''}. Do you want to close it?"
        )
        await interaction.response.send_message(
            message,
            view=RequestCloseView(self, interaction.channel, creator_id, reason),
        )

    # Slash Command: /disableautoclose — staff-only, turns off the
    # 24h-inactivity autoclose for this specific ticket
    @app_commands.command(
        name="disableautoclose",
        description="Stop this ticket from auto-closing after 24 hours of inactivity",
    )
    async def disable_autoclose_command(self, interaction: discord.Interaction):
        if not await self._ticket_command_check(interaction):
            return

        ticket = get_active_ticket(interaction.channel.id)
        if ticket and ticket.get("autoclose_disabled"):
            await interaction.response.send_message(
                "🛑 Autoclose is already off for this ticket.", ephemeral=True
            )
            return

        set_autoclose_disabled(interaction.channel.id, True)
        await interaction.response.send_message(
            "🛑 Autoclose turned off — this ticket will no longer close itself due to inactivity."
        )

    # Slash Command: /enableautoclose — staff-only, resumes the
    # 24h-inactivity autoclose for this specific ticket
    @app_commands.command(
        name="enableautoclose",
        description="Resume auto-closing this ticket after inactivity",
    )
    async def enable_autoclose_command(self, interaction: discord.Interaction):
        if not await self._ticket_command_check(interaction):
            return

        ticket = get_active_ticket(interaction.channel.id)
        if ticket and not ticket.get("autoclose_disabled"):
            await interaction.response.send_message(
                "✅ Autoclose is already on for this ticket.", ephemeral=True
            )
            return

        # Reset the inactivity clock from now, rather than from whenever the
        # opener last spoke — otherwise re-enabling on an already-stale
        # ticket could fire the reminder/close almost immediately.
        set_autoclose_disabled(interaction.channel.id, False)
        update_active_ticket(
            interaction.channel.id, last_activity=time.time(), reminder_sent=False
        )
        await interaction.response.send_message(
            "✅ Autoclose resumed — this ticket will close after 12h/24h of inactivity again."
        )

    # Slash Command: /add — grants a member access to this ticket channel
    @app_commands.command(
        name="add",
        description="Add a user to this ticket channel",
    )
    @app_commands.describe(user="The member to add to this ticket")
    async def add_command(
        self, interaction: discord.Interaction, user: discord.Member
    ):
        if not await self._ticket_command_check(interaction):
            return

        if user.bot:
            await interaction.response.send_message(
                "❌ You can't add a bot to a ticket.", ephemeral=True
            )
            return

        existing_overwrite = interaction.channel.overwrites_for(user)
        if existing_overwrite.read_messages:
            await interaction.response.send_message(
                f"⚠️ {user.mention} already has access to this ticket.",
                ephemeral=True,
            )
            return

        try:
            await interaction.channel.set_permissions(
                user,
                read_messages=True,
                send_messages=True,
                attach_files=True,
                reason=f"Added to ticket by {interaction.user}",
            )
            await interaction.response.send_message(
                f"✅ {user.mention} has been added to this ticket."
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to edit this channel's permissions.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Failed to add user: {e}", ephemeral=True
            )

    # Slash Command: /remove — revokes a member's access to this ticket channel
    @app_commands.command(
        name="remove",
        description="Remove a user from this ticket channel",
    )
    @app_commands.describe(user="The member to remove from this ticket")
    async def remove_command(
        self, interaction: discord.Interaction, user: discord.Member
    ):
        if not await self._ticket_command_check(interaction):
            return

        try:
            await interaction.channel.set_permissions(
                user,
                overwrite=None,
                reason=f"Removed from ticket by {interaction.user}",
            )
            await interaction.response.send_message(
                f"✅ {user.mention} has been removed from this ticket."
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to edit this channel's permissions.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Failed to remove user: {e}", ephemeral=True
            )

    # Slash Command: /addticketblacklist — admin-only, dangerous: blocks a
    # user from creating tickets, permanently or for a set duration
    @app_commands.command(
        name="addticketblacklist",
        description="[Dangerous] Block a user from creating tickets",
    )
    @app_commands.describe(
        user="The member to blacklist from creating tickets",
        reason="Why this user is being blacklisted (required)",
        blacklist_type="Voidcore also assigns the Voidcore blacklist role; Regular does not",
        duration_hours="Optional: hours until this expires (leave blank for permanent)",
    )
    @app_commands.default_permissions(administrator=True)
    async def add_ticket_blacklist_command(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str,
        blacklist_type: Literal["voidcore", "regular"],
        duration_hours: float = None,
    ):
        if not await self._dangerous_command_check(interaction):
            return

        if duration_hours is not None and duration_hours <= 0:
            await interaction.response.send_message(
                "❌ Duration must be a positive number of hours, or left blank for permanent.",
                ephemeral=True,
            )
            return

        add_to_blacklist(
            str(user.id),
            reason=reason,
            added_by=str(interaction.user),
            hours=duration_hours,
            blacklist_type=blacklist_type,
        )

        role_note = ""
        if blacklist_type == "voidcore":
            role = interaction.guild.get_role(VOIDCORE_BLACKLIST_ROLE_ID)
            if role:
                try:
                    await user.add_roles(
                        role, reason=f"Voidcore ticket blacklist by {interaction.user}"
                    )
                except Exception:
                    logger.exception(
                        f"Failed to add Voidcore blacklist role to user={user.id}"
                    )
                    role_note = "\n⚠️ Couldn't assign the Voidcore role — check the bot's role position/permissions."
            else:
                role_note = "\n⚠️ Voidcore role not found in this server."

        try:
            append_ticket_log({
                "ticket_id": "global",
                "action": "blacklist_added",
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "target": {"id": str(user.id), "name": str(user)},
                "executor": {
                    "id": str(interaction.user.id),
                    "name": str(interaction.user),
                },
                "reason": reason,
                "duration_hours": duration_hours,
                "blacklist_type": blacklist_type,
            })
        except Exception:
            pass

        duration_text = (
            f"{duration_hours} hour(s)" if duration_hours else "Permanent"
        )

        await send_blacklist_log(
            self.bot,
            "blacklist_added",
            user=f"{user} ({user.id})",
            reason=reason,
            duration=duration_text,
            type=blacklist_type.title(),
            executor=str(interaction.user),
        )

        emb = discord.Embed(
            title="🚫 User Blacklisted",
            description=f"{user.mention} can no longer create tickets.{role_note}",
            color=discord.Color.red(),
        )
        emb.add_field(name="Type", value=blacklist_type.title(), inline=False)
        emb.add_field(name="Reason", value=reason, inline=False)
        emb.add_field(name="Duration", value=duration_text, inline=False)
        emb.set_footer(text="Tickety | Tickety.top")
        await interaction.response.send_message(embed=emb, ephemeral=True)

    # Slash Command: /removeticketblacklist — admin-only, dangerous: lifts a
    # user's ticket-creation blacklist
    @app_commands.command(
        name="removeticketblacklist",
        description="[Dangerous] Remove a user's ticket-creation blacklist",
    )
    @app_commands.describe(
        user="The member to unblacklist",
        reason="Why this user is being unblacklisted (required)",
        blacklist_type="Voidcore also removes the Voidcore blacklist role; Regular does not",
    )
    @app_commands.default_permissions(administrator=True)
    async def remove_ticket_blacklist_command(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str,
        blacklist_type: Literal["voidcore", "regular"],
    ):
        if not await self._dangerous_command_check(interaction):
            return

        removed = remove_from_blacklist(str(user.id))

        if not removed:
            await interaction.response.send_message(
                f"⚠️ {user.mention} wasn't on the ticket blacklist.",
                ephemeral=True,
            )
            return

        role_note = ""
        if blacklist_type == "voidcore":
            role = interaction.guild.get_role(VOIDCORE_BLACKLIST_ROLE_ID)
            if role:
                try:
                    await user.remove_roles(
                        role, reason=f"Voidcore ticket blacklist lifted by {interaction.user}"
                    )
                except Exception:
                    logger.exception(
                        f"Failed to remove Voidcore blacklist role from user={user.id}"
                    )
                    role_note = "\n⚠️ Couldn't remove the Voidcore role — check the bot's role position/permissions."
            else:
                role_note = "\n⚠️ Voidcore role not found in this server."

        try:
            append_ticket_log({
                "ticket_id": "global",
                "action": "blacklist_removed",
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "target": {"id": str(user.id), "name": str(user)},
                "executor": {
                    "id": str(interaction.user.id),
                    "name": str(interaction.user),
                },
                "reason": reason,
                "blacklist_type": blacklist_type,
            })
        except Exception:
            pass

        await send_blacklist_log(
            self.bot,
            "blacklist_removed",
            user=f"{user} ({user.id})",
            reason=reason,
            type=blacklist_type.title(),
            executor=str(interaction.user),
        )

        emb = discord.Embed(
            title="✅ Blacklist Removed",
            description=f"{user.mention} can create tickets again.{role_note}",
            color=discord.Color.green(),
        )
        emb.add_field(name="Type", value=blacklist_type.title(), inline=False)
        emb.add_field(name="Reason", value=reason, inline=False)
        emb.set_footer(text="Tickety | Tickety.top")
        await interaction.response.send_message(embed=emb, ephemeral=True)

    # Slash Command: /rename
    @app_commands.command(name="rename", description="Rename this ticket channel")
    @app_commands.describe(name="The new channel name")
    async def rename_command(self, interaction: discord.Interaction, name: str):
        if not await self._ticket_command_check(interaction):
            return

        new_name = slugify(name)[:90]
        try:
            await interaction.channel.edit(
                name=new_name, reason=f"Renamed by {interaction.user}"
            )
            await interaction.response.send_message(
                f"✅ Renamed to `{new_name}`.", ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Failed to rename: {e}", ephemeral=True
            )

    # Slash Command: /ticketnumber — resumes this ticket's category numbering
    @app_commands.command(
        name="ticketnumber",
        description="Set this ticket category's numbering to resume from a given number",
    )
    @app_commands.describe(
        number="The next new ticket in this category will continue counting up from this number"
    )
    async def ticket_number_command(
        self, interaction: discord.Interaction, number: int
    ):
        if not await self._powerful_command_check(interaction):
            return

        ticket = get_active_ticket(interaction.channel.id)
        prefix = (ticket or {}).get("category_prefix")
        if not prefix:
            await interaction.response.send_message(
                "❌ Couldn't determine this ticket's category.", ephemeral=True
            )
            return

        set_category_counter(prefix, number)
        await interaction.response.send_message(
            f"✅ `{prefix}` numbering set to **{number}** — the next new ticket in "
            f"this category will be `{prefix}-{number + 1:04d}`.",
            ephemeral=True,
        )

    # Slash Command: /move — move this ticket to a different ticket category
    @app_commands.command(
        name="move", description="Move this ticket to a different ticket category"
    )
    @app_commands.describe(category="The ticket category to move this ticket to")
    async def move_command(self, interaction: discord.Interaction, category: str):
        if not await self._powerful_command_check(interaction):
            return

        from utils.storage import get_ticket_categories

        categories = get_ticket_categories()
        target = next((c for c in categories if c.get("label") == category), None)
        if not target:
            await interaction.response.send_message(
                "❌ Unknown category.", ephemeral=True
            )
            return

        prefix = target.get("name_prefix") or slugify(category)
        next_number = increment_category_counter(prefix)
        new_name = f"{prefix}-{next_number:04d}"

        try:
            edit_kwargs = {
                "name": new_name,
                "reason": f"Moved to {category} by {interaction.user}",
            }
            discord_category_id = target.get("discord_category_id")
            if discord_category_id:
                disc_cat = interaction.guild.get_channel(int(discord_category_id))
                if disc_cat:
                    edit_kwargs["category"] = disc_cat

            await interaction.channel.edit(**edit_kwargs)
            update_active_ticket(
                str(interaction.channel.id),
                category_label=category,
                category_prefix=prefix,
                category_number=next_number,
            )
            await interaction.response.send_message(
                f"✅ Moved to **{category}** — renamed to `{new_name}`.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Failed to move ticket: {e}", ephemeral=True
            )

    @move_command.autocomplete("category")
    async def move_command_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        from utils.storage import get_ticket_categories

        categories = get_ticket_categories()
        return [
            app_commands.Choice(name=c["label"], value=c["label"])
            for c in categories
            if current.lower() in c["label"].lower()
        ][:25]

    async def deploy_panel_from_dashboard(
        self,
        channel_id: int,
        title: str,
        description: str,
        category_id: int = None,
        support_role_id: int = None,
        color: str = "#58b9ff",
        image_url: str = None,
        thumbnail_url: str = None,
        footer_text: str = None,
        fields: list = None,
    ):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return False, f"Channel ID {channel_id} could not be found."

        settings = get_settings()
        if category_id:
            settings["default_category_id"] = category_id
        if support_role_id:
            settings["support_role_id"] = support_role_id

        from utils.storage import SETTINGS_FILE

        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=4)

        try:
            hex_val = color.lstrip("#")
            embed_color = discord.Color(int(hex_val, 16))
        except Exception:
            embed_color = discord.Color.blue()

        embed = discord.Embed(
            title=title, description=description, color=embed_color
        )

        if image_url:
            embed.set_image(url=image_url)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        if footer_text:
            embed.set_footer(text=footer_text)

        if fields and isinstance(fields, list):
            for f in fields:
                field_name = f.get("name", "").strip()
                field_value = f.get("value", "").strip()
                is_inline = bool(f.get("inline", False))

                if field_name and field_value:
                    embed.add_field(
                        name=field_name, value=field_value, inline=is_inline
                    )

        try:
            await channel.send(embed=embed, view=self.get_ticket_view())
            return True, "Panel deployed successfully!"
        except discord.Forbidden:
            return (
                False,
                "Bot lacks permission to send messages in that channel.",
            )
        except Exception as e:
            return False, f"Failed to send embed: {str(e)}"

    async def do_close(
        self,
        channel: discord.abc.GuildChannel,
        executor: discord.abc.Snowflake,
        reason: str = "No reason provided.",
    ):
        """Generate transcript, log close, DM creator, then delete the channel after 5 seconds."""
        try:
            try:
                logger.info(
                    f"do_close invoked for channel={getattr(channel,'id',None)} by executor={getattr(executor,'id',executor)} reason={reason}"
                )
            except Exception:
                pass

            messages = []
            async for m in channel.history(limit=1000, oldest_first=True):
                ts = m.created_at.isoformat()
                author_name = str(m.author)
                author_id = getattr(m.author, "id", None)
                try:
                    avatar_url = m.author.display_avatar.url
                except Exception:
                    avatar_url = (
                        getattr(m.author, "avatar_url", None)
                        or "https://cdn.discordapp.com/embed/avatars/0.png"
                    )
                content = m.content or ""

                attachments_data = []
                for a in m.attachments:
                    is_image = bool(
                        a.content_type and a.content_type.startswith("image/")
                    ) or a.filename.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".gif", ".webp")
                    )
                    attachments_data.append({
                        "url": a.url,
                        "filename": a.filename,
                        "is_image": is_image,
                    })

                embeds_data = []
                for e in m.embeds:
                    embeds_data.append({
                        "title": e.title,
                        "description": e.description,
                        "url": e.url if e.url else None,
                        "color": e.color.value if e.color else None,
                        "author_name": e.author.name if e.author else None,
                        "author_icon": e.author.icon_url if e.author else None,
                        "thumbnail_url": e.thumbnail.url if e.thumbnail else None,
                        "image_url": e.image.url if e.image else None,
                        "footer_text": e.footer.text if e.footer else None,
                        "footer_icon": e.footer.icon_url if e.footer else None,
                        "fields": [
                            {
                                "name": f.name,
                                "value": f.value,
                                "inline": f.inline,
                            }
                            for f in e.fields
                        ],
                    })

                components_data = []
                for row in m.components:
                    row_buttons = []
                    for child in getattr(row, "children", []):
                        if isinstance(child, discord.Button):
                            row_buttons.append({
                                "label": child.label,
                                "style": str(child.style).split(".")[-1],
                                "emoji": str(child.emoji) if child.emoji else None,
                                "url": child.url,
                            })
                    if row_buttons:
                        components_data.append(row_buttons)

                messages.append({
                    "ts": ts,
                    "author_name": author_name,
                    "author_id": str(author_id) if author_id else None,
                    "avatar_url": avatar_url,
                    "content": content,
                    "attachments": attachments_data,
                    "embeds": embeds_data,
                    "components": components_data,
                    "is_bot": getattr(m.author, "bot", False),
                })

            filename = f"ticket-{channel.id}.html"
            generated_at = datetime.datetime.utcnow().isoformat() + "Z"

            ticket_meta = {}
            try:
                from utils.storage import get_logs_for_ticket

                logs = get_logs_for_ticket(str(channel.id))
                created = next(
                    (l for l in logs if l.get("action") == "created"), None
                )
                if created:
                    ticket_meta = created
            except Exception:
                ticket_meta = {}

            html_out = build_discord_like_transcript(
                messages, channel.name, ticket_meta, generated_at, filename
            )
            from utils.storage import save_transcript_html
            save_transcript_html(filename, html_out)

            timestamp = datetime.datetime.utcnow().isoformat() + "Z"
            from utils.storage import append_ticket_log, get_logs_for_ticket

            creator_id = None
            try:
                logs = get_logs_for_ticket(str(channel.id))
                created = next(
                    (l for l in logs if l.get("action") == "created"), None
                )
                if created and created.get("creator"):
                    creator_id = created.get("creator").get("id")
            except Exception:
                pass

            append_ticket_log({
                "ticket_id": str(channel.id),
                "ticket_name": channel.name,
                "action": "closed",
                "timestamp": timestamp,
                "executor": {
                    "id": str(getattr(executor, "id", executor)),
                    "name": str(executor),
                },
                "reason": reason,
                "transcript_file": filename,
                "allowed_user_id": creator_id,
            })

            # apply cooldown when the ticket is closed
            try:
                if creator_id:
                    add_cooldown(str(creator_id), hours=8)
            except Exception:
                pass

            await send_ticket_log(
                self.bot,
                "closed",
                ticket=f"#{channel.name}",
                closed_by=f"{executor} ({getattr(executor, 'id', executor)})",
                reason=reason,
            )

            try:
                remove_active_ticket(str(channel.id))
            except Exception:
                pass

            # DM creator transcript
            from utils.storage import generate_transcript_url

            signed_url = generate_transcript_url(
                filename, expires_seconds=3600
            )

            if creator_id:
                try:
                    user = await self.bot.fetch_user(int(creator_id))

                    class LinkView(discord.ui.View):

                        def __init__(self, url):
                            super().__init__(timeout=None)
                            self.add_item(
                                discord.ui.Button(
                                    label="View Transcript", url=url
                                )
                            )

                    await user.send(
                        content=f"Your ticket '{channel.name}' has been closed. The transcript is available for 1 hour.",
                        view=LinkView(signed_url),
                    )
                except Exception as e:
                    logger.exception(f"Failed DM user: {e}")

            try:
                await channel.send("This ticket will be deleted in 5 seconds.")
            except Exception:
                pass

            me = None
            if channel.guild:
                me = channel.guild.get_member(self.bot.user.id)
                if not me:
                    try:
                        me = await channel.guild.fetch_member(
                            self.bot.user.id
                        )
                    except Exception:
                        me = None

            can_delete = False
            if me:
                perms = channel.permissions_for(me)
                guild_perms = getattr(me, "guild_permissions", None)
                can_delete = bool(
                    (guild_perms and guild_perms.manage_channels)
                    or (perms and perms.manage_channels)
                )

            await asyncio.sleep(5)
            ts_now = datetime.datetime.utcnow().isoformat() + "Z"

            if can_delete:
                try:
                    await channel.delete(reason=f"Ticket closed: {reason}")
                    append_ticket_log({
                        "ticket_id": str(channel.id),
                        "ticket_name": channel.name,
                        "action": "deleted",
                        "timestamp": ts_now,
                        "executor": {
                            "id": str(getattr(executor, "id", executor)),
                            "name": str(executor),
                        },
                    })
                except Exception as e:
                    append_ticket_log({
                        "ticket_id": str(channel.id),
                        "ticket_name": channel.name,
                        "action": "delete_failed",
                        "timestamp": ts_now,
                        "executor": {
                            "id": str(getattr(executor, "id", executor)),
                            "name": str(executor),
                        },
                        "error": str(e),
                    })
                    try:
                        await channel.send(
                            "⚠️ Failed to delete channel; archiving instead."
                        )
                        await channel.set_permissions(
                            channel.guild.default_role, read_messages=False
                        )
                        await channel.edit(name=f"closed-{channel.name}")
                    except Exception:
                        pass
        except Exception as global_err:
            logger.exception(f"Error in do_close: {global_err}")


async def setup(bot):
    cog = TicketsCog(bot)
    await bot.add_cog(cog)
    bot.add_view(TicketView(bot))
    bot.add_view(CloseConfirmView(bot))

import asyncio
import datetime
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
    get_ticket_categories,
    get_ticket_open_count,
    increment_ticket_open_count,
    get_ticket_badge_config,
    get_tickets_data,
    get_welcome_message,
    increment_category_counter,
    set_ticket_counter,
    is_on_cooldown,
    is_ticket_channel,
    remove_active_ticket,
    render_ticket_template,
    set_autoclose_disabled,
    set_ticket_owner_close_locked,
    set_category_counter,
    slugify,
    touch_ticket_activity,
    update_active_ticket,
)

try:
    TRANSCRIPT_MESSAGE_LIMIT = max(
        1, int(os.getenv("TICKET_TRANSCRIPT_MESSAGE_LIMIT", "10000"))
    )
except ValueError:
    TRANSCRIPT_MESSAGE_LIMIT = 10000
    logging.getLogger(__name__).warning(
        "Invalid TICKET_TRANSCRIPT_MESSAGE_LIMIT; using 10000."
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
        from utils.dashboard_settings import get_ticket_logging_config

        config = get_ticket_logging_config()
        if not config["enabled"] or action not in config["events"]:
            return
        channel_id = config["log_channel_id"]
        if not channel_id:
            return
        channel = bot.get_channel(int(channel_id))
        if not channel:
            return

        colors = {
            "opened": discord.Color.green(),
            "closed": discord.Color.red(),
            "renamed": discord.Color.blurple(),
            "ticket_started": discord.Color.orange(),
            "ticket_finished": discord.Color.green(),
        }
        titles = {
            "opened": "🎫 Ticket Opened",
            "closed": "🔒 Ticket Closed",
            "renamed": "📝 Ticket Renamed",
            "ticket_started": "🔒 Ticket Work Started",
            "ticket_finished": "✅ Ticket Work Finished",
        }

        embed = discord.Embed(
            title=titles.get(action, action.title()),
            color=colors.get(action, discord.Color.greyple()),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
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
    messages, channel_name, ticket_meta, generated_at_iso, filename,
    omitted_messages=False,
):
    """Render the shared, self-contained liquid-glass conversation archive."""
    from utils.transcripts import render_transcript

    return render_transcript(
        messages, channel_name, ticket_meta, generated_at_iso, filename,
        omitted_messages=omitted_messages,
    )

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
        if owner_close_locked(get_active_ticket(self.target_channel.id), inter.user.id):
            await inter.response.send_message(
                "🔒 This ticket is in progress. Staff must run /end-ticket first.",
                ephemeral=True,
            )
            return
        await inter.response.defer(ephemeral=True)
        try:
            closed = await self.cog.do_close(
                self.target_channel, inter.user, "Closed via button"
            )
            if not closed:
                await inter.followup.send(
                    "⚠️ Could not close ticket; already closing or locked.",
                    ephemeral=True,
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


def owner_close_locked(ticket, user_id):
    """Ticket opener may not close their own in-progress ticket."""
    return bool(ticket and ticket.get("owner_close_locked")
                and str(ticket.get("user_id")) == str(user_id))


def can_manage_ticket(member):
    """Managers and explicitly allowed staff; no open-list permission bypass."""
    from utils.access import get_access_settings, is_admin
    if member.guild_permissions.manage_channels or is_admin(member.id):
        return True
    config = get_access_settings()
    allowed_users = {str(x) for x in config.get("basic_command_users", [])}
    allowed_roles = {str(x) for x in config.get("basic_command_roles", [])}
    return str(member.id) in allowed_users or bool(
        allowed_roles.intersection(str(role.id) for role in getattr(member, "roles", []))
    )


def ticket_notice(title, description, *, okay=True):
    """Components V2 status announcement."""
    view = discord.ui.LayoutView()
    container = discord.ui.Container(
        accent_color=discord.Color.green() if okay else discord.Color.orange()
    )
    container.add_item(discord.ui.TextDisplay("## " + title))
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(description))
    view.add_item(container)
    return view


class CloseRequestButton(discord.ui.Button):
    def __init__(self, panel, accept):
        super().__init__(
            label="Yes, close it" if accept else "No, keep it open",
            style=discord.ButtonStyle.danger if accept else discord.ButtonStyle.secondary,
            custom_id=f"reqclose:{panel.channel.id}:{panel.revision}:{int(accept)}",
        )
        self.panel = panel
        self.accept = accept

    async def callback(self, interaction):
        await self.panel.decide(interaction, self.accept)


class RequestCloseView(discord.ui.LayoutView):
    """Components V2 close prompt. Only the ticket opener may answer."""

    def __init__(self, cog, channel, creator_id, staff, reason, revision):
        super().__init__(timeout=600)
        self.cog = cog
        self.channel = channel
        self.creator_id = str(creator_id)
        self.reason = reason
        self.revision = int(revision)
        self.decided = False
        container = discord.ui.Container(accent_color=discord.Color.orange())
        container.add_item(discord.ui.TextDisplay("## 🔐 Ticket Close Request"))
        container.add_item(discord.ui.Separator())
        safe_reason = discord.utils.escape_mentions(reason) if reason else "No reason provided"
        container.add_item(discord.ui.TextDisplay(
            f"<@{creator_id}>, {staff.mention} has requested to close this ticket.\n\n"
            f"**Reason:** {safe_reason}\n\n"
            "Only the ticket opener can approve or decline."
        ))
        container.add_item(discord.ui.ActionRow(
            CloseRequestButton(self, True), CloseRequestButton(self, False)
        ))
        self.add_item(container)

    async def interaction_check(self, interaction):
        if str(interaction.user.id) != self.creator_id:
            await interaction.response.send_message(
                "❌ Only the ticket opener may respond.", ephemeral=True
            )
            return False
        ticket = get_active_ticket(self.channel.id)
        if not ticket or str(ticket.get("user_id")) != self.creator_id:
            await interaction.response.send_message(
                "⚠️ This ticket is no longer active.", ephemeral=True
            )
            return False
        if self.decided or int(ticket.get("owner_close_revision", 0)) != self.revision:
            await interaction.response.send_message(
                "⚠️ This request is outdated. Ask staff to send a new one.",
                ephemeral=True,
            )
            return False
        if owner_close_locked(ticket, interaction.user.id):
            await interaction.response.send_message(
                "🔒 This ticket is in progress. Staff must use /end-ticket first.",
                ephemeral=True,
            )
            return False
        return True

    async def decide(self, interaction, accept):
        self.decided = True
        self.stop()
        await interaction.response.edit_message(
            view=ticket_notice(
                "✅ Close Approved" if accept else "🛑 Close Declined",
                f"{interaction.user.mention} approved the request. Closing…"
                if accept else f"{interaction.user.mention} declined; ticket stays open.",
                okay=not accept,
            )
        )
        if not accept:
            return
        close_reason = (f"Opener accepted /request-close: {self.reason}" if self.reason
                        else "Opener accepted /request-close")
        try:
            closed = await self.cog.do_close(self.channel, interaction.user, close_reason)
            if not closed:
                await interaction.followup.send(
                    "⚠️ Could not close this ticket. It may be locked or already closing.",
                    ephemeral=True,
                )
        except Exception:
            logger.exception("Request-close acceptance failed")
            await interaction.followup.send("❌ Closing failed. Ask staff for help.", ephemeral=True)


def build_rename_announcement(old_name: str, new_name: str, executor: discord.abc.User):
    """Build the in-channel "ticket renamed" announcement using Components V2
    (discord.ui.LayoutView) — a plain markdown-styled layout rather than a
    classic embed. Returns None if the running discord.py version doesn't
    support Components V2 yet, so callers can fall back to a normal message.
    """
    try:
        layout_cls = getattr(discord.ui, "LayoutView", None)
        container_cls = getattr(discord.ui, "Container", None)
        text_display_cls = getattr(discord.ui, "TextDisplay", None)
        separator_cls = getattr(discord.ui, "Separator", None)
        if not (layout_cls and container_cls and text_display_cls):
            return None

        class RenameAnnouncement(layout_cls):
            def __init__(self):
                super().__init__()
                container = container_cls(accent_color=discord.Color.blurple())
                container.add_item(
                    text_display_cls("### 📝 Ticket Renamed")
                )
                if separator_cls:
                    container.add_item(separator_cls())
                container.add_item(
                    text_display_cls(
                        f"**Before:** `{old_name}`\n"
                        f"**After:** `{new_name}`\n"
                        f"**Renamed by:** {executor.mention}"
                    )
                )
                self.add_item(container)

        return RenameAnnouncement()
    except Exception:
        logger.exception("Failed to build Components V2 rename announcement")
        return None


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

        ticket = get_active_ticket(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message(
                "❌ This ticket is not active.", ephemeral=True
            )
            return
        if owner_close_locked(ticket, interaction.user.id):
            await interaction.response.send_message(
                "🔒 Ticket is in progress. Wait for staff to use /end-ticket.",
                ephemeral=True,
            )
            return
        if (str(interaction.user.id) != str(ticket.get("user_id"))
                and not can_manage_ticket(interaction.user)):
            await interaction.response.send_message(
                "❌ Only the opener or authorized staff can close this ticket.",
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

        # Badge verification gate: a category can require the configured Roblox
        # badge, or block users who have it, starting from the member's very
        # first ticket in that category. The final decision is made server-side
        # through RoVer + Roblox, never by the browser button.
        #
        # Every one of these variables is set up-front with a safe "gate off"
        # default before the try block runs. Previously gate_category was only
        # ever assigned inside the try, so if get_ticket_categories() (or
        # anything before it finished) raised, the except clause below did NOT
        # backfill gate_category — the very next line then hit a bare
        # NameError, which happened before any interaction.response call had
        # been made. Discord has nothing to show for that but "the application
        # did not respond", since the 3-second ack window just ran out on an
        # unhandled crash. This is likely exactly what's been happening.
        gate_category = None
        badge_mode = "off"
        badge_cfg = {"badge_id": "", "verification_url": "https://rover.link/verify/"}
        try:
            categories_for_gate = get_ticket_categories()
            gate_category = next((c for c in categories_for_gate if c.get("label") == selection), None)
            badge_mode = (gate_category or {}).get("badge_mode", "off")
            badge_cfg = get_ticket_badge_config()
        except Exception:
            logger.exception(
                "ticket_select: badge-gate lookup failed, opening the ticket "
                "without the badge gate rather than failing the interaction"
            )
            gate_category, badge_mode = None, "off"
            badge_cfg = {"badge_id": "", "verification_url": "https://rover.link/verify/"}

        category_badges = (gate_category or {}).get("badge_ids") or []
        category_badges = [str(b).strip() for b in category_badges if str(b).strip().isdigit()]
        blocked_badges = badge_cfg.get("blocked_badges") or []
        global_block_badges = [str(b).strip() for b in blocked_badges if str(b).strip().isdigit()]
        if badge_mode == "block_badge":
            block_badges = category_badges or global_block_badges
            if not block_badges and badge_cfg.get("badge_id"):
                block_badges = [str(badge_cfg.get("badge_id"))]
        else:
            block_badges = []
        required_badges = category_badges or ([str(badge_cfg.get("badge_id")).strip()] if str(badge_cfg.get("badge_id") or "").strip().isdigit() else [])
        badge_gate_required = (
            (badge_mode == "require_badge" and bool(required_badges)) or
            (badge_mode == "block_badge" and bool(block_badges))
        )

        async def show_badge_gate():
            from utils.storage import get_dashboard_base_url
            verification_url = badge_cfg.get("verification_url") or "https://rover.link/verify/"
            view = discord.ui.View(timeout=600)
            view.add_item(discord.ui.Button(label="🔗 Verify with RoVer", style=discord.ButtonStyle.link, url=verification_url))
            check = discord.ui.Button(label="✅ I've verified", style=discord.ButtonStyle.success)

            async def _send_continue_button(target_interaction: discord.Interaction):
                # A modal can only ever be shown as the FIRST response to an
                # interaction — never through a followup/webhook, and never
                # after response.defer() has already been used. Since the
                # badge check above needs to defer (it makes network calls
                # that can take longer than Discord's 3-second ack window),
                # this same interaction can no longer show a modal. Instead,
                # hand the user a fresh button; that button's own interaction
                # is un-deferred, so its callback can call send_modal() on it
                # directly and it will actually open.
                continue_view = discord.ui.View(timeout=300)
                continue_button = discord.ui.Button(label="🎫 Continue to Ticket Form", style=discord.ButtonStyle.success)

                async def continue_callback(continue_interaction: discord.Interaction):
                    if continue_interaction.user.id != interaction.user.id:
                        await continue_interaction.response.send_message("❌ This verification belongs to another user.", ephemeral=True)
                        return
                    await continue_interaction.response.send_modal(modal)

                continue_button.callback = continue_callback
                continue_view.add_item(continue_button)
                await target_interaction.followup.send(
                    "✅ Verification passed. Click below to continue to the ticket form.",
                    view=continue_view,
                    ephemeral=True,
                )

            async def _send_needs_consent(target_interaction, reason):
                # RoVer confirms the account is verified but the user hasn't
                # granted this server (or this bot's API key) permission to
                # reveal it yet. Being "verified" no longer implies that
                # permission — see https://rover.link/help/username-privacy-and-consent.
                # Give them the exact place to grant it and let them retry
                # the same "I've verified" button afterward.
                from utils.rover_verification import ROVER_CONSENT_URL
                view = discord.ui.View(timeout=600)
                view.add_item(discord.ui.Button(label="🔓 Grant RoVer Access", style=discord.ButtonStyle.link, url=ROVER_CONSENT_URL))
                await target_interaction.followup.send(
                    f"🔒 You're verified with RoVer, but you haven't granted this server permission to see your linked Roblox account yet. "
                    f"{reason}\n\nClick **Grant RoVer Access** above, allow this server, then press **✅ I've verified** again.",
                    view=view,
                    ephemeral=True,
                )

            async def check_callback(check_interaction: discord.Interaction):
                if check_interaction.user.id != interaction.user.id:
                    await check_interaction.response.send_message("❌ This verification belongs to another user.", ephemeral=True)
                    return
                await check_interaction.response.defer(ephemeral=True)
                guild_id = check_interaction.guild.id if check_interaction.guild else None
                if badge_mode == "require_badge":
                    from utils.rover_verification import check_required_badges_for_discord_user
                    owned_badges, reason, _details, ok, needs_consent = await asyncio.to_thread(
                        check_required_badges_for_discord_user, guild_id, check_interaction.user.id, required_badges
                    )
                    if needs_consent:
                        await _send_needs_consent(check_interaction, reason)
                        return
                    if not ok:
                        # The check itself couldn't run (RoVer/Roblox unreachable
                        # or blocked) — let the ticket through rather than stall
                        # the user indefinitely, but make the failure visible so
                        # staff know this badge check did not actually run.
                        await check_interaction.followup.send(
                            f"⚠️ There was an issue verifying: {reason} Continuing without a completed badge check — a staff member may want to double check this ticket.",
                            ephemeral=True,
                        )
                        await _send_continue_button(check_interaction)
                        return
                    allowed = bool(owned_badges)
                    if allowed:
                        await _send_continue_button(check_interaction)
                    else:
                        badge_text = ", ".join(required_badges)
                        await check_interaction.followup.send(
                            f"❌ You do not have access to open this ticket. You need at least one of the configured Roblox badges: **{badge_text}**. {reason}",
                            ephemeral=True,
                        )
                else:
                    from utils.rover_verification import check_blocked_badges_for_discord_user
                    owned_badges, reason, _details, ok, needs_consent = await asyncio.to_thread(
                        check_blocked_badges_for_discord_user, guild_id, check_interaction.user.id, block_badges
                    )
                    if needs_consent:
                        await _send_needs_consent(check_interaction, reason)
                        return
                    if not ok:
                        # Same reasoning as the require_badge path above: don't
                        # stall the ticket on an unreachable/blocked check, but
                        # flag it clearly so staff know verification didn't run.
                        await check_interaction.followup.send(
                            f"⚠️ There was an issue verifying: {reason} Continuing without a completed badge check — a staff member may want to double check this ticket.",
                            ephemeral=True,
                        )
                        await _send_continue_button(check_interaction)
                        return
                    if not owned_badges:
                        await _send_continue_button(check_interaction)
                    else:
                        owned_text = ", ".join(owned_badges)
                        await check_interaction.followup.send(
                            f"❌ You do not have access to open this ticket. Your linked Roblox account has a blocked badge: **{owned_text}**.",
                            ephemeral=True,
                        )

            check.callback = check_callback
            view.add_item(check)
            title = "🔐 Ticket Verification Required"
            if badge_mode == "require_badge":
                desc = "This ticket category requires verification. You must verify with RoVer and own at least one configured Roblox badge before continuing."
            else:
                desc = "This ticket category requires verification. You must verify with RoVer so the bot can confirm that you do not own any badge from this category's blocked list."
            embed = discord.Embed(title=title, description=desc, color=discord.Color.blurple())
            if badge_mode == "require_badge":
                display_badges = ", ".join(required_badges)
                if len(display_badges) > 1024:
                    display_badges = display_badges[:1010] + "…"
                embed.add_field(name="Required Roblox Badge IDs", value=display_badges or "None configured", inline=False)
            else:
                display_badges = ", ".join(block_badges)
                if len(display_badges) > 1024:
                    display_badges = display_badges[:1010] + "…"
                embed.add_field(name="Blocked Badge IDs", value=display_badges or "None configured", inline=False)
            embed.set_footer(text="Press ‘I've verified’ only after completing RoVer verification.")
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        # Modal is constructed below; delay the gate until after modal creation.

        # check blacklist (individually-blacklisted user IDs) — only
        # "regular" type blocks ALL ticket categories outright. "voidcore"
        # type intentionally does NOT block here; it instead relies on the
        # Voidcore role (assigned alongside the blacklist) being set as a
        # per-category blacklist role on whichever categories should stay
        # off-limits, so the user can still open other ticket types.
        try:
            bl = get_blacklist_data()
            my_entry = next(
                (
                    e for e in bl.get("blacklisted_users", [])
                    if str(e.get("user_id")) == str(interaction.user.id)
                ),
                None,
            )
            if my_entry and my_entry.get("blacklist_type", "regular") != "voidcore":
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

                self.tds_level = discord.ui.TextInput(
                    label="🏆 What level are you in TDS? *",
                    placeholder="e.g. 50",
                    required=True,
                    style=discord.TextStyle.short,
                    max_length=10,
                )

                self.add_item(self.timezone)
                self.add_item(self.display_name)
                self.add_item(self.can_join)
                self.add_item(self.tds_level)

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

                try:
                    # Roles granted Pin Message Access get Discord's actual
                    # Pin Messages permission inside this ticket channel —
                    # Discord split this out from Manage Messages, so this
                    # grants pinning only, nothing else. Falls back to
                    # Manage Messages only if the installed discord.py
                    # version predates the dedicated Pin Messages flag.
                    from utils.access import get_pin_message_role_ids

                    for rid in get_pin_message_role_ids():
                        pin_role = guild.get_role(int(rid))
                        if not pin_role:
                            continue
                        existing = overwrites.get(pin_role)
                        if existing is None:
                            existing = discord.PermissionOverwrite(
                                read_messages=True, send_messages=True
                            )
                        if hasattr(existing, "pin_messages"):
                            existing.pin_messages = True
                        else:
                            existing.manage_messages = True
                        overwrites[pin_role] = existing
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
                from utils.storage import increment_ticket_counter

                # Shared/combined numbering: every category draws from the
                # SAME running sequence, so fallen-carry-0001 and
                # frost-carry-0002 share one continuous count rather than
                # each category numbering independently from 1.
                ticket_number = increment_ticket_counter()
                category_number = ticket_number
                channel_name = f"{prefix}-{ticket_number:04d}"

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
                        "tds_level": self.tds_level.value or "",
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
                        if welcome_cfg.get("show_tds_level", True) and self.tds_level.value:
                            embed.add_field(
                                name="TDS Level",
                                value=self.tds_level.value,
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

                        welcome_msg = await ticket_channel.send(
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
                        if welcome_cfg.get("show_tds_level", True) and self.tds_level.value:
                            plain_parts.append(f"**TDS Level:** {self.tds_level.value}")
                        if category_cfg and category_cfg.get("open_note"):
                            plain_parts.append(f"**Note:** {category_cfg['open_note']}")
                        welcome_msg = await ticket_channel.send(content="\n".join(plain_parts))

                    # Pin the welcome message — it's the one place that has
                    # both the "ticket created" info and the opener's
                    # answers (timezone/display name/TDS level/etc), so
                    # staff can see it without scrolling once the channel
                    # fills up with conversation.
                    try:
                        await welcome_msg.pin(
                            reason="Ticket welcome message — kept visible for staff reference"
                        )
                    except discord.Forbidden:
                        logger.warning(
                            f"Missing permission to pin welcome message in channel={ticket_channel.id}"
                        )
                    except discord.HTTPException:
                        # Most commonly Discord's 50-pin-per-channel cap
                        logger.warning(
                            f"Failed to pin welcome message in channel={ticket_channel.id} (pin limit reached?)"
                        )
                    except Exception:
                        logger.exception(
                            f"Unexpected error pinning welcome message in channel={ticket_channel.id}"
                        )

                    # Attach close button view
                    await ticket_channel.send(
                        view=CloseConfirmView(outer_view.bot)
                    )

                    # Log creation
                    ticket_id = str(ticket_channel.id)
                    timestamp = datetime.datetime.utcnow().isoformat() + "Z"
                    # ticket_number was already drawn from the shared global
                    # counter above (used for the channel name too) - reuse
                    # it here rather than incrementing a second time.

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
                                "tds_level": self.tds_level.value,
                            },
                        })
                    except Exception as e:
                        logger.exception(
                            f"Failed to append ticket creation log for channel={ticket_channel.id}: {e}"
                        )

                    increment_ticket_open_count(user.id, self.selection)

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

        if badge_gate_required:
            await show_badge_gate()
            return

        from utils.storage import get_april_fools_enabled, get_dashboard_base_url

        if get_april_fools_enabled():
            from utils.access import create_ticket_ad_verification, consume_ticket_ad_verification

            # If they already finished the ad on a previous attempt (picked a
            # category, went and watched it, came back and picked again), this
            # consumes that completed session and lets them straight through.
            if consume_ticket_ad_verification(interaction.user.id):
                await interaction.response.send_modal(modal)
                return

            base_url = get_dashboard_base_url()
            token = None
            try:
                token = create_ticket_ad_verification(interaction.user.id)
            except ValueError:
                token = None

            if token and base_url:
                ad_url = f"{base_url}/ticket-ad/{token}"
                gate_view = discord.ui.View(timeout=600)
                gate_view.add_item(
                    discord.ui.Button(
                        label="📺 Watch Ad",
                        style=discord.ButtonStyle.link,
                        url=ad_url,
                    )
                )

                watched_button = discord.ui.Button(
                    label="✅ I've watched it", style=discord.ButtonStyle.success
                )

                async def _on_watched(watched_interaction: discord.Interaction, _user_id=interaction.user.id, _modal=modal):
                    if watched_interaction.user.id != _user_id:
                        await watched_interaction.response.send_message(
                            "❌ This isn't your ad to confirm.", ephemeral=True
                        )
                        return
                    from utils.access import consume_ticket_ad_verification
                    if consume_ticket_ad_verification(watched_interaction.user.id):
                        await watched_interaction.response.send_modal(_modal)
                    else:
                        await watched_interaction.response.send_message(
                            "⏳ Looks like the countdown on the ad page isn't done yet (or you "
                            "haven't opened it). Click **Watch Ad** above, wait for it to finish, "
                            "press Continue there, then press this button again.",
                            ephemeral=True,
                        )

                watched_button.callback = _on_watched
                gate_view.add_item(watched_button)

                ad_embed = discord.Embed(
                    title="📺 A Message From Our Totally Real Sponsors",
                    description=(
                        "Before your ticket opens, please watch the ad on our website, "
                        "then come back here and press **I've watched it**.\n\n"
                        "*(April Fools! ...mostly. You do actually need to watch it.)*"
                    ),
                    color=discord.Color.gold(),
                )
                ad_embed.set_footer(text="🃏 April Fools Mode is enabled on this server")
                await interaction.response.send_message(
                    embed=ad_embed, view=gate_view, ephemeral=True
                )
                return
            # No PUBLIC_BASE_URL configured, so there's nowhere to send them —
            # fail open rather than locking every ticket behind a dead link.

        await interaction.response.send_modal(modal)


# --- TICKETS COG & SLASH COMMANDS ---
class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Keep close operations idempotent while transcript generation and
        # channel deletion are still in progress.
        self._closing_ticket_ids = set()
        # Prevent the leave event and periodic recovery job racing to close
        # the same ticket. The do_close guard separately covers all close paths.
        self._departing_ticket_ids = set()
        self._last_member_reconciliation_at = 0.0

    def get_ticket_view(self):
        return TicketView(self.bot)

    # Message Context Menu: right-click a message -> Apps -> 📌 Toggle Pin
    #
    # NOTE: @app_commands.context_menu() cannot decorate a method defined
    # inside a class body — discord.py raises "context menus cannot be
    # defined inside a class" at import time if you try. The actual
    # implementation lives in the module-level `_toggle_pin_message`
    # function below and is manually attached to the bot's command tree in
    # cog_load/cog_unload.

    async def cog_load(self):
        self.autoclose_watcher.start()
        self.bot.tree.add_command(toggle_pin_message_context_menu)

    def cog_unload(self):
        self.autoclose_watcher.cancel()
        self.bot.tree.remove_command(
            toggle_pin_message_context_menu.name,
            type=toggle_pin_message_context_menu.type,
        )

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

    async def _close_departed_ticket(self, channel):
        """Close a departed owner's ticket once, preserving do_close's transcript safeguards."""
        ticket_id = str(channel.id)
        if ticket_id in self._departing_ticket_ids or ticket_id in self._closing_ticket_ids:
            return
        self._departing_ticket_ids.add(ticket_id)
        try:
            # A manual or simultaneous close might have removed the record.
            if not get_active_ticket(ticket_id):
                return
            await channel.send(
                "🔒 This ticket is being automatically closed because "
                "the person who opened it is no longer in the server."
            )
            await self.do_close(
                channel,
                self.bot.user,
                reason="Automatically closed — opener left the server.",
            )
        except Exception:
            logger.exception("Failed to close departed owner's ticket channel=%s", ticket_id)
        finally:
            self._departing_ticket_ids.discard(ticket_id)

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent):
        """Use an uncached gateway leave event when Guild Members intent is enabled."""
        if not self.bot.intents.members:
            return
        user_id = getattr(getattr(payload, "user", None), "id", None)
        if user_id is None:
            return
        try:
            tickets = list(get_tickets_data().get("active_tickets", []))
        except Exception:
            logger.exception("Failed to load tickets on member departure")
            return
        for ticket in tickets:
            if str(ticket.get("user_id")) != str(user_id):
                continue
            try:
                channel = self.bot.get_channel(int(ticket["channel_id"]))
            except (KeyError, ValueError, TypeError):
                continue
            # Ticket channel IDs are globally unique. Match the guild too so
            # a departure from an unrelated server never closes a ticket.
            if not channel or getattr(getattr(channel, "guild", None), "id", None) != payload.guild_id:
                continue
            await self._close_departed_ticket(channel)

    @tasks.loop(minutes=10)
    async def autoclose_watcher(self):
        """Check inactivity deadlines every 10m; separately reconcile missed departures.

        With Guild Members intent: departures close via on_raw_member_remove;
        verify membership every 12h to catch events missed while offline.
        Without it: retain the REST membership check about every 2h.
        """
        try:
            data = get_tickets_data()
        except Exception:
            logger.exception("autoclose_watcher: failed to load tickets data")
            return

        now = time.time()
        membership_interval = 12 * 3600 if self.bot.intents.members else 2 * 3600
        reconcile_members = (
            now - self._last_member_reconciliation_at >= membership_interval
        )
        if reconcile_members:
            self._last_member_reconciliation_at = now

        if AUTOCLOSE_TEST_MODE:
            REMINDER_AFTER = 30            # 30 seconds
            CLOSE_AFTER = 60                # 1 minute
        else:
            REMINDER_AFTER = 12 * 3600
            CLOSE_AFTER = 24 * 3600

        for ticket in list(data.get("active_tickets", [])):
            channel_id = ticket.get("channel_id")
            user_id = ticket.get("user_id")

            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                continue
            guild = channel.guild

            # Event-driven departures are immediate when Guild Members is on.
            # A periodic REST reconciliation still handles offline/missed events.
            if reconcile_members:
                try:
                    member = guild.get_member(int(user_id))
                    if member is None:
                        try:
                            await guild.fetch_member(int(user_id))
                        except discord.NotFound:
                            await self._close_departed_ticket(channel)
                            continue
                        except discord.HTTPException:
                            # An API failure is not evidence that the user left.
                            logger.warning(
                                "Could not verify membership for ticket channel=%s",
                                channel_id,
                            )
                        finally:
                            # Pace checks even with privileged intents enabled.
                            await asyncio.sleep(1.2)
                except Exception:
                    logger.exception(
                        "autoclose_watcher: membership check failed for channel=%s",
                        channel_id,
                    )

            if ticket.get("autoclose_disabled"):
                continue

            last_activity = ticket.get("last_activity") or ticket.get("created_at") or now
            elapsed = now - float(last_activity)

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
                    await channel.send(
                        content=(
                            f"<@{user_id}> ⏳ This ticket will **automatically close in 12 hours** "
                            "due to inactivity. Send a message here to keep it open, or ask staff "
                            "to run `/autoclose disable` to stop autoclose entirely."
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

    async def _require_ticket_channel(self, interaction: discord.Interaction) -> bool:
        """Just the "must be in an active ticket channel" half of
        _ticket_command_check, without the Manage Channels requirement -
        used by _basic_command_check, which has its own more flexible
        permission logic (role/user access OR Manage Channels, either
        one is enough)."""
        if not interaction.channel or not is_ticket_channel(interaction.channel.id):
            await interaction.response.send_message(
                "❌ This command can only be used inside an active ticket channel.",
                ephemeral=True,
            )
            return False
        return True

    async def _basic_command_check(self, interaction: discord.Interaction) -> bool:
        """Extra guard for the non-dangerous ticket-management commands
        (/close, /request-close, /requestclose, /start-ticket, /end-ticket,
        /autoclose, /add, /remove, /rename).

        Allowed if EITHER:
        - the user has a role/user ID granted in Access Control's Basic
          Command Access list (or is admin), OR
        - the user simply has the native Manage Channels permission

        Either path alone is enough - they're not stacked on top of each
        other. This lets you grant these commands to a role that doesn't
        have Manage Channels at the server level (e.g. Trial Staff),
        while staff who already have Manage Channels keep working even
        if nobody remembered to add their specific role to the list.
        """
        if not await self._require_ticket_channel(interaction):
            return False

        if not can_manage_ticket(interaction.user):
            await interaction.response.send_message(
                "❌ Only authorized staff (Manage Channels or explicit Basic Command "
                "Access) may manage tickets.", ephemeral=True
            )
            return False
        return True

    async def _pin_command_check(self, interaction: discord.Interaction) -> bool:
        """Guard for pinning/unpinning messages inside a ticket channel.

        Strictly gated by Access Control's Pin Message Access list (roles
        or user IDs explicitly added there), plus admins. Discord's native
        Manage Messages / channel-level Pin Messages permission is
        intentionally NOT a fallback here — access is controlled entirely
        through the bot's own role list, so someone with Manage Messages
        but no matching role still can't use this until they're added.
        """
        if not await self._require_ticket_channel(interaction):
            return False

        from utils.access import has_pin_message_access

        member_role_ids = [str(r.id) for r in getattr(interaction.user, "roles", [])]
        if not has_pin_message_access(interaction.user.id, member_role_ids):
            await interaction.response.send_message(
                "❌ You need a role or user ID granted in the Pin Message Access "
                "list (Access Control) to pin or unpin messages.",
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
        if not await self._basic_command_check(interaction):
            return

        await interaction.response.send_message(
            f"🔒 Closing this ticket"
            f"{f' — reason: {reason}' if reason else ''}...",
            ephemeral=True,
        )
        try:
            closed = await self.do_close(
                interaction.channel, interaction.user, reason or "No reason provided."
            )
            if not closed:
                await interaction.followup.send(
                    "⚠️ This ticket could not be closed. It may be locked or "
                    "already closing.", ephemeral=True
                )
        except Exception as e:
            try:
                await interaction.followup.send(
                    f"❌ Failed to close ticket: {e}", ephemeral=True
                )
            except Exception:
                pass

    # Publicly discoverable slash commands; staff authorization is checked
    # at execution, not hidden in Discord's command picker.
    @app_commands.command(
        name="request-close",
        description="Ask the ticket opener to approve or decline closing it",
    )
    @app_commands.describe(reason="Optional reason for the request")
    async def request_close_command(self, interaction: discord.Interaction, reason: str = None):
        await self._send_request_close(interaction, reason)

    @app_commands.command(
        name="requestclose",
        description="Legacy alias for request-close",
    )
    @app_commands.describe(reason="Optional reason for the request")
    async def request_close_legacy(self, interaction: discord.Interaction, reason: str = None):
        await self._send_request_close(interaction, reason)

    async def _send_request_close(self, interaction, reason=None):
        if not await self._basic_command_check(interaction):
            return
        ticket = get_active_ticket(interaction.channel.id)
        if not ticket or not ticket.get("user_id"):
            await interaction.response.send_message(
                "❌ Could not determine the ticket opener.", ephemeral=True
            )
            return
        if ticket.get("owner_close_locked"):
            await interaction.response.send_message(
                view=ticket_notice(
                    "🔒 Ticket In Progress",
                    "The opener cannot approve a close until staff use /end-ticket.",
                    okay=False,
                ), ephemeral=True,
            )
            return
        reason = (reason.strip()[:400] if reason and reason.strip() else None)
        await interaction.response.send_message(view=RequestCloseView(
            self, interaction.channel, ticket["user_id"], interaction.user,
            reason, ticket.get("owner_close_revision", 0),
        ))

    @app_commands.command(
        name="start-ticket",
        description="Start ticket work and block the opener from closing it",
    )
    async def start_ticket_command(self, interaction: discord.Interaction):
        if not await self._basic_command_check(interaction):
            return
        ticket = get_active_ticket(interaction.channel.id)
        if ticket and ticket.get("owner_close_locked"):
            await interaction.response.send_message(
                view=ticket_notice(
                    "🔒 Ticket Already In Progress",
                    "Owner closing is already disabled. Finish with /end-ticket.",
                    okay=False,
                ), ephemeral=True,
            )
            return
        try:
            changed = set_ticket_owner_close_locked(interaction.channel.id, True)
        except RuntimeError:
            await interaction.response.send_message(
                "❌ Could not save the ticket lock. Please retry.", ephemeral=True
            )
            return
        if not changed:
            await interaction.response.send_message(
                "⚠️ Ticket state changed. Please retry.", ephemeral=True
            )
            return
        await interaction.response.send_message(view=ticket_notice(
            "🔒 Ticket In Progress",
            f"**Started by:** {interaction.user.mention}\n"
            "**Ticket opener:** Cannot close\n"
            "**Authorized staff:** Can still close\n\n"
            "Use /end-ticket to allow the opener to close again.",
        ))
        append_ticket_log({
            "ticket_id": str(interaction.channel.id),
            "action": "ticket_started",
            "ticket_name": interaction.channel.name,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "executor": {"id": str(interaction.user.id), "name": str(interaction.user)},
        })

        await send_ticket_log(self.bot, "ticket_started", ticket=interaction.channel.mention,
                              started_by=f"{interaction.user} ({interaction.user.id})")

    @app_commands.command(
        name="end-ticket",
        description="Finish ticket work and allow the opener to close again",
    )
    async def end_ticket_command(self, interaction: discord.Interaction):
        if not await self._basic_command_check(interaction):
            return
        ticket = get_active_ticket(interaction.channel.id)
        if not ticket or not ticket.get("owner_close_locked"):
            await interaction.response.send_message(
                view=ticket_notice(
                    "✅ Ticket Not In Progress",
                    "The opener may already close. Use /start-ticket to lock it.",
                    okay=False,
                ), ephemeral=True,
            )
            return
        try:
            changed = set_ticket_owner_close_locked(interaction.channel.id, False)
        except RuntimeError:
            await interaction.response.send_message(
                "❌ Could not save the ticket state. Please retry.", ephemeral=True
            )
            return
        if not changed:
            await interaction.response.send_message(
                "⚠️ Ticket state changed. Please retry.", ephemeral=True
            )
            return
        await interaction.response.send_message(view=ticket_notice(
            "✅ Ticket Work Finished",
            f"**Finished by:** {interaction.user.mention}\n"
            "**Ticket opener:** Can close again\n"
            "**Authorized staff:** Can still close\n\n"
            "Old close requests expire. Staff may send a new /request-close.",
        ))
        append_ticket_log({
            "ticket_id": str(interaction.channel.id),
            "action": "ticket_finished",
            "ticket_name": interaction.channel.name,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "executor": {"id": str(interaction.user.id), "name": str(interaction.user)},
        })

        await send_ticket_log(self.bot, "ticket_finished", ticket=interaction.channel.mention,
                              finished_by=f"{interaction.user} ({interaction.user.id})")

    # Slash Command: /autoclose — staff-only, toggles the 24h-inactivity
    # autoclose for this specific ticket on or off
    @app_commands.command(
        name="autoclose",
        description="Turn this ticket's 24h-inactivity autoclose on or off",
    )
    @app_commands.describe(state="Whether autoclose should be enabled or disabled for this ticket")
    async def autoclose_command(
        self, interaction: discord.Interaction, state: Literal["enable", "disable"]
    ):
        if not await self._basic_command_check(interaction):
            return

        ticket = get_active_ticket(interaction.channel.id)

        if state == "disable":
            if ticket and ticket.get("autoclose_disabled"):
                await interaction.response.send_message(
                    "🛑 Autoclose is already off for this ticket.", ephemeral=True
                )
                return

            set_autoclose_disabled(interaction.channel.id, True)
            await interaction.response.send_message(
                "🛑 Autoclose turned off — this ticket will no longer close itself due to inactivity."
            )
        else:
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
        if not await self._basic_command_check(interaction):
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
        if not await self._basic_command_check(interaction):
            return

        try:
            overwrite = interaction.channel.overwrites_for(user)
            overwrite.read_messages = False
            await interaction.channel.set_permissions(
                user,
                overwrite=overwrite,
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

    # Message Context Menu: right-click a message -> Apps -> 📌 Toggle Pin
    # (actual implementation is the module-level toggle_pin_message_context_menu below)

    # Slash Command: /rename
    @app_commands.command(name="rename", description="Rename this ticket channel")
    @app_commands.describe(name="The new channel name")
    async def rename_command(self, interaction: discord.Interaction, name: str):
        if not await self._basic_command_check(interaction):
            return

        old_name = interaction.channel.name
        new_name = slugify(name)[:90]

        if new_name == old_name:
            await interaction.response.send_message(
                f"⚠️ The channel is already named `{old_name}`.", ephemeral=True
            )
            return

        try:
            await interaction.channel.edit(
                name=new_name, reason=f"Renamed by {interaction.user}"
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Failed to rename: {e}", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ Renamed to `{new_name}`.", ephemeral=True
        )

        # Log the before/after name change — both to the persisted ticket
        # logs (Ticket Logs page) and the configured Ticket Activity Log
        # Channel, same as opened/closed/blacklist events.
        ticket = get_active_ticket(interaction.channel.id)
        ticket_id = ticket.get("ticket_id") if ticket else "unknown"
        try:
            append_ticket_log({
                "ticket_id": ticket_id,
                "action": "renamed",
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "executor": {
                    "id": str(interaction.user.id),
                    "name": str(interaction.user),
                },
                "old_name": old_name,
                "new_name": new_name,
            })
        except Exception:
            pass

        await send_ticket_log(
            self.bot,
            "renamed",
            channel=interaction.channel.mention,
            before=f"`{old_name}`",
            after=f"`{new_name}`",
            renamed_by=str(interaction.user),
        )

        # Post the in-channel announcement using Components V2 when the
        # running discord.py version supports it; otherwise fall back to a
        # plain message so the rename is still visible either way.
        announcement = build_rename_announcement(old_name, new_name, interaction.user)
        try:
            if announcement is not None:
                await interaction.channel.send(view=announcement)
            else:
                await interaction.channel.send(
                    f"📝 **Ticket renamed** — `{old_name}` → `{new_name}` "
                    f"(by {interaction.user.mention})"
                )
        except Exception:
            logger.exception(
                f"Failed to post rename announcement in channel={interaction.channel.id}"
            )

    # Slash Command: /ticketnumber — resumes the SHARED numbering used by
    # every category (all categories draw from one combined sequence)
    @app_commands.command(
        name="ticketnumber",
        description="Set the shared ticket numbering (used by all categories) to resume from a given number",
    )
    @app_commands.describe(
        number="The next new ticket, in any category, will continue counting up from this number"
    )
    async def ticket_number_command(
        self, interaction: discord.Interaction, number: int
    ):
        if not await self._powerful_command_check(interaction):
            return

        set_ticket_counter(number)
        await interaction.response.send_message(
            f"✅ Shared ticket numbering set to **{number}** — the next new "
            f"ticket, in any category, will be numbered `{number + 1:04d}`.",
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
        # Shared/combined numbering, matching ticket creation - draw the
        # next number from the same global sequence every category uses.
        from utils.storage import increment_ticket_counter

        next_number = increment_ticket_counter()
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
        title: str = "Request Carry",
        description: str = "",
        category_id: int = None,
        support_role_id: int = None,
        color: str = "#58b9ff",
        image_url: str = None,
        thumbnail_url: str = None,
        footer_text: str = None,
        fields: list = None,
        components: list = None,
    ):
        """Deploy the Advanced Carry panel using Discord Components V2.

        The dashboard stores a list of blocks.  Supported blocks are:
        embed (a V2 Container), divider (Separator), button and dropdown.
        Buttons/dropdowns can live at the top level or inside an embed.
        """
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
            hex_val = (color or "#58b9ff").lstrip("#")
            accent = discord.Color(int(hex_val, 16))
        except Exception:
            accent = discord.Color.blurple()

        from utils.storage import get_ticket_categories

        categories = get_ticket_categories()
        enabled_categories = [c for c in categories if c.get("dropdown_enabled", True)]

        def parse_variable_pairs(cfg):
            """Reads a block's variable filters — a list of "key=value"
            strings — into (key, value) tuples. Falls back to the old
            single variable/value fields for panels saved before this was
            a list, so nothing breaks on existing drafts."""
            raw = cfg.get("variables")
            if raw is None:
                v = str(cfg.get("variable") or "").strip().lower()
                val = str(cfg.get("value") or "").strip()
                return [(v, val)] if v else []
            pairs = []
            for item in raw or []:
                item = str(item or "").strip()
                if not item:
                    continue
                if "=" in item:
                    k, val = item.split("=", 1)
                else:
                    k, val = item, ""
                k = k.strip().lower()
                val = val.strip()
                if k:
                    pairs.append((k, val))
            return pairs

        def matches_any(cat, pairs):
            """True if no filters are set (show everything), or the
            category matches at least one of the given variable=value
            pairs (OR across pairs, so one dropdown can pull in categories
            tagged with several different variables at once)."""
            if not pairs:
                return True
            vars_ = cat.get("variables") or {}
            for variable, value in pairs:
                if str(vars_.get(variable, "")).strip().lower() == str(value or "").strip().lower():
                    return True
            return False

        class AdvancedCarryView(discord.ui.LayoutView):
            def __init__(self, bot):
                super().__init__(timeout=None)
                self.bot = bot
                self._custom_counter = 0
                self.build(components or self._legacy_components())

            def _legacy_components(self):
                # Keep old saved drafts usable.
                blocks = [{
                    "type": "embed",
                    "title": title,
                    "description": description,
                    "color": color,
                    "image_url": image_url,
                    "thumbnail_url": thumbnail_url,
                    "footer": footer_text,
                    "fields": fields or [],
                    "children": []
                }]
                blocks.append({"type": "dropdown", "placeholder": "Choose ticket type...", "variables": []})
                return blocks

            def _text(self, value):
                return discord.ui.TextDisplay(str(value or ""))

            def _add_button(self, parent, cfg):
                label = str(cfg.get("label") or "Open Ticket")[:80]
                pairs = parse_variable_pairs(cfg)
                required_role_ids = {str(r) for r in (cfg.get("required_roles") or [])}

                async def callback(interaction: discord.Interaction):
                    if not get_settings().get("tickets_enabled", True):
                        await interaction.response.send_message(
                            "🚫 Ticket creation is currently disabled by administrators.", ephemeral=True
                        )
                        return
                    if required_role_ids:
                        member_role_ids = {str(r.id) for r in getattr(interaction.user, "roles", [])}
                        if not member_role_ids.intersection(required_role_ids):
                            await interaction.response.send_message(
                                "❌ You don't have the required role to use this button.", ephemeral=True
                            )
                            return
                    matches_ = [c for c in enabled_categories if matches_any(c, pairs)]
                    if not matches_:
                        await interaction.response.send_message(
                            "❌ No enabled ticket category matches this button.", ephemeral=True
                        )
                        return
                    if len(matches_) > 1 and not pairs:
                        await interaction.response.send_message(
                            "❌ This button needs at least one Variable set when more than one category is available.",
                            ephemeral=True
                        )
                        return
                    selection = matches_[0]["label"]
                    fake_select = type("_ButtonSelection", (), {"values": [selection]})()
                    await TicketView(self.bot).ticket_select(interaction, fake_select)

                btn = discord.ui.Button(
                    label=label,
                    style=discord.ButtonStyle.primary,
                    custom_id=f"carry_btn_{self._custom_counter}"
                )
                self._custom_counter += 1
                btn.callback = callback
                parent.add_item(btn)

            def _add_dropdown(self, parent, cfg):
                pairs = parse_variable_pairs(cfg)
                cats = [c for c in enabled_categories if matches_any(c, pairs)][:25]
                options = []
                for c in cats:
                    options.append(discord.SelectOption(
                        label=str(c.get("label", "Ticket"))[:100],
                        description=str(c.get("description", ""))[:100] or None,
                        emoji=c.get("emoji") or None,
                        value=str(c.get("label", "Ticket"))[:100],
                    ))
                if not options:
                    options = [discord.SelectOption(label="No matching ticket types", value="__none__", description="No enabled categories match this filter.")]

                select = discord.ui.Select(
                    placeholder=str(cfg.get("placeholder") or "Choose ticket type...")[:150],
                    min_values=1, max_values=1,
                    options=options,
                    custom_id=f"carry_dd_{self._custom_counter}"
                )
                self._custom_counter += 1
                required_role_ids = {str(r) for r in (cfg.get("required_roles") or [])}

                async def callback(interaction: discord.Interaction):
                    if select.values[0] == "__none__":
                        await interaction.response.send_message("❌ No matching ticket types are enabled.", ephemeral=True)
                        return
                    if required_role_ids:
                        member_role_ids = {str(r.id) for r in getattr(interaction.user, "roles", [])}
                        if not member_role_ids.intersection(required_role_ids):
                            await interaction.response.send_message(
                                "❌ You don't have the required role to use this dropdown.", ephemeral=True
                            )
                            return
                    await TicketView(self.bot).ticket_select(interaction, select)

                select.callback = callback
                parent.add_item(select)

            def _fill(self, parent, children):
                for cfg in children or []:
                    typ = cfg.get("type")
                    if typ == "divider":
                        parent.add_item(discord.ui.Separator())
                    elif typ == "text":
                        content = str(cfg.get("content") or "").strip()
                        if content:
                            parent.add_item(self._text(content))
                    elif typ == "dropdown" and cfg.get("enabled") is False:
                        continue
                    elif typ in ("button", "dropdown"):
                        # Containers may contain ActionRows, not buttons/selects directly.
                        row = discord.ui.ActionRow()
                        if typ == "button":
                            self._add_button(row, cfg)
                        else:
                            self._add_dropdown(row, cfg)
                        parent.add_item(row)

            def _make_container(self, cfg):
                container = discord.ui.Container(accent_color=accent)
                if cfg.get("title"):
                    container.add_item(self._text(cfg["title"]))
                if cfg.get("description"):
                    container.add_item(self._text(cfg["description"]))
                for f in cfg.get("fields") or []:
                    n, v = str(f.get("name") or "").strip(), str(f.get("value") or "").strip()
                    if n and v:
                        container.add_item(self._text(f"**{n}**\n{v}"))
                self._fill(container, cfg.get("children"))
                if cfg.get("footer"):
                    container.add_item(self._text(cfg["footer"]))
                return container

            def build(self, blocks):
                for cfg in blocks or []:
                    typ = cfg.get("type")
                    if typ == "embed":
                        self.add_item(self._make_container(cfg))
                    elif typ == "divider":
                        self.add_item(discord.ui.Separator())
                    elif typ == "button":
                        row = discord.ui.ActionRow()
                        self._add_button(row, cfg)
                        self.add_item(row)
                    elif typ == "dropdown":
                        if cfg.get("enabled") is False:
                            continue
                        row = discord.ui.ActionRow()
                        self._add_dropdown(row, cfg)
                        self.add_item(row)

        view = AdvancedCarryView(self.bot)
        try:
            await channel.send(view=view)
            return True, "Components V2 Carry Panel deployed successfully!"
        except discord.Forbidden:
            return False, "Bot lacks permission to send messages in that channel."
        except Exception as e:
            return False, f"Failed to deploy Components V2 panel: {str(e)}"

    async def do_close(
        self,
        channel: discord.abc.GuildChannel,
        executor: discord.abc.Snowflake,
        reason: str = "No reason provided.",
    ):
        """Fetch the full history, generate a transcript, then close the ticket.

        ``channel.history`` uses Discord's REST API and therefore works with
        Message Content Intent disabled. The close is aborted if history
        cannot be read, so a ticket is never deleted without its transcript.
        """
        ticket_id = str(getattr(channel, "id", ""))
        if owner_close_locked(get_active_ticket(ticket_id), getattr(executor, "id", executor)):
            logger.info("Blocked opener close for in-progress ticket=%s", ticket_id)
            return False
        if ticket_id in self._closing_ticket_ids:
            logger.info("Ignoring duplicate close request for channel=%s", ticket_id)
            return False
        self._closing_ticket_ids.add(ticket_id)
        try:
            try:
                logger.info(
                    f"do_close invoked for channel={getattr(channel,'id',None)} by executor={getattr(executor,'id',executor)} reason={reason}"
                )
            except Exception:
                pass

            messages = []
            # Capture the newest messages within a configurable bound. REST
            # retrieval includes content without the gateway Message Content
            # intent and discord.py handles pagination/rate-limit waits.
            async for m in channel.history(
                limit=TRANSCRIPT_MESSAGE_LIMIT + 1, oldest_first=False
            ):
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
            history_limited = len(messages) > TRANSCRIPT_MESSAGE_LIMIT
            if history_limited:
                messages = messages[:TRANSCRIPT_MESSAGE_LIMIT]
            messages.reverse()
            logger.info(
                "Captured %s messages for ticket transcript channel=%s (limit=%s)",
                len(messages),
                ticket_id,
                TRANSCRIPT_MESSAGE_LIMIT,
            )

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
                messages,
                channel.name,
                ticket_meta,
                generated_at,
                filename,
                omitted_messages=history_limited,
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

            if creator_id and signed_url:
                try:
                    user = await self.bot.fetch_user(int(creator_id))

                    layout_cls = getattr(discord.ui, "LayoutView", None)
                    container_cls = getattr(discord.ui, "Container", None)
                    text_display_cls = getattr(discord.ui, "TextDisplay", None)
                    separator_cls = getattr(discord.ui, "Separator", None)
                    action_row_cls = getattr(discord.ui, "ActionRow", None)

                    if layout_cls and container_cls and text_display_cls and action_row_cls:
                        class TranscriptAccessView(layout_cls):
                            def __init__(self):
                                super().__init__()
                                container = container_cls(
                                    accent_color=discord.Color.blurple()
                                )
                                container.add_item(
                                    text_display_cls("## 🔐 Transcript ready")
                                )
                                if separator_cls:
                                    container.add_item(separator_cls())
                                container.add_item(
                                    text_display_cls(
                                        f"Your ticket **{channel.name}** has been closed.\n"
                                        "Authorize with Discord to verify that this transcript belongs to you. "
                                        "The secure link expires in **1 hour**."
                                    )
                                )
                                row = action_row_cls()
                                row.add_item(
                                    discord.ui.Button(
                                        label="Authorize & view transcript",
                                        url=signed_url,
                                        style=discord.ButtonStyle.link,
                                    )
                                )
                                container.add_item(row)
                                self.add_item(container)

                        await user.send(view=TranscriptAccessView())
                    else:
                        view = discord.ui.View(timeout=None)
                        view.add_item(
                            discord.ui.Button(
                                label="Authorize & view transcript",
                                url=signed_url,
                                style=discord.ButtonStyle.link,
                            )
                        )
                        await user.send(
                            content=(
                                f"Your ticket '{channel.name}' has been closed. "
                                "Authorize with Discord to view your transcript. "
                                "The secure link expires in 1 hour."
                            ),
                            view=view,
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
            return True
        except Exception as global_err:
            logger.exception(f"Error in do_close: {global_err}")
            return False


@app_commands.context_menu(name="📌 Toggle Pin")
async def toggle_pin_message_context_menu(interaction: discord.Interaction, message: discord.Message):
    """Right-click a message -> Apps -> 📌 Toggle Pin.

    Must live at module level, not inside TicketsCog — discord.py raises
    "context menus cannot be defined inside a class" if you try to
    decorate a method with @app_commands.context_menu directly. It's
    manually attached to the bot's tree in TicketsCog.cog_load instead of
    being auto-discovered like a normal @app_commands.command.
    """
    cog = interaction.client.get_cog("TicketsCog")
    if cog is None or not await cog._pin_command_check(interaction):
        return
    try:
        if message.pinned:
            await message.unpin(reason=f"Unpinned by {interaction.user}")
            await interaction.response.send_message(
                f"📌 Unpinned [that message]({message.jump_url}).", ephemeral=True
            )
        else:
            await message.pin(reason=f"Pinned by {interaction.user}")
            await interaction.response.send_message(
                f"📌 Pinned [that message]({message.jump_url}).", ephemeral=True
            )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I don't have permission to pin/unpin messages in this channel.",
            ephemeral=True,
        )
    except discord.HTTPException as e:
        await interaction.response.send_message(
            f"❌ Failed to update the pin — {e.text if hasattr(e, 'text') else e}",
            ephemeral=True,
        )


async def setup(bot):
    cog = TicketsCog(bot)
    await bot.add_cog(cog)
    bot.add_view(TicketView(bot))
    bot.add_view(CloseConfirmView(bot))


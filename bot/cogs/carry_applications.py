import asyncio
import datetime
import logging
import time

import discord
from discord.ext import commands, tasks

from utils.access import (
    has_carry_application_verdict_access,
    is_globally_blocked,
)
from utils.carry_applications import (
    decide_application,
    get_application,
    get_pending_review_applications,
    get_queued_application_notifications,
    update_application_notification,
)
from utils.storage import get_dashboard_base_url, get_settings

logger = logging.getLogger("carry_applications")

ANSWER_LABELS = (
    ("age_range", "Age range"),
    ("timezone", "Country and time zone"),
    ("roblox_username", "Roblox username"),
    ("tds_level", "Tower Defense Simulator level"),
    ("motivation", "Why do you want to join the Carry Team?"),
    ("daily_activity", "Daily activity"),
    ("understands_terms", "Understands the no-rewards policy"),
    ("strategy_rating", "Game strategy experience (1-10)"),
)


def _review_url(application_id):
    base_url = get_dashboard_base_url()
    if not base_url:
        raise RuntimeError("The public site URL is not configured or known yet.")
    return f"{base_url}/carry-application/review/{application_id}"


def _clip(value, limit=500):
    text = str(value or "").strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text or "No response"


def _application_embed(application, review_url, include_answers=True):
    embed = discord.Embed(
        title=f"Carry Team Application: {application.get('username', 'Applicant')}",
        description=(
            f"Application `{application['application_id']}` is awaiting review.\n"
            f"[Open the complete response]({review_url})"
        ),
        color=discord.Color.gold(),
        timestamp=datetime.datetime.fromisoformat(application["submitted_at"]),
    )
    embed.add_field(
        name="Verified Discord account",
        value=f"{application.get('username') or 'Unknown'} (`{application['user_id']}`)",
        inline=False,
    )
    if include_answers:
        for key, label in ANSWER_LABELS:
            embed.add_field(
                name=label,
                value=_clip(application.get("answers", {}).get(key)),
                inline=False,
            )
    return embed


class ApplicationDecisionModal(discord.ui.Modal):
    def __init__(self, view, verdict):
        title = "Accept Carry Application" if verdict == "accepted" else "Deny Carry Application"
        super().__init__(title=title, timeout=300)
        self.review_view = view
        self.verdict = verdict
        self.reason = discord.ui.TextInput(
            label="Reason for this verdict",
            placeholder="Enter a reason that will be sent to the applicant",
            style=discord.TextStyle.paragraph,
            min_length=3,
            max_length=1000,
            required=True,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.review_view.has_access(interaction.user):
            await interaction.response.send_message(
                "You no longer have a role that can decide applications.",
                ephemeral=True,
            )
            return
        await self.review_view.finish_decision(
            interaction,
            self.verdict,
            self.reason.value.strip(),
        )


class ApplicationReviewView(discord.ui.View):
    def __init__(self, bot, application_id, review_url, disabled=False):
        super().__init__(timeout=None)
        self.bot = bot
        self.application_id = str(application_id)
        self.review_url = review_url

        accept = discord.ui.Button(
            label="Accept",
            style=discord.ButtonStyle.success,
            custom_id=f"carry_app:{self.application_id}:accept",
            disabled=disabled,
        )
        accept.callback = self.accept_application
        self.add_item(accept)

        deny = discord.ui.Button(
            label="Decline",
            style=discord.ButtonStyle.danger,
            custom_id=f"carry_app:{self.application_id}:deny",
            disabled=disabled,
        )
        deny.callback = self.deny_application
        self.add_item(deny)

        self.add_item(
            discord.ui.Button(
                label="View full response",
                style=discord.ButtonStyle.link,
                url=review_url,
            )
        )

    def has_access(self, user):
        roles = [str(role.id) for role in getattr(user, "roles", [])]
        return (
            not is_globally_blocked(user.id)
            and has_carry_application_verdict_access(user.id, roles)
        )

    async def _open_modal(self, interaction, verdict):
        if not self.has_access(interaction.user):
            await interaction.response.send_message(
                "You don't have a role authorized to decide applications.",
                ephemeral=True,
            )
            return
        application = get_application(self.application_id)
        if not application or application.get("status") != "pending":
            await interaction.response.send_message(
                "This application has already received a verdict or no longer exists.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            ApplicationDecisionModal(self, verdict)
        )

    async def accept_application(self, interaction):
        await self._open_modal(interaction, "accepted")

    async def deny_application(self, interaction):
        await self._open_modal(interaction, "denied")

    async def finish_decision(self, interaction, verdict, reason):
        try:
            application = decide_application(
                self.application_id,
                verdict,
                reason,
                interaction.user.id,
            )
        except Exception:
            logger.exception(
                "Failed to save verdict for application=%s", self.application_id
            )
            await interaction.response.send_message(
                "The verdict could not be saved. Please try again.",
                ephemeral=True,
            )
            return

        if not application:
            await interaction.response.send_message(
                "This application has already received a verdict.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        dm_error = None
        try:
            recipient = self.bot.get_user(int(application["user_id"]))
            if recipient is None:
                recipient = await self.bot.fetch_user(int(application["user_id"]))
            if verdict == "accepted":
                description = (
                    "Your Carry Service Team application has been accepted. "
                    "A staff member will contact you about next steps. No role "
                    "has been assigned automatically."
                )
            else:
                available_at = datetime.datetime.fromisoformat(
                    application["decided_at"]
                ) + datetime.timedelta(days=14)
                description = (
                    "Your Carry Service Team application was not accepted. "
                    f"You may apply again after **{discord.utils.format_dt(available_at, 'F')}**."
                )
            embed = discord.Embed(
                title="Carry Team Application Update",
                description=description,
                color=(
                    discord.Color.green()
                    if verdict == "accepted"
                    else discord.Color.red()
                ),
            )
            embed.add_field(name="Staff reason", value=_clip(reason, 1000), inline=False)
            await recipient.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Could not DM applicant=%s about verdict=%s",
                application["user_id"],
                verdict,
            )
            dm_error = " The applicant's DMs could not be delivered."

        await self._disable_review_controls(application)
        status = "accepted" if verdict == "accepted" else "denied"
        await interaction.followup.send(
            f"Application {status}. The applicant's response remains available for review."
            f"{dm_error or ''}",
            ephemeral=True,
        )

    async def _disable_review_controls(self, application):
        channel_id = application.get("bot_channel_id")
        message_id = application.get("bot_message_id")
        if not channel_id or not message_id:
            return
        channel = self.bot.get_channel(int(channel_id))
        try:
            if channel is None:
                channel = await self.bot.fetch_channel(int(channel_id))
            message = await channel.fetch_message(int(message_id))
            review_url = _review_url(self.application_id)
            disabled_view = ApplicationReviewView(
                self.bot,
                self.application_id,
                review_url,
                disabled=True,
            )
            embed = message.embeds[0] if message.embeds else discord.Embed(
                title="Carry Team Application"
            )
            embed.color = (
                discord.Color.green()
                if application.get("status") == "accepted"
                else discord.Color.red()
            )
            embed.set_footer(
                text=f"Application {application.get('status', 'reviewed').title()} | "
                f"Decision reason is recorded on the response page"
            )
            await message.edit(embed=embed, view=disabled_view)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            logger.exception(
                "Could not disable review controls for application=%s",
                self.application_id,
            )


class CarryApplicationsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._processing = set()
        self._registered_application_ids = set()
        self.notification_worker.start()
        self.view_restore_worker.start()

    async def cog_load(self):
        await self._restore_pending_review_views()

    async def _restore_pending_review_views(self):
        try:
            applications = get_pending_review_applications()
        except Exception:
            logger.exception("Failed to restore pending Carry application review cards")
            return
        for application in applications:
            application_id = application["application_id"]
            if application_id in self._registered_application_ids:
                continue
            try:
                review_url = _review_url(application_id)
                view = ApplicationReviewView(
                    self.bot,
                    application_id,
                    review_url,
                )
                self.bot.add_view(
                    view,
                    message_id=int(application["bot_message_id"]),
                )
                self._registered_application_ids.add(application_id)
            except Exception:
                logger.exception(
                    "Failed to restore review controls for application=%s",
                    application_id,
                )

    def cog_unload(self):
        self.notification_worker.cancel()
        self.view_restore_worker.cancel()

    @tasks.loop(seconds=30)
    async def notification_worker(self):
        try:
            for application in get_queued_application_notifications():
                await self.process_application_notice(
                    application["application_id"]
                )
        except Exception:
            logger.exception("Carry application notification worker failed")

    @notification_worker.before_loop
    async def before_notification_worker(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=60)
    async def view_restore_worker(self):
        await self._restore_pending_review_views()

    @view_restore_worker.before_loop
    async def before_view_restore_worker(self):
        await self.bot.wait_until_ready()

    async def process_application_notice(self, application_id):
        app_id = str(application_id)
        if app_id in self._processing:
            return
        self._processing.add(app_id)
        try:
            application = get_application(app_id)
            if not application or application.get("status") != "pending":
                return
            if application.get("notification_status") == "sent":
                return
            await self._deliver_application(application)
            latest = get_application(app_id)
            if latest and latest.get("status") != "pending":
                review_url = _review_url(app_id)
                view = ApplicationReviewView(
                    self.bot,
                    app_id,
                    review_url,
                    disabled=True,
                )
                await view._disable_review_controls(latest)
                return
            update_application_notification(
                app_id,
                notification_status="sent",
                notification_error=None,
                notification_next_attempt_at=0,
            )
        except Exception as error:
            logger.error(
                "Unable to notify staff about application=%s (error type: %s)",
                app_id,
                type(error).__name__,
            )
            try:
                application = get_application(app_id)
                attempts = int((application or {}).get("notification_attempts") or 0) + 1
                delay = min(3600, 30 * (2 ** min(attempts, 7)))
                update_application_notification(
                    app_id,
                    notification_status="pending",
                    notification_attempts=attempts,
                    notification_error=type(error).__name__,
                    notification_next_attempt_at=time.time() + delay,
                )
            except Exception:
                logger.exception(
                    "Failed to reschedule application notification id=%s", app_id
                )
        finally:
            self._processing.discard(app_id)

    async def _deliver_application(self, application):
        settings = get_settings().get("carry_application_delivery") or {}
        mode = settings.get("mode", "bot")
        review_url = _review_url(application["application_id"])

        if mode == "webhook":
            webhook_url = str(settings.get("webhook_url") or "")
            webhook = None
            if not application.get("webhook_message_id"):
                if not webhook_url:
                    raise RuntimeError("No application review webhook is configured.")
                webhook = await discord.Webhook.from_url(
                    webhook_url,
                    client=self.bot,
                ).fetch()
                webhook_message = await webhook.send(
                    embed=_application_embed(application, review_url),
                    wait=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                update_application_notification(
                    application["application_id"],
                    webhook_message_id=str(webhook_message.id),
                    webhook_channel_id=str(webhook.channel_id or ""),
                )
                application["webhook_message_id"] = str(webhook_message.id)
                application["webhook_channel_id"] = str(webhook.channel_id or "")
            channel_id = application.get("webhook_channel_id")
            if not channel_id:
                if webhook is None:
                    if not webhook_url:
                        raise RuntimeError(
                            "No application review webhook is configured."
                        )
                    webhook = await discord.Webhook.from_url(
                        webhook_url,
                        client=self.bot,
                    ).fetch()
                channel_id = webhook.channel_id
                if channel_id:
                    update_application_notification(
                        application["application_id"],
                        webhook_channel_id=str(channel_id),
                    )
            if not channel_id:
                raise RuntimeError(
                    "The configured webhook did not provide a Discord channel ID."
                )
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                channel = await self.bot.fetch_channel(int(channel_id))
            guild = self.bot.guilds[0] if self.bot.guilds else None
            channel_guild = getattr(channel, "guild", None)
            if guild is None or channel_guild is None or str(channel_guild.id) != str(guild.id):
                raise RuntimeError(
                    "The webhook must post in the connected Carry Service server."
                )
            if not application.get("bot_message_id"):
                review_embed = discord.Embed(
                    title="Application verdict required",
                    description=(
                        f"**{application.get('username', 'Applicant')}** has submitted "
                        "a Carry Team application.\n"
                        f"[View the full response]({review_url})"
                    ),
                    color=discord.Color.gold(),
                )
                message = await channel.send(
                    embed=review_embed,
                    view=ApplicationReviewView(
                        self.bot,
                        application["application_id"],
                        review_url,
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                update_application_notification(
                    application["application_id"],
                    bot_message_id=str(message.id),
                    bot_channel_id=str(channel.id),
                )
                self._registered_application_ids.add(
                    application["application_id"]
                )
            return

        channel_id = str(settings.get("channel_id") or "")
        if not channel_id.isdigit():
            raise RuntimeError("No application review channel is configured.")
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            channel = await self.bot.fetch_channel(int(channel_id))
        if not application.get("bot_message_id"):
            message = await channel.send(
                embed=_application_embed(application, review_url),
                view=ApplicationReviewView(
                    self.bot,
                    application["application_id"],
                    review_url,
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            update_application_notification(
                application["application_id"],
                bot_message_id=str(message.id),
                bot_channel_id=str(channel.id),
            )
            self._registered_application_ids.add(application["application_id"])


async def setup(bot):
    await bot.add_cog(CarryApplicationsCog(bot))

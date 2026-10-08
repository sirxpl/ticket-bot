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


def _format_submitted_at(application):
    submitted_at = application.get("submitted_at")
    if not submitted_at:
        return "Unknown"
    submitted_at = datetime.datetime.fromisoformat(submitted_at)
    if submitted_at.tzinfo is None:
        submitted_at = submitted_at.replace(tzinfo=datetime.timezone.utc)
    return discord.utils.format_dt(submitted_at, "R")


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


class ApplicationReviewView(discord.ui.LayoutView):
    def __init__(
        self,
        bot,
        application_id,
        review_url,
        application=None,
        disabled=False,
        include_answers=True,
        show_verdict_controls=True,
        status="pending",
        decision_reason=None,
        applicant_name=None,
    ):
        super().__init__(timeout=None)
        self.bot = bot
        self.application_id = str(application_id)
        self.review_url = review_url
        application = application or {}
        applicant_name = applicant_name or application.get("username")

        color = (
            discord.Color.green()
            if status == "accepted"
            else discord.Color.red()
            if status == "denied"
            else discord.Color.gold()
        )
        container = discord.ui.Container(accent_color=color)
        if status == "pending":
            heading = "## Carry Team Application"
            description = (
                f"**Applicant:** {applicant_name or 'Carry Team applicant'}\n"
                f"**Verified Discord ID:** `{application.get('user_id', 'Unknown')}`"
            )
        else:
            heading = f"## Application {status.title()}"
            description = (
                f"**Applicant:** {applicant_name or 'Carry Team applicant'}\n"
                f"**Verified Discord ID:** `{application.get('user_id', 'Unknown')}`"
            )
            if decision_reason:
                description += f"\n**Verdict reason:** {_clip(decision_reason, 1000)}"

        container.add_item(discord.ui.TextDisplay(heading))
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                f"{description}\n"
                f"**Application ID:** `{self.application_id}`\n"
                f"**Submitted:** {_format_submitted_at(application)}\n"
                f"[Open the complete response]({review_url})"
            )
        )
        container.add_item(discord.ui.Separator())
        if include_answers:
            answers = application.get("answers", {})
            for index in range(0, len(ANSWER_LABELS), 2):
                answer_group = ANSWER_LABELS[index : index + 2]
                answer_text = "\n\n".join(
                    f"**{label}**\n{_clip(answers.get(key), 500)}"
                    for key, label in answer_group
                )
                container.add_item(discord.ui.TextDisplay(answer_text))
        if status == "pending" and not disabled and show_verdict_controls:
            row = discord.ui.ActionRow()
            accept = discord.ui.Button(
                label="Accept",
                style=discord.ButtonStyle.success,
                custom_id=f"carry_app:{self.application_id}:accept",
            )
            accept.callback = self.accept_application
            row.add_item(accept)

            decline = discord.ui.Button(
                label="Decline",
                style=discord.ButtonStyle.danger,
                custom_id=f"carry_app:{self.application_id}:deny",
            )
            decline.callback = self.deny_application
            row.add_item(decline)
            container.add_item(row)
        elif status != "pending" or disabled:
            row = discord.ui.ActionRow()
            closed_label = (
                "Verdict recorded" if status != "pending" else "Review disabled"
            )
            row.add_item(
                discord.ui.Button(
                    label=closed_label,
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"carry_app:{self.application_id}:closed",
                    disabled=True,
                )
            )
            container.add_item(row)

        link_row = discord.ui.ActionRow()
        link_row.add_item(
            discord.ui.Button(
                label="View full response",
                style=discord.ButtonStyle.link,
                url=review_url,
            )
        )
        container.add_item(link_row)
        self.add_item(container)

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
            result_view = discord.ui.LayoutView(timeout=None)
            result_container = discord.ui.Container(
                accent_color=(
                    discord.Color.green()
                    if verdict == "accepted"
                    else discord.Color.red()
                )
            )
            result_container.add_item(
                discord.ui.TextDisplay("## Carry Team Application Update")
            )
            result_container.add_item(discord.ui.Separator())
            result_container.add_item(
                discord.ui.TextDisplay(
                    f"{description}\n\n**Staff reason:**\n{_clip(reason, 1000)}"
                )
            )
            result_view.add_item(result_container)
            await recipient.send(
                view=result_view,
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
                application=application,
                disabled=True,
                status=application.get("status", "pending"),
                decision_reason=application.get("decision_reason"),
                applicant_name=application.get("username"),
            )
            await message.edit(view=disabled_view)
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
                    application=application,
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
            if not application:
                return
            if (
                application.get("status") == "pending"
                and application.get("notification_status") != "sent"
            ):
                try:
                    await self._deliver_application(application)
                    latest = get_application(app_id)
                    if latest and latest.get("status") != "pending":
                        view = ApplicationReviewView(
                            self.bot,
                            app_id,
                            _review_url(app_id),
                            application=latest,
                            disabled=True,
                            status=latest.get("status", "pending"),
                            decision_reason=latest.get("decision_reason"),
                            applicant_name=latest.get("username"),
                        )
                        await view._disable_review_controls(latest)
                    update_application_notification(
                        app_id,
                        notification_status="sent",
                        notification_error=None,
                    )
                except Exception as error:
                    self._schedule_notification_retry(
                        app_id,
                        error,
                        status_field="notification_status",
                        attempts_field="notification_attempts",
                        error_field="notification_error",
                    )

            application = get_application(app_id)
            if (
                application
                and application.get("confirmation_dm_status") == "pending"
            ):
                try:
                    await self._deliver_applicant_confirmation(application)
                    update_application_notification(
                        app_id,
                        confirmation_dm_status="sent",
                        confirmation_dm_error=None,
                    )
                except (discord.Forbidden, discord.NotFound) as error:
                    logger.warning(
                        "Could not send submission confirmation DM for application=%s: %s",
                        app_id,
                        type(error).__name__,
                    )
                    update_application_notification(
                        app_id,
                        confirmation_dm_status="failed",
                        confirmation_dm_error=type(error).__name__,
                    )
                except Exception as error:
                    self._schedule_notification_retry(
                        app_id,
                        error,
                        status_field="confirmation_dm_status",
                        attempts_field="confirmation_dm_attempts",
                        error_field="confirmation_dm_error",
                    )
        finally:
            self._processing.discard(app_id)

    def _schedule_notification_retry(
        self,
        application_id,
        error,
        status_field,
        attempts_field,
        error_field,
    ):
        logger.error(
            "Unable to process %s for application=%s (error type: %s)",
            error_field,
            application_id,
            type(error).__name__,
        )
        try:
            application = get_application(application_id)
            attempts = int((application or {}).get(attempts_field) or 0) + 1
            delay = min(3600, 30 * (2 ** min(attempts, 7)))
            update_application_notification(
                application_id,
                **{
                    status_field: "pending",
                    attempts_field: attempts,
                    error_field: type(error).__name__,
                    "notification_next_attempt_at": time.time() + delay,
                },
            )
        except Exception:
            logger.exception(
                "Failed to reschedule %s for application=%s",
                error_field,
                application_id,
            )

    async def _deliver_applicant_confirmation(self, application):
        recipient = self.bot.get_user(int(application["user_id"]))
        if recipient is None:
            recipient = await self.bot.fetch_user(int(application["user_id"]))
        view = discord.ui.LayoutView(timeout=None)
        container = discord.ui.Container(accent_color=discord.Color.blue())
        container.add_item(
            discord.ui.TextDisplay("## Carry Team Application Submitted")
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "Your application was submitted successfully and is awaiting staff "
                "review. **Please wait for the result**—we'll send you another DM "
                "when a decision has been made.\n\n"
                f"**Application ID:** `{application['application_id']}`"
            )
        )
        view.add_item(container)
        await recipient.send(
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

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
                    view=ApplicationReviewView(
                        self.bot,
                        application["application_id"],
                        review_url,
                        application=application,
                        show_verdict_controls=False,
                    ),
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
                message = await channel.send(
                    view=ApplicationReviewView(
                        self.bot,
                        application["application_id"],
                        review_url,
                        application=application,
                        include_answers=False,
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
                view=ApplicationReviewView(
                    self.bot,
                    application["application_id"],
                    review_url,
                    application=application,
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

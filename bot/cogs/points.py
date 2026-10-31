"""Discord commands, Components V2 audit delivery, and Points channel automation."""
import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils import points

logger = logging.getLogger("points.cog")

LOG_PRESENTATION = {
    "manual_add": ("➕ Points Added", discord.Color.green()),
    "manual_subtract": ("➖ Points Subtracted", discord.Color.orange()),
    "manual_set": ("🎯 Points Balance Set", discord.Color.blurple()),
    "leaderboard_removed": ("👤 User Removed from Leaderboard", discord.Color.orange()),
    "excluded": ("🚫 Automatic Points Exclusion", discord.Color.orange()),
    "included": ("✅ Automatic Points Inclusion", discord.Color.green()),
    "automatic_awarded": ("✨ Automatic Points Awarded", discord.Color.green()),
    "automatic_skipped": ("⚠️ Automatic Points Skipped", discord.Color.orange()),
    "duplicate_ticket": ("🛡️ Duplicate Ticket Prevented", discord.Color.orange()),
    "format_passed": ("✅ Format Checker Passed", discord.Color.green()),
    "format_rejected": ("❌ Format Checker Rejected", discord.Color.red()),
    "thread_created": ("🧵 Automation Thread Created", discord.Color.blurple()),
    "thread_archived": ("📦 Automation Thread Archived", discord.Color.blurple()),
    "thread_failed": ("⚠️ Automation Thread Failed", discord.Color.orange()),
    "settings_updated": ("⚙️ Points Settings Updated", discord.Color.blurple()),
}


def _v2_notice(title, body, *, okay=True):
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(
        accent_color=discord.Color.green() if okay else discord.Color.orange()
    )
    container.add_item(discord.ui.TextDisplay("## " + title))
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(body))
    view.add_item(container)
    return view


def _member_name(member):
    return str(getattr(member, "display_name", None) or member)


class _SafeThreadName(dict):
    def __missing__(self, key):
        return "{" + key + "}"


class PointsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _allowed(self, interaction, *, manager_only=False):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return False
        try:
            if manager_only:
                allowed = await asyncio.to_thread(points.has_points_manager_access, interaction.user)
            else:
                allowed = await asyncio.to_thread(
                    points.has_points_command_access, interaction.user, interaction.guild.id
                )
        except Exception:
            logger.exception("Could not check Points command permissions")
            allowed = False
        if not allowed:
            await interaction.response.send_message(
                view=_v2_notice(
                    "Points Access Required",
                    "You do not have permission to use this Points command. Ask a Carry Manager to review Points access settings.",
                    okay=False,
                ),
                ephemeral=True,
            )
            return False
        return True

    async def _manual_adjust(self, interaction, user, mode, amount, reason):
        if user.bot:
            await interaction.response.send_message(
                view=_v2_notice("Invalid User", "Points cannot be assigned to bot accounts.", okay=False),
                ephemeral=True,
            )
            return
        if not await self._allowed(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await asyncio.to_thread(
                points.adjust_points,
                interaction.guild.id, user.id, _member_name(user), mode, amount, reason,
                interaction.user.id, _member_name(interaction.user),
            )
        except ValueError as error:
            await interaction.followup.send(
                view=_v2_notice("Invalid Points Change", str(error), okay=False), ephemeral=True
            )
            return
        except Exception:
            logger.exception("Manual Points command failed")
            await interaction.followup.send(
                view=_v2_notice("Points Not Updated", "The point change could not be saved. No success was recorded.", okay=False),
                ephemeral=True,
            )
            return
        action = {"add": "manual_add", "subtract": "manual_subtract", "set": "manual_set"}[mode]
        await self._deliver_log(
            interaction.guild, action, user_id=user.id, username=_member_name(user),
            amount=amount if mode != "subtract" else -amount,
            points_after=int(result.get("points", 0)), reason=reason,
            actor_id=interaction.user.id, actor_name=_member_name(interaction.user),
        )
        await interaction.followup.send(
            view=_v2_notice(
                "Points Updated",
                f"{user.mention} now has {int(result.get('points', 0)):,} points.\n"
                f"Action: {mode.title()} {amount:,}\nReason: {discord.utils.escape_mentions(reason)}",
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="points-add", description="Add points to a Carry Team member")
    @app_commands.guild_only()
    @app_commands.describe(user="Member receiving points", amount="Number of points to add", reason="Why points are being added")
    async def points_add(self, interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1, 1_000_000_000], reason: str):
        await self._manual_adjust(interaction, user, "add", amount, reason)

    @app_commands.command(name="points-subtract", description="Subtract points from a Carry Team member")
    @app_commands.guild_only()
    @app_commands.describe(user="Member losing points", amount="Number of points to subtract", reason="Why points are being subtracted")
    async def points_subtract(self, interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1, 1_000_000_000], reason: str):
        await self._manual_adjust(interaction, user, "subtract", amount, reason)

    @app_commands.command(name="remove-user", description="Remove a member from the Points leaderboard")
    @app_commands.guild_only()
    @app_commands.describe(user="Member to remove from the leaderboard", reason="Optional reason for removal")
    async def remove_user(self, interaction: discord.Interaction, user: discord.Member, reason: str = None):
        if user.bot:
            await interaction.response.send_message("Bot accounts are not on the Points leaderboard.", ephemeral=True)
            return
        if not await self._allowed(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        clean_reason = reason.strip() if reason and reason.strip() else "Removed from the Points leaderboard."
        try:
            await asyncio.to_thread(
                points.remove_points_user, interaction.guild.id, user.id, _member_name(user),
                clean_reason, interaction.user.id, _member_name(interaction.user),
            )
        except Exception:
            logger.exception("Points leaderboard removal failed")
            await interaction.followup.send(
                view=_v2_notice("Removal Failed", "The user could not be removed from the leaderboard.", okay=False),
                ephemeral=True,
            )
            return
        await self._deliver_log(
            interaction.guild, "leaderboard_removed", user_id=user.id, username=_member_name(user),
            reason=clean_reason, actor_id=interaction.user.id, actor_name=_member_name(interaction.user),
        )
        await interaction.followup.send(
            view=_v2_notice("User Removed", f"{user.mention} has been removed from the Points leaderboard."),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _set_exclusion(self, interaction, user, excluded, reason):
        if user.bot:
            await interaction.response.send_message("Bot accounts cannot be excluded from Points.", ephemeral=True)
            return
        if not await self._allowed(interaction, manager_only=True):
            return
        await interaction.response.defer(ephemeral=True)
        clean_reason = reason.strip() if reason and reason.strip() else (
            "Excluded from automatic points." if excluded else "Included for automatic points again."
        )
        try:
            await asyncio.to_thread(
                points.set_user_excluded, interaction.guild.id, user.id, _member_name(user),
                excluded, clean_reason, interaction.user.id, _member_name(interaction.user),
            )
        except Exception:
            logger.exception("Points exclusion update failed")
            await interaction.followup.send(
                view=_v2_notice("Points Exclusion Not Updated", "The exclusion setting could not be saved.", okay=False),
                ephemeral=True,
            )
            return
        action = "excluded" if excluded else "included"
        await self._deliver_log(
            interaction.guild, action, user_id=user.id, username=_member_name(user),
            reason=clean_reason, actor_id=interaction.user.id, actor_name=_member_name(interaction.user),
        )
        await interaction.followup.send(
            view=_v2_notice(
                "Automatic Points Exclusion Updated",
                f"{user.mention} {'will no longer receive' if excluded else 'can receive'} automatic points.\n"
                "Their existing points balance was preserved.",
            ),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="exclude", description="Exclude a member from earning automatic points")
    @app_commands.guild_only()
    @app_commands.describe(user="Member to exclude", reason="Optional reason for exclusion")
    async def exclude_user(self, interaction: discord.Interaction, user: discord.Member, reason: str = None):
        await self._set_exclusion(interaction, user, True, reason)

    @app_commands.command(name="include", description="Allow a previously excluded member to earn automatic points again")
    @app_commands.guild_only()
    @app_commands.describe(user="Member to include again", reason="Optional reason for inclusion")
    async def include_user(self, interaction: discord.Interaction, user: discord.Member, reason: str = None):
        await self._set_exclusion(interaction, user, False, reason)

    async def _deliver_log(self, guild, action, **fields):
        try:
            config = await asyncio.to_thread(points.get_points_config, guild.id)
            logging_config = config.get("logging") or {}
            if not logging_config.get("enabled") or action not in logging_config.get("events", []):
                return
            channel_id = logging_config.get("channel_id")
            if not channel_id:
                return
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                channel = await self.bot.fetch_channel(int(channel_id))
            title, accent = LOG_PRESENTATION.get(action, ("Points Activity", discord.Color.blurple()))
            lines = []
            if fields.get("user_id"):
                lines.append(f"User: <@{fields['user_id']}> ({fields['user_id']})")
            elif fields.get("username"):
                lines.append(f"Member: {discord.utils.escape_markdown(str(fields['username']))}")
            if fields.get("amount") is not None:
                lines.append(f"Points: {int(fields.get('amount') or 0):+,}")
            if fields.get("points_after") is not None:
                lines.append(f"Balance after: {int(fields['points_after']):,}")
            if fields.get("gamemode"):
                lines.append(f"Gamemode: {discord.utils.escape_markdown(str(fields['gamemode']))}")
            if fields.get("ticket_number"):
                lines.append(f"Ticket: {fields['ticket_number']}")
            if fields.get("thread_id"):
                lines.append(f"Thread: <#{fields['thread_id']}>")
            if fields.get("actor_id"):
                lines.append(f"Performed by: <@{fields['actor_id']}> ({fields['actor_id']})")
            elif fields.get("actor_name"):
                lines.append(f"Source: {discord.utils.escape_markdown(str(fields['actor_name']))}")
            if fields.get("reason"):
                lines.append("Reason: " + discord.utils.escape_mentions(str(fields["reason"]))[:500])
            if fields.get("details"):
                lines.append("Details: " + discord.utils.escape_mentions(str(fields["details"]))[:700])
            lines.append(f"Time: <t:{int(discord.utils.utcnow().timestamp())}:F>")
            view = discord.ui.LayoutView(timeout=None)
            container = discord.ui.Container(accent_color=accent)
            container.add_item(discord.ui.TextDisplay("## " + title))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay("\n".join(lines) or "A Points system event occurred."))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay("-# Carry Ticket Bot • Points Audit Log"))
            view.add_item(container)
            await channel.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.exception("Could not deliver Points Components V2 log for action=%s", action)

    async def _record_and_log(self, guild, action, **fields):
        try:
            await asyncio.to_thread(
                points.record_event, guild.id, action,
                user_id=fields.get("user_id"), username=fields.get("username"),
                amount=fields.get("amount", 0), points_after=fields.get("points_after"),
                reason=fields.get("reason"), actor_id=fields.get("actor_id"),
                actor_name=fields.get("actor_name", "Automation"),
                ticket_number=fields.get("ticket_number"), thread_id=fields.get("thread_id"),
                details=fields.get("details"),
            )
        except Exception:
            logger.exception("Could not persist Points automation audit event=%s", action)
        await self._deliver_log(guild, action, **fields)

    def _thread_name(self, template, message, ticket_number=None, gamemode=None):
        author = discord.utils.escape_markdown(_member_name(message.author))[:45]
        values = _SafeThreadName(
            author=author,
            ticket_number=str(ticket_number or f"message-{message.id}"),
            gamemode=discord.utils.escape_markdown(str(gamemode or ""))[:40],
            channel=discord.utils.escape_markdown(str(message.channel.name))[:40],
        )
        try:
            name = str(template or "{author} - {ticket_number}").format_map(values)
        except Exception:
            name = f"{author} - {ticket_number or message.id}"
        return (name.strip() or f"Carry request {message.id}")[:100]

    async def _create_thread(self, message, template, ticket_number=None, gamemode=None):
        name = self._thread_name(template, message, ticket_number, gamemode)
        try:
            return await message.create_thread(name=name, auto_archive_duration=10080)
        except Exception as error:
            logger.exception("Points thread automation failed in channel=%s", message.channel.id)
            await self._record_and_log(
                message.guild, "thread_failed", user_id=message.author.id,
                username=_member_name(message.author), ticket_number=ticket_number,
                details=f"Could not create '{name}': {type(error).__name__}: {error}",
            )
            return None

    async def _handle_format_checker(self, message, config):
        checker = config.get("format_checker") or {}
        exempt_roles = {str(role_id) for role_id in checker.get("exempt_role_ids", [])}
        member_roles = {str(role.id) for role in getattr(message.author, "roles", [])}
        if exempt_roles.intersection(member_roles):
            return

        parsed, error = points.validate_format(message.content)
        if error:
            deleted = False
            try:
                await message.delete()
                deleted = True
            except discord.HTTPException:
                logger.exception("Could not delete invalid Format Checker message=%s", message.id)
            await self._record_and_log(
                message.guild, "format_rejected", user_id=message.author.id,
                username=_member_name(message.author), reason=error,
                details=f"Invalid Format Checker post in #{message.channel.name}; message {message.id}; deleted={deleted}",
            )
            try:
                outcome = "was removed" if deleted else "could not be removed automatically"
                await message.channel.send(
                    view=_v2_notice(
                        "Incorrect Carry Format",
                        f"{message.author.mention}, your submission did not match the required format and {outcome}.\n"
                        f"Correction: {error}\n\nInclude sections 1, 2, 3, and 4. Notes are optional.",
                        okay=False,
                    ),
                    delete_after=20,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.exception("Could not send Format Checker correction message")
            return

        thread = await self._create_thread(
            message, (config.get("thread_automation") or {}).get("thread_name"),
            parsed["ticket_number"], parsed["gamemode"],
        )
        if thread:
            await self._record_and_log(
                message.guild, "thread_created", user_id=message.author.id,
                username=_member_name(message.author), ticket_number=parsed["ticket_number"],
                thread_id=thread.id, gamemode=parsed["gamemode"],
                details="Thread created after the Format Checker passed.",
            )
        await self._record_and_log(
            message.guild, "format_passed", user_id=message.author.id,
            username=_member_name(message.author), ticket_number=parsed["ticket_number"],
            thread_id=thread.id if thread else None, gamemode=parsed["gamemode"],
            details=f"Validated sections 1-4; notes={'present' if parsed['notes'] else 'empty'}.",
        )

        if not parsed["recipients"]:
            await self._record_and_log(
                message.guild, "automatic_skipped",
                user_id=message.author.id, username=_member_name(message.author),
                ticket_number=parsed["ticket_number"], thread_id=thread.id if thread else None,
                gamemode=parsed["gamemode"],
                reason="Section 1 contains no eligible recipients after excluding the carried user in section 2.",
                details="The format passed validation, but nobody can receive points from this submission.",
            )
            return

        rule = points.find_gamemode_rule(config, parsed["gamemode"])
        if rule is None:
            await self._record_and_log(
                message.guild, "automatic_skipped", user_id=message.author.id,
                username=_member_name(message.author), ticket_number=parsed["ticket_number"],
                thread_id=thread.id if thread else None, gamemode=parsed["gamemode"],
                reason=f"Gamemode '{parsed['gamemode']}' is not configured in the Points dashboard.",
                details="Format passed, but no automatic points were awarded.",
            )
            return

        usernames = {}
        for user_id in parsed["recipients"]:
            member = message.guild.get_member(int(user_id))
            usernames[user_id] = _member_name(member) if member else user_id
        try:
            result = await asyncio.to_thread(
                points.award_auto_points,
                message.guild.id, parsed["ticket_number"], parsed["gamemode"],
                parsed["recipients"], rule["points"],
                rule.get("reason") or f"Automatic {rule['text']} points",
                usernames, thread.id if thread else None,
            )
        except Exception as error:
            await self._record_and_log(
                message.guild, "automatic_skipped", ticket_number=parsed["ticket_number"],
                thread_id=thread.id if thread else None, gamemode=parsed["gamemode"],
                reason="The award could not be safely saved.",
                details=f"{type(error).__name__}: {error}",
            )
            return
        if result.get("duplicate"):
            await self._record_and_log(
                message.guild, "duplicate_ticket", ticket_number=parsed["ticket_number"],
                thread_id=thread.id if thread else None, gamemode=parsed["gamemode"],
                details="This ticket number has already been processed; no second award was made.",
            )
            return
        if result.get("awarded"):
            recipients = ", ".join(f"<@{user_id}>" for user_id in result["awarded"])
            await self._deliver_log(
                message.guild, "automatic_awarded", username=recipients,
                amount=rule["points"], reason=rule.get("reason") or f"Automatic {rule['text']} points",
                ticket_number=parsed["ticket_number"], thread_id=thread.id if thread else None,
                gamemode=parsed["gamemode"], actor_name="Format Checker",
                details=f"Awarded to {len(result['awarded'])} Carry Team member(s).",
            )
        if result.get("excluded"):
            await self._record_and_log(
                message.guild, "automatic_skipped", ticket_number=parsed["ticket_number"],
                thread_id=thread.id if thread else None, gamemode=parsed["gamemode"],
                reason="One or more recipients are excluded from automatic points.",
                details="Excluded user IDs: " + ", ".join(result["excluded"]),
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or isinstance(message.channel, discord.Thread):
            return
        try:
            config = await asyncio.to_thread(points.get_points_config, message.guild.id)
        except Exception:
            logger.exception("Could not load Points settings for message automation")
            return

        checker = config.get("format_checker") or {}
        checker_channels = {str(value) for value in checker.get("channel_ids", [])}
        if checker.get("enabled") and str(message.channel.id) in checker_channels:
            if not self.bot.intents.message_content:
                logger.warning("Format Checker is enabled but Message Content Intent is disabled.")
                return
            await self._handle_format_checker(message, config)
            return

        automation = config.get("thread_automation") or {}
        channel_ids = {str(value) for value in automation.get("channel_ids", [])}
        if not automation.get("enabled") or str(message.channel.id) not in channel_ids:
            return
        thread = await self._create_thread(message, automation.get("thread_name"))
        if thread:
            await self._record_and_log(
                message.guild, "thread_created", user_id=message.author.id,
                username=_member_name(message.author), thread_id=thread.id,
                details=f"Created from message {message.id} in #{message.channel.name}; auto-archives after one week of inactivity.",
            )

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        if before.archived or not after.archived or not after.guild:
            return
        try:
            config = await asyncio.to_thread(points.get_points_config, after.guild.id)
        except Exception:
            logger.exception("Could not load Points config for thread archive event")
            return
        thread_cfg = config.get("thread_automation") or {}
        checker_cfg = config.get("format_checker") or {}
        tracked_parent_ids = {
            str(value) for value in (
                list(thread_cfg.get("channel_ids", [])) + list(checker_cfg.get("channel_ids", []))
            )
        }
        if str(after.parent_id) not in tracked_parent_ids:
            return
        await self._record_and_log(
            after.guild, "thread_archived", thread_id=after.id,
            details=f"Automation thread '{after.name}' was archived.",
        )

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.bot.intents.message_content:
            for guild in self.bot.guilds:
                try:
                    config = await asyncio.to_thread(points.get_points_config, guild.id)
                except Exception:
                    continue
                if (config.get("format_checker") or {}).get("enabled"):
                    logger.warning("Format Checker is enabled for guild=%s but Message Content Intent is disabled.", guild.id)


async def setup(bot):
    await bot.add_cog(PointsCog(bot))

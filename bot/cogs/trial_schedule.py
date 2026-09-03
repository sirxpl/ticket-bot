import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.access import is_admin
from utils.storage import get_trial_schedule_settings, save_trial_schedule_settings

logger = logging.getLogger("trial_schedule")

# Trial schedule uses Eastern Time.
# America/New_York automatically handles EST/EDT daylight-saving changes.
EASTERN = ZoneInfo("America/New_York")

DOC_URL = (
    "https://docs.google.com/document/d/"
    "1NzhAEK4WJ9cA2gDCcACtW-HXQHhJplrE-_ZKkPWXIJk/"
    "edit?tab=t.pdj0ytau4jf4"
)

# Schedule anchor:
# Limitation starts September 2, 2026 at 11:00 PM Eastern Time.
ANCHOR = dt.datetime(
    2026,
    9,
    2,
    23,
    0,
    tzinfo=EASTERN,
)

# Every trial lasts 3 hours.
SLOT = dt.timedelta(hours=3)

# Trial rotation.
TRIALS = [
    ("📦", "Limitation"),
    ("🌪️", "Flying"),
    ("🔒", "Jailed Towers"),
    ("💥", "Exploding"),
    ("💰", "Inflation"),
    ("🧲", "Committed"),
    ("🚫", "Hidden"),
    ("💸", "Broke"),
    ("🤝", "Healthy"),
    ("⚡", "Speedy"),
    ("➕", "Glass"),
    ("☣️", "Quarantine"),
    ("🌳", "Fog"),
]


def _slot_number(now: dt.datetime) -> int:
    """
    Return which 3-hour slot the current time belongs to.

    Slot 0 starts at the Limitation anchor.
    """
    now = now.astimezone(EASTERN)

    return int(
        (now - ANCHOR).total_seconds()
        // SLOT.total_seconds()
    )


def _slot_start(number: int) -> dt.datetime:
    """Return the Eastern Time start datetime for a slot."""
    return ANCHOR + (SLOT * number)


def _discord_timestamp(when: dt.datetime, style: str = "F") -> str:
    """
    Create a Discord timestamp.

    F = full date/time
    f = short date/time
    R = relative time
    """
    timestamp = int(when.timestamp())
    return f"<t:{timestamp}:{style}>"


def build_trial_schedule_embed(
    now: dt.datetime | None = None,
) -> tuple[discord.Embed, int]:
    """
    Build the Trial Schedule embed.

    Every trial receives Discord timestamps so Discord automatically
    displays the correct time for each user's timezone.
    """

    now = (
        now or dt.datetime.now(EASTERN)
    ).astimezone(EASTERN)

    slot_number = _slot_number(now)

    # If the current time is before the initial Limitation anchor,
    # start the displayed schedule at Limitation rather than showing
    # the previous cycle's Fog trial.
    first_slot = max(slot_number, 0)

    lines = [
        f"Strategies for modifiers [here]({DOC_URL}).",
        "",
    ]

    # Show the next 13 trials, including the current/upcoming one.
    for offset in range(13):
        slot_no = first_slot + offset

        trial_index = slot_no % len(TRIALS)
        emoji, name = TRIALS[trial_index]

        start_time = _slot_start(slot_no)
        end_time = start_time + SLOT

        # Determine whether this is currently active,
        # upcoming, or the first scheduled trial.
        if start_time <= now < end_time:
            status = f"🟢 **ACTIVE** • Ends {_discord_timestamp(end_time, 'R')}"
        elif start_time > now:
            status = (
                f"🕐 Starts {_discord_timestamp(start_time, 'F')} "
                f"({_discord_timestamp(start_time, 'R')})"
            )
        else:
            status = f"🕐 {_discord_timestamp(start_time, 'F')}"

        lines.append(
            f"{emoji} **{name}**\n"
            f"   {status}\n"
            f"   🕒 Ends {_discord_timestamp(end_time, 'F')}"
        )

        if offset != 12:
            lines.append("")

    embed = discord.Embed(
        title="Trial Schedule",
        description="\n".join(lines),
        color=discord.Color.from_rgb(47, 49, 54),
    )

    embed.set_footer(
        text="Eastern Time (EST/EDT) • Each trial lasts 3 hours"
    )

    return embed, slot_number


class TrialSchedule(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_slot = None
        self._update_lock = asyncio.Lock()

        self.auto_updater.start()

    def cog_unload(self):
        self.auto_updater.cancel()

    async def publish_or_update(
        self,
        channel_id: int | str | None = None,
        force: bool = False,
    ):
        """
        Create or update the configured Trial Schedule message.

        This intentionally avoids repeatedly editing the Discord message.
        Discord handles the <t:...:R> countdown on the client side.
        """

        async with self._update_lock:
            cfg = get_trial_schedule_settings()

            target_id = str(
                channel_id
                or cfg.get("channel_id")
                or ""
            ).strip()

            if not target_id:
                return None

            try:
                channel = (
                    self.bot.get_channel(int(target_id))
                    or await self.bot.fetch_channel(int(target_id))
                )

            except (
                discord.HTTPException,
                discord.NotFound,
                discord.Forbidden,
                ValueError,
                TypeError,
            ):
                logger.warning(
                    "Could not resolve trial schedule channel %s",
                    target_id,
                )
                return None

            embed, slot_no = build_trial_schedule_embed()

            message = None

            stored_message_id = cfg.get("message_id")

            same_channel = (
                str(cfg.get("channel_id") or "")
                == target_id
            )

            # Try to reuse the existing schedule message.
            if stored_message_id and same_channel:
                try:
                    message = await channel.fetch_message(
                        int(stored_message_id)
                    )

                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException,
                    ValueError,
                    TypeError,
                ):
                    message = None

            try:
                if message:
                    # Only edit when the actual 3-hour slot changes,
                    # or when a forced update is requested.
                    #
                    # The relative Discord timestamps update automatically
                    # for users, so there is no reason to edit every minute.
                    if force or self._last_slot != slot_no:
                        await message.edit(embed=embed)

                else:
                    # No existing message was found, so create one.
                    message = await channel.send(embed=embed)

                    save_trial_schedule_settings(
                        {
                            "channel_id": target_id,
                            "message_id": str(message.id),
                        }
                    )

                self._last_slot = slot_no

                return message

            except discord.HTTPException as exc:
                # discord.py handles Discord's rate-limit responses.
                # Do not create a manual hot-loop retry.
                logger.warning(
                    "Trial schedule Discord API request failed: %s",
                    exc,
                )

                return None

    @tasks.loop(seconds=60)
    async def auto_updater(self):
        cfg = get_trial_schedule_settings()

        if not cfg.get("enabled"):
            return

        if not cfg.get("channel_id"):
            return

        current_slot = _slot_number(
            dt.datetime.now(EASTERN)
        )

        # The message only needs to be edited when the 3-hour
        # trial changes.
        #
        # Discord itself updates <t:...:R> timestamps for users,
        # meaning we don't need to make an API request every minute.
        if self._last_slot != current_slot:
            await self.publish_or_update()

    @auto_updater.before_loop
    async def before_auto_updater(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="trial_schedule",
        description="Post or refresh the current Trial Schedule",
    )
    async def trial_schedule(
        self,
        interaction: discord.Interaction,
    ):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message(
                "❌ You do not have permission to use "
                "`/trial_schedule`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        embed, _ = build_trial_schedule_embed()

        try:
            await interaction.channel.send(
                embed=embed
            )

            await interaction.followup.send(
                "✅ Trial Schedule posted.",
                ephemeral=True,
            )

        except discord.HTTPException:
            await interaction.followup.send(
                "❌ I couldn't post the Trial Schedule "
                "in this channel.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(TrialSchedule(bot))

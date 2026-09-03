import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.storage import get_trial_schedule_settings, save_trial_schedule_settings

logger = logging.getLogger("trial_schedule")

EASTERN = ZoneInfo("America/New_York")

DOC_URL = (
    "https://docs.google.com/document/d/"
    "1NzhAEK4WJ9cA2gDCcACtW-HXQHhJplrE-_ZKkPWXIJk/"
    "edit?tab=t.pdj0ytau4jf4"
)

ANCHOR = dt.datetime(2026, 9, 2, 23, 0, tzinfo=EASTERN)
SLOT = dt.timedelta(hours=3)

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
    now = now.astimezone(EASTERN)
    return int((now - ANCHOR).total_seconds() // SLOT.total_seconds())


def _slot_start(number: int) -> dt.datetime:
    return ANCHOR + SLOT * number


def _discord_timestamp(when: dt.datetime, style: str) -> str:
    return f"<t:{int(when.timestamp())}:{style}>"


def build_trial_schedule_embed(
    now: dt.datetime | None = None,
) -> tuple[discord.Embed, int]:
    now = (now or dt.datetime.now(EASTERN)).astimezone(EASTERN)

    slot_number = _slot_number(now)
    first_slot = max(slot_number, 0)

    lines = [
        f"Strategies for modifiers [here]({DOC_URL}).",
    ]

    current_start = _slot_start(first_slot)
    current_end = current_start + SLOT
    current_emoji, current_name = TRIALS[first_slot % len(TRIALS)]

    if current_start <= now < current_end:
        lines.append(
            f"{current_emoji} 🟢 **{current_name}** "
            f"ends {_discord_timestamp(current_end, 'R')}"
        )
        start_offset = 1
    else:
        start_offset = 0

    # Show the rest of the current rotation without duplicates.
    for offset in range(start_offset, len(TRIALS)):
        slot_no = first_slot + offset
        emoji, name = TRIALS[slot_no % len(TRIALS)]
        start_time = _slot_start(slot_no)

        lines.append(
            f"{emoji} **{name}** "
            f"{_discord_timestamp(start_time, 't')}"
        )

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
        async with self._update_lock:
            cfg = get_trial_schedule_settings()

            target_id = str(
                channel_id or cfg.get("channel_id") or ""
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
                str(cfg.get("channel_id") or "") == target_id
            )

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
                    if force or self._last_slot != slot_no:
                        await message.edit(embed=embed)
                else:
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
                logger.warning(
                    "Trial schedule Discord API request failed: %s",
                    exc,
                )
                return None

    @tasks.loop(seconds=60)
    async def auto_updater(self):
        cfg = get_trial_schedule_settings()

        if not cfg.get("enabled") or not cfg.get("channel_id"):
            return

        current_slot = _slot_number(
            dt.datetime.now(EASTERN)
        )

        if self._last_slot != current_slot:
            await self.publish_or_update()

    @auto_updater.before_loop
    async def before_auto_updater(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="trial_schedule",
        description="View the current Trial Schedule",
    )
    async def trial_schedule(
        self,
        interaction: discord.Interaction,
    ):
        embed, _ = build_trial_schedule_embed()

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TrialSchedule(bot))

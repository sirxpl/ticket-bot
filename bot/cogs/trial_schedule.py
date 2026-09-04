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

# Limitation starts September 2, 2026 at 11:00 PM Eastern Time.
ANCHOR = dt.datetime(2026, 9, 2, 23, 0, tzinfo=EASTERN)

# Every trial lasts 3 hours.
SLOT = dt.timedelta(hours=3)

TRIALS = [
    ("<:LimitationModifier:1545248731480195122>", "Limitation"),
    ("<:FlyingEnemiesModifier:1545248724614381688>", "Flying"),
    ("<:JailedModifier:1545248730293207040>", "Jailed Towers"),
    ("<:ExplodingEnemiesModifier:1545248723389648937>", "Exploding"),
    ("<:InflationModifier:1545248729202823260>", "Inflation"),
    ("<:CommittedModifier:1545248722324168744>", "Committed"),
    ("<:HiddenEnemiesModifier:1545248728405909574>", "Hidden"),
    ("<:BrokeModifier:1545248721099292674>", "Broke"),
    ("<:HealthyEnemiesModifier:1545248727663517708>", "Healthy"),
    ("<:SpeedyEnemiesModifier:1545248734038859796>", "Speedy"),
    ("<:GlassModifier:1545248726849818734>", "Glass"),
    ("<:QuarantineModifier:1545248732474515507>", "Quarantine"),
    ("<:FogModifier:1545248725788790894>", "Fog"),
]


def _slot_number(now: dt.datetime) -> int:
    now = now.astimezone(EASTERN)

    return int(
        (now - ANCHOR).total_seconds()
        // SLOT.total_seconds()
    )


def _slot_start(number: int) -> dt.datetime:
    return ANCHOR + SLOT * number


def _discord_timestamp(
    when: dt.datetime,
    style: str,
) -> str:
    return f"<t:{int(when.timestamp())}:{style}>"


def build_trial_schedule_embed(
    now: dt.datetime | None = None,
) -> tuple[discord.Embed, int]:
    now = (
        now or dt.datetime.now(EASTERN)
    ).astimezone(EASTERN)

    slot_number = _slot_number(now)

    # Before the schedule begins, start at Limitation.
    first_slot = max(slot_number, 0)

    lines = [
        f"Strategies for modifiers [here]({DOC_URL}).",
    ]

    current_start = _slot_start(first_slot)
    current_end = current_start + SLOT

    # ---------------------------------------------------------
    # CURRENTLY ACTIVE TRIAL
    # ---------------------------------------------------------
    if current_start <= now < current_end:
        emoji, name = TRIALS[first_slot % len(TRIALS)]

        lines.append(
            f"{emoji} 🟢 **{name}** "
            f"ends {_discord_timestamp(current_end, 'R')}"
        )

        # The first upcoming trial gets "starts".
        upcoming_start = first_slot + 1

        emoji, name = TRIALS[
            upcoming_start % len(TRIALS)
        ]

        start_time = _slot_start(upcoming_start)

        lines.append(
            f"{emoji} **{name}** "
            f"starts {_discord_timestamp(start_time, 't')}"
        )

        upcoming_start += 1

    # ---------------------------------------------------------
    # BEFORE THE FIRST TRIAL
    # ---------------------------------------------------------
    else:
        emoji, name = TRIALS[
            first_slot % len(TRIALS)
        ]

        start_time = _slot_start(first_slot)

        lines.append(
            f"{emoji} **{name}** "
            f"starts {_discord_timestamp(start_time, 't')}"
        )

        upcoming_start = first_slot + 1

    # ---------------------------------------------------------
    # REMAINING TRIALS
    # ---------------------------------------------------------
    #
    # Display exactly one full rotation.
    # This prevents the currently active/upcoming trial
    # from appearing twice.
    #
    for slot_no in range(
        upcoming_start,
        first_slot + len(TRIALS),
    ):
        emoji, name = TRIALS[
            slot_no % len(TRIALS)
        ]

        start_time = _slot_start(slot_no)

        lines.append(
            f"{emoji} **{name}** "
            f"{_discord_timestamp(start_time, 't')}"
        )

    embed = discord.Embed(
        title="Trial Schedule",
        description="\n".join(lines),
        color=discord.Color.from_rgb(
            47,
            49,
            54,
        ),
    )

    embed.set_footer(
        text="Eastern Time (EST/EDT) • Each trial lasts 3 hours"
    )

    return embed, slot_number


class TrialSchedule(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
    ):
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
                channel_id
                or cfg.get("channel_id")
                or ""
            ).strip()

            if not target_id:
                return None

            try:
                channel = (
                    self.bot.get_channel(
                        int(target_id)
                    )
                    or await self.bot.fetch_channel(
                        int(target_id)
                    )
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

            embed, slot_no = (
                build_trial_schedule_embed()
            )

            message = None
            stored_message_id = cfg.get(
                "message_id"
            )

            same_channel = (
                str(
                    cfg.get("channel_id")
                    or ""
                )
                == target_id
            )

            # Try to find the existing public schedule message.
            if stored_message_id and same_channel:
                try:
                    message = (
                        await channel.fetch_message(
                            int(stored_message_id)
                        )
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
                    # Only edit the public message when
                    # the actual 3-hour trial changes.
                    if (
                        force
                        or self._last_slot
                        != slot_no
                    ):
                        await message.edit(
                            embed=embed
                        )

                else:
                    # Create the public schedule message.
                    message = await channel.send(
                        embed=embed
                    )

                    save_trial_schedule_settings(
                        {
                            "channel_id": target_id,
                            "message_id": str(
                                message.id
                            ),
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

        if not cfg.get("enabled"):
            return

        if not cfg.get("channel_id"):
            return

        current_slot = _slot_number(
            dt.datetime.now(EASTERN)
        )

        # The public schedule only needs to be edited
        # when the 3-hour trial changes.
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
        # Anyone can use this command.
        #
        # ephemeral=True means ONLY the person who
        # used /trial_schedule can see the response.
        #
        # This does NOT modify the public schedule.
        embed, _ = build_trial_schedule_embed()

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )


async def setup(
    bot: commands.Bot,
):
    await bot.add_cog(
        TrialSchedule(bot)
    )

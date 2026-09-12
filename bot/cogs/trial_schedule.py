import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.storage import get_trial_schedule_settings, save_trial_schedule_settings
from utils.access import has_carry_manager_access

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


class TrialScheduleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Modifier strategies",
                url=DOC_URL,
                style=discord.ButtonStyle.link,
                emoji="📖",
            )
        )


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

    lines = []

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

    return embed, slot_number


def build_trial_schedule_v2_view(description: str):
    layout_cls = getattr(discord.ui, "LayoutView", None)
    container_cls = getattr(discord.ui, "Container", None)
    text_display_cls = getattr(discord.ui, "TextDisplay", None)
    separator_cls = getattr(discord.ui, "Separator", None)
    action_row_cls = getattr(discord.ui, "ActionRow", None)
    if not all((layout_cls, container_cls, text_display_cls, action_row_cls)):
        return None

    class TrialScheduleV2View(layout_cls):
        def __init__(self):
            super().__init__()
            container = container_cls(accent_color=discord.Color.blurple())
            container.add_item(text_display_cls("## 🗓️ Trial Schedule"))
            if separator_cls:
                container.add_item(separator_cls())
            container.add_item(text_display_cls(description))
            row = action_row_cls()
            row.add_item(
                discord.ui.Button(
                    label="Modifier strategies",
                    url=DOC_URL,
                    style=discord.ButtonStyle.link,
                    emoji="📖",
                )
            )
            container.add_item(row)
            self.add_item(container)

    return TrialScheduleV2View()


def trial_schedule_message(embed, mode: str):
    if mode == "components_v2":
        description = embed.description or ""
        view = build_trial_schedule_v2_view(description)
        if view is not None:
            return {"view": view}
    return {"embed": embed, "view": TrialScheduleView()}


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

            embed, slot_no = build_trial_schedule_embed()
            message_kwargs = trial_schedule_message(
                embed, cfg.get("display_mode", "embed")
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
                        await message.edit(**message_kwargs)

                else:
                    # Create the public schedule message.
                    message = await channel.send(**message_kwargs)

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
    @app_commands.allowed_contexts(
        guilds=True,
        dms=True,
        private_channels=True,
    )
    @app_commands.allowed_installs(
        guilds=True,
        users=True,
    )
    async def trial_schedule(
        self,
        interaction: discord.Interaction,
    ):
        # Anyone can use this command from any channel. Show the schedule
        # directly in the command response instead of sending a separate DM.
        embed, _ = build_trial_schedule_embed()
        cfg = get_trial_schedule_settings()
        message_kwargs = trial_schedule_message(
            embed, cfg.get("display_mode", "embed")
        )

        await interaction.response.send_message(
            **message_kwargs,
        )

    @app_commands.command(
        name="trial_schedule_settings",
        description="Choose the Trial Schedule display style.",
    )
    @app_commands.describe(
        style="Choose whether the schedule uses an embed or Components V2.",
    )
    @app_commands.choices(
        style=[
            app_commands.Choice(name="Embed", value="embed"),
            app_commands.Choice(name="Components V2", value="components_v2"),
        ]
    )
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def trial_schedule_settings(
        self,
        interaction: discord.Interaction,
        style: app_commands.Choice[str],
    ):
        role_ids = [str(role.id) for role in getattr(interaction.user, "roles", [])]
        if not has_carry_manager_access(interaction.user.id, role_ids):
            await interaction.response.send_message(
                "❌ You don't have permission to change Trial Schedule settings.",
                ephemeral=True,
            )
            return

        save_trial_schedule_settings({"display_mode": style.value})
        message = await self.publish_or_update(force=True)
        if message is None:
            await interaction.response.send_message(
                f"✅ Trial Schedule style saved as **{style.name}**, but the tracked "
                "schedule message could not be updated. Check the configured channel.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ Trial Schedule style changed to **{style.name}** and the public "
            "schedule was updated.",
            ephemeral=True,
        )


async def setup(
    bot: commands.Bot,
):
    await bot.add_cog(
        TrialSchedule(bot)
    )

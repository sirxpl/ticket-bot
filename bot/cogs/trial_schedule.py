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

# The user-facing schedule is Eastern Time. America/New_York is used instead
# of a fixed UTC-5 offset so the clock stays correct during daylight saving.
EASTERN = ZoneInfo("America/New_York")
DOC_URL = "https://docs.google.com/document/d/1NzhAEK4WJ9cA2gDCcACtW-HXQHhJplrE-_ZKkPWXIJk/edit?tab=t.pdj0ytau4jf4"

# Anchor supplied for this schedule: Limitation begins Sep 2, 2026 at 11:00 PM ET.
ANCHOR = dt.datetime(2026, 9, 2, 23, 0, tzinfo=EASTERN)
SLOT = dt.timedelta(hours=3)

# Keep emoji text in one place so custom server emoji markup can replace these
# later (for example: "<:fog:123456789012345678>") without touching the logic.
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
    return ANCHOR + (SLOT * number)


def _fmt_time(when: dt.datetime, now: dt.datetime) -> str:
    """Screenshot-like time: today = 11:00 PM; later dates = 9/4/26, 2:00 AM."""
    local = when.astimezone(EASTERN)
    clock = local.strftime("%I:%M %p").lstrip("0")
    if local.date() == now.astimezone(EASTERN).date():
        return clock
    return f"{local.month}/{local.day}/{str(local.year)[2:]}, {clock}"


def build_trial_schedule_embed(now: dt.datetime | None = None) -> tuple[discord.Embed, int]:
    now = (now or dt.datetime.now(EASTERN)).astimezone(EASTERN)
    number = _slot_number(now)
    current_idx = number % len(TRIALS)
    current_start = _slot_start(number)
    current_end = current_start + SLOT
    emoji, name = TRIALS[current_idx]

    lines = [
        f"Strategies for modifiers [here]({DOC_URL}).",
        "",
        # Discord renders the relative timestamp itself, so 'in an hour' can
        # count down without the bot editing the message every minute.
        f"{emoji} **{name}** ends <t:{int(current_end.timestamp())}:R>",
    ]

    # Show the next 12 modifiers, matching the long schedule style in the example.
    for offset in range(1, 13):
        slot_no = number + offset
        e, n = TRIALS[slot_no % len(TRIALS)]
        lines.append(f"{e} **{n}** {_fmt_time(_slot_start(slot_no), now)}")

    embed = discord.Embed(
        title="Trial Schedule",
        description="\n".join(lines),
        color=discord.Color.from_rgb(47, 49, 54),
    )
    embed.set_footer(text="Eastern Time (EST/EDT) • Updates automatically")
    return embed, number


class TrialSchedule(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_slot = None
        self._update_lock = asyncio.Lock()
        self.auto_updater.start()

    def cog_unload(self):
        self.auto_updater.cancel()

    async def publish_or_update(self, channel_id: int | str | None = None, force: bool = False):
        """Create or edit the configured schedule message with minimal API calls."""
        async with self._update_lock:
            cfg = get_trial_schedule_settings()
            target_id = str(channel_id or cfg.get("channel_id") or "").strip()
            if not target_id:
                return None

            try:
                channel = self.bot.get_channel(int(target_id)) or await self.bot.fetch_channel(int(target_id))
            except (discord.HTTPException, discord.NotFound, discord.Forbidden, ValueError, TypeError):
                logger.warning("Could not resolve trial schedule channel %s", target_id)
                return None

            embed, slot_no = build_trial_schedule_embed()
            message = None
            stored_message_id = cfg.get("message_id")
            same_channel = str(cfg.get("channel_id") or "") == target_id

            if stored_message_id and same_channel:
                try:
                    message = await channel.fetch_message(int(stored_message_id))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError, TypeError):
                    message = None

            try:
                if message:
                    if force or self._last_slot != slot_no:
                        await message.edit(embed=embed)
                else:
                    message = await channel.send(embed=embed)
                    save_trial_schedule_settings({
                        "channel_id": target_id,
                        "message_id": str(message.id),
                    })
                self._last_slot = slot_no
                return message
            except discord.HTTPException as exc:
                # discord.py already honors Discord's Retry-After/rate-limit
                # handling. Do not hot-loop or manually retry here.
                logger.warning("Trial schedule Discord API request failed: %s", exc)
                return None

    @tasks.loop(seconds=60)
    async def auto_updater(self):
        cfg = get_trial_schedule_settings()
        if not cfg.get("enabled") or not cfg.get("channel_id"):
            return
        current_slot = _slot_number(dt.datetime.now(EASTERN))
        # Only edit when a 3-hour trial boundary changes. Relative timestamps
        # update client-side, which keeps this extremely light on Discord API use.
        if self._last_slot != current_slot:
            await self.publish_or_update()

    @auto_updater.before_loop
    async def before_auto_updater(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="trial_schedule", description="Post or refresh the current Trial Schedule")
    async def trial_schedule(self, interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message(
                "❌ You do not have permission to use `/trial_schedule`.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        # Slash command posts in the channel where it is used. This does not
        # replace the dashboard's configured auto-post unless it is that channel.
        embed, _ = build_trial_schedule_embed()
        try:
            await interaction.channel.send(embed=embed)
            await interaction.followup.send("✅ Trial Schedule posted.", ephemeral=True)
        except discord.HTTPException:
            await interaction.followup.send(
                "❌ I couldn't post the Trial Schedule in this channel.", ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(TrialSchedule(bot))

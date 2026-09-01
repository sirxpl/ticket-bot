import asyncio
import functools
import time

from discord.ext import commands


class RateLimitGuard(commands.Cog):
    """Small safety layer for the bot's own high-level API patterns.

    discord.py already owns the actual Discord HTTP rate-limit buckets and
    handles Retry-After/429 responses internally. This cog avoids one common
    source of unnecessary bursts in this project: synchronizing every slash
    command again on every gateway reconnect.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._sync_installed = False
        self._sync_lock = asyncio.Lock()
        self._install_sync_guard()

    def _install_sync_guard(self):
        if self._sync_installed:
            return

        tree = self.bot.tree
        original_sync = tree.sync
        guard = self

        @functools.wraps(original_sync)
        async def guarded_sync(*args, **kwargs):
            # Discord command registration is not something we need to repeat
            # every time the gateway reconnects. Only the first sync after a
            # process start performs the REST operation.
            if getattr(guard, "_commands_synced", False):
                return list(getattr(guard, "_synced_commands", []))

            async with guard._sync_lock:
                if getattr(guard, "_commands_synced", False):
                    return list(getattr(guard, "_synced_commands", []))

                # Tiny spacing before the first registration call prevents a
                # reconnect/startup burst from landing at exactly the same
                # moment as other bot REST traffic.
                await asyncio.sleep(0.5)
                result = await original_sync(*args, **kwargs)
                guard._synced_commands = list(result or [])
                guard._commands_synced = True
                return result

        tree.sync = guarded_sync
        self._sync_installed = True


async def setup(bot: commands.Bot):
    await bot.add_cog(RateLimitGuard(bot))

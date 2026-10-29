"""Saved access audit events and best-effort Components V2 Discord delivery."""
import asyncio
import datetime
import logging
import threading
import time

import discord
from utils.components import embed_layout

logger = logging.getLogger("audit")
AUDIT_EVENTS = {
    "ticket_creation_denied": "Ticket creation blocked by blacklist",
    "blacklist_added": "Member blacklisted",
    "blacklist_removed": "Member unblacklisted",
    "blacklist_expired": "Timed blacklist expired",
    "blacklist_role_added": "Blacklist role configured",
    "blacklist_role_updated": "Blacklist role configuration changed",
    "blacklist_role_deleted": "Blacklist role configuration removed",
    "blacklist_role_assigned": "Blacklist role assigned in Discord",
    "blacklist_role_unassigned": "Blacklist role removed in Discord",
    "user_blocked": "User globally blocked",
    "user_unblocked": "User globally unblocked",
    "cooldown_removed": "Ticket cooldown cleared",
    "access_entries_removed": "Access entries removed in bulk",
    "unblock_link_created": "Terms unblock link created",
    "unblock_link_revoked": "Terms unblock link revoked",
}
_expected = {}
_role_lock = threading.Lock()


async def change_role(member, role, *, add, reason):
    """Suppress the gateway echo of a command that already emits its own log."""
    if any(item.id == role.id for item in member.roles) == add:
        return
    key = (member.guild.id, member.id, role.id, add)
    with _role_lock:
        now = time.monotonic()
        for old in [old for old, until in _expected.items() if until <= now]:
            _expected.pop(old, None)
        _expected[key] = now + 90
    try:
        operation = member.add_roles if add else member.remove_roles
        await operation(role, reason=reason[:500])
    except Exception:
        with _role_lock:
            _expected.pop(key, None)
        raise


def consume_role_echo(guild_id, user_id, role_id, added):
    with _role_lock:
        until = _expected.pop((guild_id, user_id, role_id, added), 0)
    return until > time.monotonic()


def record_event(action, actor_id=None, **fields):
    from utils.storage import append_ticket_log
    try:
        return append_ticket_log({"ticket_id": str(fields.get("ticket_id") or "global"),
            "action": action, "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "executor": {"id": str(actor_id), "name": str(actor_id)} if actor_id else {"name": "System / Discord"},
            **fields})
    except Exception:
        logger.exception("Could not save audit event %s", action)
        return False


async def send_event(bot, action, **fields):
    from utils.dashboard_settings import get_ticket_logging_config
    try:
        config = get_ticket_logging_config()
        if not config["audit_enabled"] or action not in config["audit_events"]:
            return
        channel_id = (config.get("blacklist_log_channel_id") if action.startswith("blacklist_") or action == "ticket_creation_denied" else None)
        channel_id = channel_id or config.get("access_log_channel_id") or config.get("log_channel_id")
        if not channel_id:
            return
        channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
        embed = discord.Embed(title=AUDIT_EVENTS.get(action, action.replace("_", " ").title()),
            color=discord.Color.blurple(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        for key, value in fields.items():
            if value is not None:
                embed.add_field(name=key.replace("_", " ").title(), value=str(value)[:1000], inline=False)
        await channel.send(view=embed_layout(embed), allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.exception("Could not deliver Discord audit event %s", action)


def emit_event(bot, action, actor_id=None, **fields):
    """Flask mutations save history even while the Discord connection is offline."""
    record_event(action, actor_id, **fields)
    coroutine = None
    try:
        if not bot.is_ready() or bot.loop.is_closed():
            return
        coroutine = send_event(bot, action, executor=str(actor_id) if actor_id else "System", **fields)
        future = asyncio.run_coroutine_threadsafe(coroutine, bot.loop)
        def finished(result):
            try:
                result.result()
            except Exception:
                logger.exception("Discord audit scheduling failed for %s", action)
        future.add_done_callback(finished)
    except Exception:
        if coroutine is not None:
            coroutine.close()
        logger.exception("Could not schedule audit event %s", action)


def role_config_events(bot, before, after, actor_id):
    old = {str(row["role_id"]): row for row in before}
    new = {str(row["role_id"]): row for row in after}
    for role in sorted(old.keys() | new.keys()):
        if role not in old:
            action = "blacklist_role_added"
        elif role not in new:
            action = "blacklist_role_deleted"
        elif old[role] != new[role]:
            action = "blacklist_role_updated"
        else:
            continue
        row = new.get(role) or old[role]
        emit_event(bot, action, actor_id, role_id=role, name=row.get("name"),
                   reason=row.get("reason"), scope="All tickets" if row.get("block_all") else "Selected categories",
                   source="Access Control")

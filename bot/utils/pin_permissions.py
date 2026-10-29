"""Native Discord Pin Messages overwrites for active ticket channels."""
import logging

import discord
from utils import access, storage

logger = logging.getLogger("pin_permissions")


def selected_roles(settings=None):
    settings = settings if settings is not None else access.get_access_settings()
    return {str(value) for value in settings.get("pin_message_roles", []) if str(value).isdigit()}


def change_roles(*, add=(), remove=()):
    """Persist selection plus offline removal work without replacing access settings."""
    from utils.dashboard_settings import _database, _file_lock, _read_json, _write_json, selected_ids
    adding = set(selected_ids(add)) if add else set()
    removing = set(selected_ids(remove)) if remove else set()
    if not adding and not removing:
        raise ValueError("Select at least one role.")
    if adding and removing:
        raise ValueError("Add or remove roles in one operation.")
    db = _database(access)
    with _file_lock:
        settings = (db.access_control.find_one({"_id": "singleton"}) or {}) if db is not None else _read_json(access.ACCESS_FILE, {})
        before = selected_roles(settings)
        after = (before | adding) - removing
        if before == after:
            return 0
        pending = {str(value) for value in settings.get("pin_permission_pending_removals", [])}
        pending = (pending | (before - after)) - after
        updates = {"pin_message_roles": sorted(after), "pin_permission_pending_removals": sorted(pending),
                   "pin_permission_revision": int(settings.get("pin_permission_revision", 0)) + 1}
        if db is not None:
            # A lease prevents a simultaneous settings edit from being lost.
            criteria = {"_id": "singleton", "pin_permission_revision": settings.get("pin_permission_revision")}
            result = db.access_control.update_one(criteria, {"$set": updates}, upsert=not settings)
            if settings and not result.matched_count:
                raise RuntimeError("Pin access changed while saving. Reload and retry.")
        else:
            settings.update(updates)
            _write_json(access.ACCESS_FILE, settings)
    return len(before ^ after)


def apply_new_ticket_overwrites(guild, overwrites):
    """Grant only the dedicated pin flag, preserving all other creation overwrites."""
    if not hasattr(discord.PermissionOverwrite(), "pin_messages"):
        raise RuntimeError("Native pin permissions require discord.py 2.7.1 or newer.")
    applied = []
    for role_id in sorted(selected_roles()):
        role = guild.get_role(int(role_id))
        if role is None:
            continue
        existing = overwrites.get(role)
        overwrite = discord.PermissionOverwrite.from_pair(*existing.pair()) if existing is not None else discord.PermissionOverwrite()
        overwrite.pin_messages = True
        overwrites[role] = overwrite
        applied.append(role_id)
    return applied


def _clear_pending(settings):
    from utils.dashboard_settings import _database, _file_lock, _read_json, _write_json
    db = _database(access)
    revision = int(settings.get("pin_permission_revision", 0))
    if db is not None:
        criteria = {"_id": "singleton", "pin_permission_revision": settings.get("pin_permission_revision")}
        db.access_control.update_one(criteria, {"$set": {"pin_permission_pending_removals": []}})
    else:
        with _file_lock:
            current = _read_json(access.ACCESS_FILE, {})
            if int(current.get("pin_permission_revision", 0)) == revision:
                current["pin_permission_pending_removals"] = []
                _write_json(access.ACCESS_FILE, current)


async def sync_active_tickets(bot):
    """Reconcile only active ticket channels; failures retain pending removals."""
    settings = access.get_access_settings()
    selected = selected_roles(settings)
    pending = {str(value) for value in settings.get("pin_permission_pending_removals", []) if str(value).isdigit()}
    result = {"updated": 0, "failed": 0}
    from utils.dashboard_settings import _database
    db = _database(storage)
    # Never clear pending revocations using an ephemeral fallback during a DB outage.
    tickets = (db.tickets_data.find_one({"_id": "singleton"}) or {}) if db is not None else storage.get_tickets_data()
    for ticket in tickets.get("active_tickets", []):
        channel_id = ticket.get("channel_id")
        if not str(channel_id).isdigit():
            continue
        try:
            channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
            if not getattr(channel, "guild", None):
                continue
            previous = {str(value) for value in ticket.get("pin_permission_managed_roles", []) if str(value).isdigit()}
            managed = set()
            for role_id in sorted(selected | previous | pending):
                role = channel.guild.get_role(int(role_id))
                if role is None:
                    continue
                existing = channel.overwrites_for(role)
                desired = True if role_id in selected else None
                if role_id in selected:
                    managed.add(role_id)
                # Removals clear our Allow; they never erase an explicit Deny.
                if existing.pin_messages == desired or (desired is None and existing.pin_messages is not True):
                    continue
                overwrite = discord.PermissionOverwrite.from_pair(*existing.pair())
                overwrite.pin_messages = desired
                await channel.set_permissions(role, overwrite=None if overwrite.is_empty() else overwrite,
                    reason="Synchronize native ticket Pin Messages access")
                result["updated"] += 1
            if managed != previous:
                if not storage.update_active_ticket(channel_id, pin_permission_managed_roles=sorted(managed)):
                    raise RuntimeError("Could not save pin permission synchronization state.")
        except discord.NotFound:
            continue  # A deleted ticket has no permissions left to update.
        except Exception:
            result["failed"] += 1
            logger.exception("Could not synchronize Pin Messages for ticket=%s", channel_id)
    if not result["failed"] and pending:
        _clear_pending(settings)
    return result

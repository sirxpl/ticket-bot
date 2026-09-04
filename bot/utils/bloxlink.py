"""Bloxlink (Discord -> Roblox) lookups and Roblox badge ownership checks.

Used by the ticket flow to gate ticket creation on a category's
`bloxlink_verification` / `required_badges` / `badge_requirement` settings.
"""

import asyncio
import logging
import os

import requests

logger = logging.getLogger("bloxlink")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)

BLOXLINK_API_BASE = "https://api.blox.link/v4/public"
ROBLOX_BADGES_API = "https://badges.roblox.com/v1"
ROBLOX_USERS_API = "https://users.roblox.com/v1"
REQUEST_TIMEOUT = 10
# Roblox caps the awarded-dates endpoint at 100 badge ids per call
BADGE_BATCH_SIZE = 100


class BloxlinkError(Exception):
    """Raised when a Roblox account can't be resolved for a Discord user."""


def get_api_key():
    return os.getenv("BLOXLINK_API_KEY") or ""


def _lookup_roblox_id(discord_user_id, guild_id=None):
    api_key = get_api_key()
    if not api_key:
        raise BloxlinkError(
            "Bloxlink verification isn't configured on this bot (missing BLOXLINK_API_KEY)."
        )

    urls = []
    if guild_id:
        urls.append(f"{BLOXLINK_API_BASE}/guilds/{guild_id}/discord-to-roblox/{discord_user_id}")
    urls.append(f"{BLOXLINK_API_BASE}/discord-to-roblox/{discord_user_id}")

    last_error = None
    for url in urls:
        try:
            resp = requests.get(url, headers={"Authorization": api_key}, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            last_error = f"Couldn't reach Bloxlink ({e.__class__.__name__})."
            continue

        if resp.status_code == 404:
            last_error = "no_link"
            continue
        if resp.status_code in (401, 403):
            raise BloxlinkError("Bloxlink rejected this bot's API key.")
        if resp.status_code == 429:
            last_error = "Bloxlink is rate limiting us. Please try again in a moment."
            continue
        if resp.status_code >= 400:
            last_error = f"Bloxlink returned an error (HTTP {resp.status_code})."
            continue

        try:
            data = resp.json()
        except ValueError:
            last_error = "Bloxlink returned an unreadable response."
            continue

        roblox_id = data.get("robloxID") or data.get("robloxId")
        if roblox_id:
            return str(roblox_id)
        last_error = "no_link"

    if last_error == "no_link":
        raise BloxlinkError(
            "Your Discord account isn't linked to a Roblox account on Bloxlink. "
            "Verify at https://blox.link/verify and try again."
        )
    raise BloxlinkError(last_error or "Bloxlink lookup failed.")


def _lookup_roblox_username(roblox_id):
    try:
        resp = requests.get(f"{ROBLOX_USERS_API}/users/{roblox_id}", timeout=REQUEST_TIMEOUT)
        if resp.ok:
            data = resp.json()
            return data.get("name") or None
    except Exception:
        logger.exception(f"Failed to fetch Roblox username for id={roblox_id}")
    return None


def _lookup_owned_badges(roblox_id, badge_ids):
    """Returns the subset of badge_ids the Roblox user has been awarded."""
    owned = []
    for i in range(0, len(badge_ids), BADGE_BATCH_SIZE):
        batch = badge_ids[i:i + BADGE_BATCH_SIZE]
        url = f"{ROBLOX_BADGES_API}/users/{roblox_id}/badges/awarded-dates"
        try:
            resp = requests.get(
                url, params={"badgeIds": ",".join(batch)}, timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException as e:
            raise BloxlinkError(f"Couldn't reach Roblox to check badges ({e.__class__.__name__}).")
        if resp.status_code == 429:
            raise BloxlinkError("Roblox is rate limiting badge checks. Please try again shortly.")
        if not resp.ok:
            raise BloxlinkError(f"Roblox badge check failed (HTTP {resp.status_code}).")
        try:
            entries = resp.json().get("data") or []
        except ValueError:
            raise BloxlinkError("Roblox returned an unreadable badge response.")
        owned.extend(str(e.get("badgeId")) for e in entries if e.get("badgeId") is not None)
    return owned


def normalize_badge_ids(badge_ids):
    normalized = []
    for b in badge_ids or []:
        b = str(b).strip()
        if b.isdigit() and b not in normalized:
            normalized.append(b)
    return normalized


def _verify_sync(discord_user_id, guild_id, badge_ids, badge_requirement):
    roblox_id = _lookup_roblox_id(discord_user_id, guild_id)
    badge_ids = normalize_badge_ids(badge_ids)
    result = {
        "roblox_id": roblox_id,
        "roblox_username": _lookup_roblox_username(roblox_id),
        "required_badges": badge_ids,
        "badge_requirement": "ALL" if str(badge_requirement).upper() == "ALL" else "ANY",
        "owned_badges": [],
        "missing_badges": [],
        "has_required_badges": True,
    }
    if not badge_ids:
        return result

    owned = _lookup_owned_badges(roblox_id, badge_ids)
    result["owned_badges"] = owned
    result["missing_badges"] = [b for b in badge_ids if b not in owned]
    result["has_required_badges"] = (
        not result["missing_badges"] if result["badge_requirement"] == "ALL" else bool(owned)
    )
    return result


async def verify_user(discord_user_id, guild_id=None, badge_ids=None, badge_requirement="ANY"):
    """Resolve the user's Roblox account via Bloxlink and check badge ownership.

    Raises BloxlinkError when the account can't be resolved or Roblox/Bloxlink
    can't be reached; otherwise returns a dict describing the badge outcome.
    """
    return await asyncio.to_thread(
        _verify_sync, str(discord_user_id), guild_id, badge_ids or [], badge_requirement
    )

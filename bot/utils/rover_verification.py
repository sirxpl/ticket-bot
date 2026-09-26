import os
import requests

ROVER_API_BASE = os.getenv("ROVER_API_BASE_URL", "https://registry.rover.link/api").rstrip("/")
ROBLOX_BADGE_URL = "https://badges.roblox.com/v1/users/{user_id}/badges/awarded-dates"


def _headers():
    key=os.getenv("ROVER_API_KEY", "").strip()
    if not key:
        return None
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


def _extract_roblox_id(data):
    if not isinstance(data, dict): return None
    for key in ("robloxId", "roblox_id", "robloxUserId", "userId", "id"):
        value=data.get(key)
        if value is not None and str(value).isdigit(): return str(value)
    return None


def check_badge_for_discord_user(discord_id, badge_id):
    """Return (allowed, reason, details). Requires the RoVer API key server-side.

    RoVer maps the Discord account to Roblox; Roblox's public badge endpoint then
    confirms whether the configured badge has ever been awarded to that account.
    """
    badge_id=str(badge_id or "").strip()
    if not badge_id.isdigit(): return False, "The badge ID is not configured correctly.", {}
    headers=_headers()
    if not headers: return False, "RoVer API is not configured yet. Add ROVER_API_KEY in Render.", {}
    try:
        r=requests.get(f"{ROVER_API_BASE}/discord-to-roblox/{int(discord_id)}", headers=headers, timeout=10)
        if r.status_code == 404: return False, "This Discord account is not linked through RoVer.", {}
        r.raise_for_status()
        data=r.json()
        if data.get("verified") is False:
            return False, "This Discord account is not verified through RoVer.", data
        roblox_id=_extract_roblox_id(data)
        if not roblox_id: return False, "RoVer did not return a linked Roblox account for this Discord user.", data

        br=requests.get(ROBLOX_BADGE_URL.format(user_id=roblox_id), params={"badgeIds":badge_id}, timeout=10)
        br.raise_for_status()
        badge_data=br.json()
        owned=bool(badge_data.get("data"))
        return owned, ("Badge verified." if owned else "The linked Roblox account does not have the required badge."), {"roblox_id":roblox_id,"rover":data,"badge":badge_data}
    except requests.RequestException as exc:
        return False, f"Verification service could not be reached: {exc}", {}
    except ValueError:
        return False, "Verification service returned an invalid response.", {}


def check_blocked_badges_for_discord_user(discord_id, badge_ids):
    """Return (owned_badges, reason, details) for a list of blocked badges."""
    ids=[]
    for badge_id in badge_ids or []:
        badge_id=str(badge_id).strip()
        if badge_id.isdigit() and badge_id not in ids:
            ids.append(badge_id)
    if not ids:
        return [], "No blocked badges are configured.", {}
    headers=_headers()
    if not headers:
        return [], "RoVer API is not configured yet. Add ROVER_API_KEY in Render.", {}
    try:
        r=requests.get(f"{ROVER_API_BASE}/discord-to-roblox/{int(discord_id)}", headers=headers, timeout=10)
        if r.status_code == 404:
            return [], "This Discord account is not linked through RoVer.", {}
        r.raise_for_status()
        data=r.json()
        if data.get("verified") is False:
            return [], "This Discord account is not verified through RoVer.", data
        roblox_id=_extract_roblox_id(data)
        if not roblox_id:
            return [], "RoVer did not return a linked Roblox account for this Discord user.", data

        owned=[]
        # Roblox's awarded-dates endpoint accepts badgeIds; keep requests bounded
        # so a long admin list cannot cause an excessive request burst.
        for start in range(0, len(ids), 25):
            batch=ids[start:start+25]
            br=requests.get(
                ROBLOX_BADGE_URL.format(user_id=roblox_id),
                params={"badgeIds": ",".join(batch)},
                timeout=10,
            )
            br.raise_for_status()
            badge_data=br.json()
            for item in badge_data.get("data", []) if isinstance(badge_data, dict) else []:
                value=item.get("badgeId") if isinstance(item, dict) else None
                if value is not None and str(value) in batch and str(value) not in owned:
                    owned.append(str(value))
        return owned, ("Blocked badge found." if owned else "None of the blocked badges were found."), {"roblox_id":roblox_id,"rover":data,"owned_badges":owned}
    except requests.RequestException as exc:
        return [], f"Verification service could not be reached: {exc}", {}
    except ValueError:
        return [], "Verification service returned an invalid response.", {}



def check_required_badges_for_discord_user(discord_id, badge_ids):
    """Return (owned_badges, reason, details) for required badges.

    A user passes when they own at least one configured required badge.
    RoVer supplies the Discord -> Roblox mapping; Roblox confirms badge ownership.
    """
    return check_blocked_badges_for_discord_user(discord_id, badge_ids)

import os
import requests

ROVER_API_BASE = os.getenv("ROVER_API_BASE_URL", "https://registry.rover.link/api").rstrip("/")
# Roblox's docs mark the bulk /badges/awarded-dates endpoint "Not Recommended"
# and it has also started 403ing requests from cloud/datacenter IPs (like
# Render's) that don't look like a real browser. The single-badge
# awarded-date endpoint is the documented replacement, and is checked once
# per badge ID rather than in one bulk call.
ROBLOX_SINGLE_BADGE_URL = "https://badges.roblox.com/v1/users/{user_id}/badges/{badge_id}/awarded-date"
ROVER_CONSENT_URL = "https://rover.link/consent"

# Roblox's public API commonly rejects requests whose User-Agent looks like a
# bare HTTP client (e.g. requests' default "python-requests/2.x"), treating
# them as bot/scraper traffic and returning 403 regardless of the badge/user
# being valid. Sending ordinary browser-style headers avoids that.
_ROBLOX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# Words RoVer's own API error messages use when a user is verified but hasn't
# granted this server (or this bot's API key) permission to see their linked
# Roblox account. See: https://rover.link/help/username-privacy-and-consent
_CONSENT_HINT_WORDS = ("consent", "access", "grant", "permission", "reveal")


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


def _looks_like_consent_error(status_code, body_text):
    if status_code != 403:
        return False
    text = (body_text or "").lower()
    return any(word in text for word in _CONSENT_HINT_WORDS)


def _get_linked_roblox_id(guild_id, discord_id):
    """Resolve a Discord user's linked Roblox ID through RoVer for one guild.

    RoVer's real API is scoped per-guild: GET /guilds/{guild_id}/discord-to-roblox/{user_id}.
    A previous version of this file called /discord-to-roblox/{user_id} with no
    guild segment at all, which RoVer always answers with 404 (route doesn't
    exist). That 404 was being read as "this account isn't linked", which in
    the blocked-badge flow gets treated as "no blocked badge found" — i.e. the
    endpoint being wrong was silently letting every user through the gate.

    RoVer also now requires the user to separately grant this server (or the
    bot's API key) permission to reveal their linked Roblox account, even once
    they're verified — see the URL above. When RoVer's response indicates that
    permission hasn't been granted, this is reported back distinctly so callers
    can point the user at https://rover.link/consent instead of a generic
    failure or, worse, silently treating it as "no badge found."

    Returns (roblox_id_or_None, reason, data, ok, needs_consent).
    - `ok` is False only when the lookup itself could not be completed (bad
      config, network error, RoVer outage, etc.) — callers should treat that
      as "verification could not be confirmed" and block rather than allow.
    - `needs_consent` is True when RoVer confirms the account exists but the
      user hasn't granted this server/bot access to see it.
    """
    if not guild_id:
        return None, "This check requires a server (guild) context and none was available.", {}, False, False
    headers=_headers()
    if not headers:
        return None, "RoVer API is not configured yet. Add ROVER_API_KEY in Render.", {}, False, False
    try:
        r=requests.get(
            f"{ROVER_API_BASE}/guilds/{int(guild_id)}/discord-to-roblox/{int(discord_id)}",
            headers=headers, timeout=10,
        )
        if r.status_code == 404:
            return None, "This Discord account is not linked through RoVer.", {}, True, False
        if r.status_code == 401:
            return None, "RoVer rejected the API key. Check ROVER_API_KEY in Render.", {}, False, False
        if r.status_code == 403:
            if _looks_like_consent_error(r.status_code, r.text):
                try:
                    body = r.json()
                    msg = body.get("message") or body.get("detail")
                except Exception:
                    msg = None
                return (
                    None,
                    msg or "This member is verified with RoVer but hasn't granted this server permission to see their linked Roblox account yet.",
                    {}, True, True,
                )
            return None, "RoVer API key does not have access to this server. Make sure RoVer and this bot are both in the server and the key was created for it.", {}, False, False
        r.raise_for_status()
        data=r.json()
        if data.get("verified") is False:
            return None, "This Discord account is not verified through RoVer.", data, True, False
        # Some RoVer responses may still include an explicit access/consent
        # flag alongside a verified account; treat it the same way if present.
        if data.get("hasAccess") is False or data.get("consentGranted") is False:
            return None, "This member is verified with RoVer but hasn't granted this server permission to see their linked Roblox account yet.", data, True, True
        roblox_id=_extract_roblox_id(data)
        if not roblox_id:
            return None, "RoVer did not return a linked Roblox account for this Discord user.", data, True, False
        return roblox_id, "Linked.", data, True, False
    except requests.RequestException as exc:
        return None, f"Verification service could not be reached: {exc}", {}, False, False
    except ValueError:
        return None, "Verification service returned an invalid response.", {}, False, False


def _roblox_user_has_badge(roblox_id, badge_id):
    """Check one badge via Roblox's recommended per-badge endpoint.

    Returns (owned, ok, reason). ok=False means the request itself failed
    (network error, unexpected status, bad response) and the result should
    not be trusted.
    """
    try:
        br = requests.get(
            ROBLOX_SINGLE_BADGE_URL.format(user_id=roblox_id, badge_id=badge_id),
            headers=_ROBLOX_HEADERS,
            timeout=10,
        )
        if br.status_code == 404:
            # Roblox returns 404 for a badge the user has never been awarded.
            return False, True, None
        if br.status_code == 403:
            return False, False, (
                f"Roblox blocked the badge lookup for badge {badge_id} (403 Forbidden). "
                "This usually means Roblox's API is rejecting requests from this server's IP/User-Agent."
            )
        br.raise_for_status()
        data = br.json()
        # A successful response with a real awarded date means they own it;
        # some responses may still come back as an explicit null/empty date.
        owned = bool(data) and data.get("awardedDate") is not None if isinstance(data, dict) else bool(data)
        return owned, True, None
    except requests.RequestException as exc:
        return False, False, f"Verification service could not be reached: {exc}"
    except ValueError:
        return False, False, "Verification service returned an invalid response."


def check_badge_for_discord_user(guild_id, discord_id, badge_id):
    """Return (allowed, reason, details, ok, needs_consent). Requires the RoVer
    API key server-side. RoVer maps the Discord account to Roblox; Roblox's
    public badge endpoint then confirms whether the configured badge has ever
    been awarded to that account. ok=False means the check could not be
    completed and the result should not be trusted either way.
    """
    badge_id=str(badge_id or "").strip()
    if not badge_id.isdigit(): return False, "The badge ID is not configured correctly.", {}, False, False

    roblox_id, reason, data, ok, needs_consent = _get_linked_roblox_id(guild_id, discord_id)
    if not ok or needs_consent:
        return False, reason, data, ok, needs_consent
    if not roblox_id:
        return False, reason, data, True, False

    owned, ok, err = _roblox_user_has_badge(roblox_id, badge_id)
    if not ok:
        return False, err, {"roblox_id": roblox_id, "rover": data}, False, False
    return owned, ("Badge verified." if owned else "The linked Roblox account does not have the required badge."), {"roblox_id": roblox_id, "rover": data}, True, False


def check_blocked_badges_for_discord_user(guild_id, discord_id, badge_ids):
    """Return (owned_badges, reason, details, ok, needs_consent) for a list of
    blocked badges.

    ok=False means the check could not be completed (bad API key/config,
    network failure, RoVer/Roblox outage). Callers must treat ok=False as
    "verification failed" and block the ticket — never as "no blocked badge
    was found" — otherwise a broken check silently passes everyone.

    needs_consent=True means RoVer confirms the account is verified but the
    user hasn't granted this server access to see it yet; callers should
    block and point the user at https://rover.link/consent rather than
    treating the missing Roblox ID as "no blocked badge found."
    """
    ids=[]
    for badge_id in badge_ids or []:
        badge_id=str(badge_id).strip()
        if badge_id.isdigit() and badge_id not in ids:
            ids.append(badge_id)
    if not ids:
        return [], "No blocked badges are configured.", {}, True, False

    roblox_id, reason, data, ok, needs_consent = _get_linked_roblox_id(guild_id, discord_id)
    if not ok or needs_consent:
        return [], reason, data, ok, needs_consent
    if not roblox_id:
        return [], reason, data, True, False

    owned=[]
    for badge_id in ids:
        badge_owned, ok, err = _roblox_user_has_badge(roblox_id, badge_id)
        if not ok:
            return [], err, {"roblox_id": roblox_id, "rover": data}, False, False
        if badge_owned:
            owned.append(badge_id)
    return owned, ("Blocked badge found." if owned else "None of the blocked badges were found."), {"roblox_id":roblox_id,"rover":data,"owned_badges":owned}, True, False


def check_required_badges_for_discord_user(guild_id, discord_id, badge_ids):
    """Return (owned_badges, reason, details, ok, needs_consent) for required
    badges. A user passes when they own at least one configured required
    badge. RoVer supplies the Discord -> Roblox mapping; Roblox confirms badge
    ownership.
    """
    return check_blocked_badges_for_discord_user(guild_id, discord_id, badge_ids)

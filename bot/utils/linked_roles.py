import requests

DISCORD_API = "https://discord.com/api/v10"

BOOLEAN_EQUAL = 7

ROLE_CONNECTION_METADATA = [
    {
        "key": "agreed_to_rules",
        "name": "Agreed to Carry Rules",
        "description": "The member confirmed they fully read and understand the Carry Service System Rules.",
        "type": BOOLEAN_EQUAL,
    },
]


def _headers(token: str, *, bot: bool = False):
    prefix = "Bot " if bot else "Bearer "
    return {
        "Authorization": f"{prefix}{token}",
        "Content-Type": "application/json",
        "User-Agent": "Carry-Ticket-Bot/1.0",
    }


def register_metadata(application_id: str, bot_token: str):
    if not application_id or not bot_token:
        raise RuntimeError("DISCORD_CLIENT_ID and DISCORD_BOT_TOKEN are required")
    url = f"{DISCORD_API}/applications/{application_id}/role-connections/metadata"
    resp = requests.put(
        url,
        headers=_headers(bot_token, bot=True),
        json=ROLE_CONNECTION_METADATA,
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(
            f"Discord metadata registration failed ({resp.status_code}): {resp.text[:500]}"
        )
    return resp.json()


def get_oauth_application(access_token: str):
    resp = requests.get(
        f"{DISCORD_API}/oauth2/@me",
        headers=_headers(access_token),
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(
            f"Discord OAuth session lookup failed ({resp.status_code}): {resp.text[:500]}"
        )
    return resp.json()


def push_role_connection(
    access_token: str,
    agreed: bool = True,
    platform_name: str = "Carry Ticket Bot",
    platform_username: str = "carry-rules-verified",
):
    """Publish the user's Linked Role metadata to Discord.

    Discord evaluates this metadata against the requirements configured on a
    server role. The bot does not directly assign a Linked Role.
    """
    if not access_token:
        raise RuntimeError("Missing Linked Roles OAuth access token")

    oauth_me = get_oauth_application(access_token)
    application_id = (oauth_me.get("application") or {}).get("id")
    if not application_id:
        raise RuntimeError("Discord did not return the OAuth application ID")

    url = f"{DISCORD_API}/users/@me/applications/{application_id}/role-connection"
    payload = {
        "platform_name": platform_name[:100],
        "platform_username": platform_username[:100],
        "metadata": {"agreed_to_rules": 1 if agreed else 0},
    }
    resp = requests.put(
        url,
        headers=_headers(access_token),
        json=payload,
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(
            f"Discord Linked Role update failed ({resp.status_code}): {resp.text[:500]}"
        )
    return resp.json() if resp.content else {}

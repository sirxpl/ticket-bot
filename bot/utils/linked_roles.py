import requests

DISCORD_API = "https://discord.com/api/v10"

# The single metadata field this bot verifies for Linked Roles. Discord
# supports several comparison types (INTEGER_LESS_THAN_OR_EQUAL, DATETIME_*,
# etc.) — type 7 is BOOLEAN_EQUAL, which is all we need for a plain
# "has agreed to the rules" checkbox.
BOOLEAN_EQUAL = 7

ROLE_CONNECTION_METADATA = [
    {
        "key": "agreed_to_rules",
        "name": "Agreed to Rules",
        "description": "Has agreed to the server's Rules & Regulations",
        "type": BOOLEAN_EQUAL,
    },
]


def register_metadata(application_id: str, bot_token: str):
    """One-time setup call (safe to re-run any time) that tells Discord
    what metadata fields this application can verify. Must succeed before
    any role in the server can require "Agreed to Rules" under
    Server Settings -> Roles -> [role] -> Links.
    """
    url = f"{DISCORD_API}/applications/{application_id}/role-connections/metadata"
    resp = requests.put(
        url,
        headers={
            "Authorization": f"Bot {bot_token}",
            "Content-Type": "application/json",
        },
        json=ROLE_CONNECTION_METADATA,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def push_role_connection(access_token: str, agreed: bool, platform_name: str = "Carry Ticket Bot"):
    """Called after a member completes the OAuth2 + agree flow. Tells
    Discord this specific user's metadata value, which Discord then checks
    against any role's linked-role requirement automatically — no bot
    gateway involvement, no member list needed, no privileged intents.
    """
    me_resp = requests.get(
        f"{DISCORD_API}/oauth2/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    me_resp.raise_for_status()
    application_id = me_resp.json()["application"]["id"]

    url = f"{DISCORD_API}/users/@me/applications/{application_id}/role-connection"
    resp = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "platform_name": platform_name,
            "metadata": {"agreed_to_rules": 1 if agreed else 0},
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()

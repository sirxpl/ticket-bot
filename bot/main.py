import os
import glob
import json
import re
import asyncio
import datetime
import hmac
import secrets
import time
import aiohttp
from zoneinfo import ZoneInfo
from pathlib import Path
from functools import wraps
from urllib.parse import urlsplit
from dotenv import load_dotenv

import discord
from discord.ext import commands
from flask import (
    Flask, 
    render_template, 
    request, 
    redirect, 
    flash, 
    session, 
    url_for, 
    jsonify, 
    send_from_directory,
    Response,
    abort
)
from requests_oauthlib import OAuth2Session
from werkzeug.middleware.proxy_fix import ProxyFix

# Import storage helpers
from utils.storage import (
    get_tickets_data, 
    get_blacklist_data, 
    remove_from_blacklist, 
    TRANSCRIPTS_DIR,
    get_settings,
    set_tickets_enabled,
    get_april_fools_enabled,
    set_april_fools_enabled,
    get_april_fools_ad_config,
    save_april_fools_ad_config,
    pick_april_fools_ad,
    get_ticket_logs,
    get_logs_for_ticket,
    get_transcript_info,
    get_transcript_page,
    get_transcript_html,
    list_transcript_filenames,
    get_ticket_categories,
    get_ticket_badge_config,
    save_ticket_badge_config,
    add_blocked_ticket_badge,
    remove_blocked_ticket_badge,
    get_ticket_panel_draft,
    get_carry_rules_agreement,
    save_carry_rules_agreement,
    save_ticket_panel_draft,
    get_redirect_message,
    save_redirect_message,
    remember_base_url,
    get_welcome_message,
    save_welcome_message,
    get_ticket_analytics,
    get_trial_schedule_settings,
    save_trial_schedule_settings,
    save_carry_application_delivery,
    get_premade_warning_reasons,
    add_premade_warning_reason,
    update_premade_warning_reason,
    remove_premade_warning_reason,
    get_warning_builder,
    save_warning_builder,
    get_all_warnings,
)

# Import access-control helpers
from utils.access import (
    get_access_settings,
    add_carry_application_verdict_role,
    remove_carry_application_verdict_role,
    has_carry_application_verdict_access,
    create_ticket_ad_verification,
    get_ticket_ad_verification,
    mark_ticket_ad_viewed,
    ticket_ad_seconds_remaining,
    complete_ticket_ad_verification,
    get_admin_ids,
    add_allowed_user,
    remove_allowed_user,
    add_allowed_role,
    remove_allowed_role,
    add_blacklist_role,
    remove_blacklist_role,
    add_carry_manager_role,
    remove_carry_manager_role,
    add_ticket_viewer_role,
    remove_ticket_viewer_role,
    add_powerful_command_role,
    remove_powerful_command_role,
    add_powerful_command_user,
    remove_powerful_command_user,
    add_moderation_command_role,
    remove_moderation_command_role,
    add_moderation_command_user,
    remove_moderation_command_user,
    has_moderation_command_access,
    add_basic_command_role,
    remove_basic_command_role,
    add_basic_command_user,
    remove_basic_command_user,
    add_transcripts_role,
    remove_transcripts_role,
    add_remove_cooldown_role,
    remove_remove_cooldown_role,
    add_analytics_role,
    remove_analytics_role,
    add_pin_message_role,
    remove_pin_message_role,
    set_log_channel,
    set_blacklist_log_channel,
    set_warning_log_channel,
    has_dashboard_access,
    has_carry_manager_access,
    has_carry_application_management_access,
    has_transcripts_access,
    has_remove_cooldown_access,
    has_analytics_access,
    is_admin,
    is_globally_blocked,
    get_globally_blocked_users,
    add_globally_blocked_user,
    remove_globally_blocked_user,
    create_unblock_terms_token,
    get_terms_unblock_token,
    consume_terms_unblock_token,
    get_active_terms_unblock_tokens,
    revoke_terms_unblock_token,
    get_offline_schedule,
    save_offline_schedule,
)
from utils.carry_applications import (
    ApplicationStateError,
    check_application_available,
    get_application,
    get_application_config,
    get_application_cooldown_end,
    get_application_page,
    manage_applications,
    reset_pending_application_notifications,
    save_application_config,
    submit_application,
)

from utils import blacklists, audit, pin_permissions, points
from utils.transcripts import render_preview_transcript, theme_saved_transcript
from utils.dashboard_settings import (
    ACCESS_LISTS, DELIVERY_EVENTS, LOG_ACTIONS, get_ticket_logging_config,
    remove_access_entries, remove_blocked_badges, save_categories_checked,
    save_ticket_logging_config, ticket_log_page,
)


# Environment & OAuth Setup
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
# Optional overrides. Left unset, every public URL (OAuth2 redirect URIs,
# transcript links, the site links in Discord embeds) is derived from the
# incoming request, so the same deployment works on any domain - localhost, a
# preview host or a custom domain - without touching the config.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
REDIRECT_URI = os.getenv("OAUTH2_REDIRECT_URI", "").strip()
LINKED_ROLE_REDIRECT_URI = os.getenv("LINKED_ROLE_REDIRECT_URI", "").strip()
# Optional Discord role granted after a member accepts the Carry Service System
# Rules. Set this to the role ID in your hosting environment.
CARRY_RULES_ROLE_ID = os.getenv("CARRY_RULES_ROLE_ID", "").strip()

AUTHORIZATION_BASE_URL = 'https://discord.com/api/oauth2/authorize'
TOKEN_URL = 'https://discord.com/api/oauth2/token'

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

# Flask App
app = Flask(__name__)
from utils.analytics import duration as analytics_duration
app.jinja_env.filters["duration"] = analytics_duration

app.secret_key = os.getenv("SECRET_KEY", "supersecretkey123")
# Hosts like Render terminate TLS in front of the app, so without this Flask
# builds http:// URLs for an https:// site and Discord rejects the redirect.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Discord profile Connections → Domain ownership verification.
# This must be publicly readable at the exact URL, with no login or HTML.
@app.get("/.well-known/discord")
def discord_domain_verification():
    return Response(
        "dh=8a09c229f65a33f53c04b2cf31a4e98f48d2f317",
        mimetype="text/plain",
    )



_BOT_STATUS_SECTION_HTML = """<section class="ctb-status-section" aria-label="Bot status and downtime">
  <div class="ctb-status-copy">
    <span class="ctb-status-dot __STATUS_CLASS__"></span>
    <div><strong>Bot Status</strong><span class="ctb-status-state">__STATUS_TEXT__</span></div>
  </div>
  <div class="ctb-status-countdown" data-api-block-until="__API_BLOCK_UNTIL__">__API_BLOCK_TEXT__</div>
  <a class="ctb-status-link" href="/downtime">Downtime &amp; Offline Schedule →</a>
</section>
<style>
.ctb-status-section{position:fixed;right:18px;bottom:18px;z-index:9999;display:flex;align-items:center;gap:16px;max-width:min(560px,calc(100vw - 36px));padding:12px 14px;border:1px solid #25273a;border-radius:12px;background:rgba(20,21,31,.96);box-shadow:0 12px 40px #0008;backdrop-filter:blur(12px);font:13px/1.35 Inter,system-ui,sans-serif;color:#eef0f7}
.ctb-status-copy{display:flex;align-items:center;gap:9px;min-width:110px}.ctb-status-copy strong{display:block}.ctb-status-state{display:block;color:#8d90a8;font-size:12px;margin-top:2px}.ctb-status-countdown{color:#ffb454;font-size:12px;font-weight:700;min-width:115px}.ctb-status-dot{width:9px;height:9px;border-radius:50%;display:inline-block;flex:0 0 9px}.ctb-online{background:#3fd68c;box-shadow:0 0 10px #3fd68c88}.ctb-offline{background:#f1556c;box-shadow:0 0 10px #f1556c66}.ctb-status-link{color:#ffc93c;text-decoration:none;font-weight:700;white-space:nowrap}.ctb-status-link:hover{text-decoration:underline}
@media(max-width:600px){.ctb-status-section{left:12px;right:12px;bottom:12px;max-width:none;justify-content:space-between;gap:10px}.ctb-status-link{white-space:normal;text-align:right}}
</style>
<script>
(function(){
  const box=document.querySelector(".ctb-status-countdown");
  if(!box) return;
  const until=Number(box.dataset.apiBlockUntil||0);
  if(!until) return;
  const fmt=s=>{s=Math.max(0,Math.floor(s));const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),x=s%60;return h?String(h).padStart(2,"0")+":"+String(m).padStart(2,"0")+":"+String(x).padStart(2,"0"):String(m).padStart(2,"0")+":"+String(x).padStart(2,"0");};
  const tick=()=>{
    const left=Math.max(0,until-Math.floor(Date.now()/1000));
    if(left>0){box.textContent="Discord API blocked — retrying in "+fmt(left);}
    else{box.textContent="Retry window reached — checking Discord…"; location.reload();}
  };
  tick(); setInterval(tick,1000);
})();
</script>"""

# The old fixed Bot Status overlay was retired. The live status, incidents,
# offline schedule and rate-limit information now belong to /status and
# /downtime rather than being injected into every page.


def current_base_url():
    """Public origin of the current request, honouring PUBLIC_BASE_URL."""
    return PUBLIC_BASE_URL or request.url_root.rstrip("/")


def external_url(endpoint, **values):
    """url_for(..., _external=True) rooted at the public origin."""
    return f"{current_base_url()}{url_for(endpoint, **values)}"


def request_url():
    """The current request URL as the browser saw it. Used as the OAuth
    authorization_response, whose origin has to match the redirect_uri."""
    if not PUBLIC_BASE_URL:
        return request.url
    return f"{PUBLIC_BASE_URL}{request.full_path if request.query_string else request.path}"


@app.context_processor
def _inject_public_urls():
    return {"base_url": current_base_url(), "external_url": external_url}


@app.before_request
def _remember_public_origin():
    # The bot side (embeds, transcript links) has no request context, so it
    # reads back the origin remembered here.
    remember_base_url(current_base_url())

# Discord Bot
def _privileged_intent_enabled(name: str) -> bool:
    """Opt-in only after the matching intent is enabled in Discord's portal."""
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


intents = discord.Intents.default()
# These privileged intents default OFF for production and for applications
# that have not been granted permission in the Discord Developer Portal.
intents.members = _privileged_intent_enabled("DISCORD_MEMBERS_INTENT")
intents.message_content = _privileged_intent_enabled("DISCORD_MESSAGE_CONTENT_INTENT")
class GlobalCommandTree(discord.app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_globally_blocked(interaction.user.id):
            await interaction.response.send_message(
                "⛔ Your account is blocked from using this bot.",
                ephemeral=True,
            )
            return False
        return True


class DiagnosticBot(commands.Bot):
    """Bot subclass with startup timeout and Discord 429 diagnostics."""
    async def login(self, token: str) -> None:
        print("🔧 Discord HTTP login starting.", flush=True)
        print("🔧 Starting Discord HTTP stage probe.", flush=True)
        try:
            await asyncio.wait_for(super().login(token), timeout=60)
        except asyncio.TimeoutError:
            print("❌ Discord HTTP login timed out after 60 seconds.", flush=True)
            raise
        except discord.HTTPException as exc:
            retry_after = _retry_after_from(exc)
            if getattr(exc, "status", None) == 429:
                print(
                    f"⏸️ Discord login was rate-limited (HTTP 429); "
                    f"Retry-After={retry_after}s.",
                    flush=True,
                )
            raise
        print("🔧 Discord HTTP login finished; continuing to gateway/setup.", flush=True)


# discord.py exposes aiohttp's TraceConfig for tracing the HTTP requests it
# makes internally. This lets us see exactly where startup stalls without
# making an extra Discord API request or logging the bot token.
async def _discord_http_request_start(session, trace_config_ctx, params):
    trace_config_ctx.start_time = time.monotonic()
    print(
        f"🔎 Discord HTTP request started: {params.method} {params.url}",
        flush=True,
    )


async def _discord_http_request_end(session, trace_config_ctx, params):
    global api_block_until, api_block_reason, web_lockdown_until
    elapsed = time.monotonic() - getattr(trace_config_ctx, "start_time", time.monotonic())
    response = params.response
    status = getattr(response, "status", 0)
    if status == 429:
        headers = response.headers
        try:
            retry_after = float(headers.get("Retry-After", "0") or 0)
        except (TypeError, ValueError):
            retry_after = 0
        scope = headers.get("X-RateLimit-Scope", "unknown")
        global_limit = headers.get("X-RateLimit-Global", "unknown")
        cf_ray = headers.get("CF-Ray", "not-present")
        content_type = headers.get("Content-Type", "unknown")
        if retry_after > 0:
            # Record the server-provided wait. The watchdog must not restart
            # the process while Discord/Cloudflare is still asking us to wait.
            api_block_until = max(api_block_until, time.time() + retry_after)
            api_block_reason = "Discord REST API returned HTTP 429"
            web_lockdown_until = max(web_lockdown_until, api_block_until)
        print(
            "🛡️ Discord API Guard observed HTTP 429: "
            f"Retry-After={retry_after:g}s, scope={scope}, "
            f"global={global_limit}, CF-Ray={cf_ray}, "
            f"Content-Type={content_type}.",
            flush=True,
        )
        # The response body can distinguish a normal route/global limit from
        # an upstream IP restriction. aiohttp caches text() after reading it.
        try:
            body = (await response.text())[:500].replace("\\n", " ")
            if body:
                print(f"🛡️ Discord 429 response body (truncated): {body}", flush=True)
        except Exception as body_error:
            print(f"🛡️ Could not read 429 response body: {body_error!r}", flush=True)
        if retry_after > 0:
            print(
                f"⏸️ API Guard cooldown recorded for {retry_after:.0f}s; "
                "dashboard lockdown countdown updated.",
                flush=True,
            )
    print(
        f"🔎 Discord HTTP request finished: {params.method} {params.url} "
        f"-> HTTP {status} in {elapsed:.2f}s",
        flush=True,
    )


async def _discord_http_request_exception(session, trace_config_ctx, params):
    elapsed = time.monotonic() - getattr(trace_config_ctx, "start_time", time.monotonic())
    print(
        f"❌ Discord HTTP request failed: {params.method} {params.url} "
        f"after {elapsed:.2f}s: {params.exception!r}",
        flush=True,
    )


discord_http_trace = aiohttp.TraceConfig()
discord_http_trace.on_request_start.append(_discord_http_request_start)
discord_http_trace.on_request_end.append(_discord_http_request_end)
discord_http_trace.on_request_exception.append(_discord_http_request_exception)


bot = DiagnosticBot(
    command_prefix="!",
    intents=intents,
    tree_cls=GlobalCommandTree,
    http_trace=discord_http_trace,
)

# Tracks when the bot last became ready, used by the public /status page and
# /api/status JSON endpoint below. None means it hasn't connected yet since
# this process started.
bot_ready_since = None

# --- Discord API block cooldown -------------------------------------------
# If the bot's Discord login is rejected (typically a temporary 429 / Cloudflare
# block on the host's IP), the old behaviour was: bot.run() raised, the whole
# process exited, the host restarted it instantly, and every restart hit
# Discord's API again - which is what kept the block alive. Now the process
# stays up during a cooldown, the dashboard (which also calls Discord's API
# for login/guild lookups) is switched off, and only the status pages stay
# reachable so uptime keeps being recorded and shown.
web_lockdown_until = 0.0
last_start_error = None
# Discord can temporarily block the host from REST API access while the
# gateway remains connected. Keep a separate timestamp so public status pages
# can show the exact Retry-After countdown without guessing.
api_block_until = 0.0
api_block_reason = None

# --- Idle sleep -----------------------------------------------------------
# Optional (IDLE_SLEEP_HOURS env var, off by default). After that many hours
# with no bot interaction and no open tickets, the bot disconnects from Discord
# but the web/status pages stay up. It can be woken from the status page.
bot_sleeping = False
last_panel_activity = None   # timestamp of the last interaction with the bot
_waking = False

# Status pages plus the public info pages linked from the site header. None of
# these call Discord's API (they render static templates / a local file), so
# they're safe to keep serving while the dashboard is switched off.
LOCKDOWN_ALLOWED_PATHS = {
    "/status", "/downtime", "/api/status", "/api/status-history",
    "/docs", "/rules", "/guidelines", "/privacy", "/terms",
}

_LOCKDOWN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard temporarily unavailable</title>
<style>
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0b0b0f;color:#e8e8ee;font-family:system-ui,-apple-system,Segoe UI,sans-serif}
.card{max-width:460px;margin:24px;padding:32px;border:1px solid #2a2a35;border-radius:12px;background:#14141b;text-align:center}
h1{font-size:1.3rem;margin:0 0 12px}p{color:#a5a5b5;line-height:1.5;margin:0 0 16px}
a{color:#7c8cff;text-decoration:none;font-weight:600}
</style></head><body><div class="card">
<h1>&#128736;&#65039; Dashboard temporarily unavailable</h1>
<p>The bot can't reach Discord right now, so the dashboard is switched off
while it waits to reconnect. This page will work again automatically.</p>
<p>Retrying in about <strong>__MINUTES__ min</strong>.</p>
<a href="/status">View live status &rarr;</a>
</div></body></html>"""


def api_block_seconds_left():
    return max(0, int(api_block_until - time.time()))


def api_block_until_ts():
    return max(0.0, api_block_until)


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def web_lockdown_seconds_left():
    return max(0, int(max(web_lockdown_until, api_block_until) - time.time()))


@app.before_request
def _lockdown_gate():
    left = web_lockdown_seconds_left()
    if left <= 0:
        return None
    path = request.path
    if path in LOCKDOWN_ALLOWED_PATHS or path.startswith("/static/"):
        return None
    # Hosting health checks / uptime pingers hit "/" - keep answering 200 so
    # the host doesn't decide the service is dead and restart it (which would
    # just re-trigger the Discord block).
    if path == "/":
        if request.method == "HEAD":
            return Response("", status=200)
        return render_template("status.html")
    retry_after = str(left)
    if path.startswith("/api/"):
        resp = jsonify({"error": "Dashboard temporarily unavailable", "retry_in_seconds": left})
        resp.status_code = 503
        resp.headers["Retry-After"] = retry_after
        return resp
    html = _LOCKDOWN_HTML.replace("__MINUTES__", str(max(1, round(left / 60))))
    return Response(html, status=503, headers={"Retry-After": retry_after, "Content-Type": "text/html; charset=utf-8"})



def make_oauth_session(state=None):
    return OAuth2Session(
        client_id=CLIENT_ID,
        state=state,
        scope=['identify', 'guilds', 'guilds.members.read'],
        redirect_uri=REDIRECT_URI or external_url("callback")
    )


def make_linked_role_oauth_session(state=None):
    """Separate OAuth2 session for the Linked Roles verification flow —
    different scope (role_connections.write) and its own redirect URI, so
    it doesn't collide with the dashboard login session above."""
    return OAuth2Session(
        client_id=CLIENT_ID,
        state=state,
        scope=['identify', 'role_connections.write'],
        redirect_uri=LINKED_ROLE_REDIRECT_URI or external_url("linked_role_callback")
    )


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user"):
            flash("🔒 Please log in with Discord to access the dashboard.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


def get_member_role_ids(user_id):
    """Look up a logged-in user's role IDs in the bot's guild, used to check
    access-control role matches. Returns [] if the guild/member isn't found.

    Tries the local member cache first (instant, no API call), and falls
    back to a direct REST fetch if the member isn't cached — this matters
    because the Members privileged intent is currently disabled (see the
    NOTE above), so the cache is mostly empty and get_member() alone would
    silently return [] for anyone the bot hasn't recently seen a gateway
    event for, even though they do have the role. fetch_member() is a plain
    REST call and works fine without that intent.
    """
    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        return []
    member = guild.get_member(int(user_id))
    if not member:
        try:
            future = asyncio.run_coroutine_threadsafe(
                guild.fetch_member(int(user_id)), bot.loop
            )
            member = future.result(timeout=10)
        except Exception:
            member = None
    if not member:
        return []
    return [str(r.id) for r in member.roles]


# Small persistent cache so repeated dashboard loads don't re-fetch the same
# users' names over and over — usernames rarely change, and this avoids
# piling up several blocking REST calls (one per unresolved user) on every
# single page load.
_username_cache = {}


def resolve_username(guild, user_id, cached_only=False):
    """Best-effort username lookup for display purposes (e.g. showing a name
    next to a raw user ID on the dashboard). Returns None if it can't be
    resolved — callers should fall back to showing the raw ID.

    Uses a short 3s timeout on the REST fallback (unlike
    get_member_role_ids's 10s) since this may run several times in a row
    while rendering one page, and a slow/failed lookup shouldn't stall the
    whole dashboard load.
    """
    if not guild or not user_id:
        return None
    key = str(user_id)
    if key in _username_cache:
        return _username_cache[key]

    try:
        member = guild.get_member(int(user_id))
    except (TypeError, ValueError):
        return None

    if not member and not cached_only:
        try:
            future = asyncio.run_coroutine_threadsafe(
                guild.fetch_member(int(user_id)), bot.loop
            )
            member = future.result(timeout=3)
        except Exception:
            member = None

    name = str(member) if member else None
    # A cache-only lookup must not permanently cache a missing member and
    # prevent another part of the bot from performing a real lookup later.
    if name is not None or not cached_only:
        _username_cache[key] = name
    return name


def access_required(f):
    """Like login_required, but also enforces the Access Control allow-list."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_data = session.get("user")
        if not user_data:
            flash("🔒 Please log in with Discord to access the dashboard.", "warning")
            return redirect(url_for("login"))
        if is_globally_blocked(user_data["id"]):
            flash("⛔ Your account is blocked from using this dashboard.", "danger")
            session.pop("user", None)
            return redirect(url_for("home"))
        role_ids = get_member_role_ids(user_data["id"])
        if not has_dashboard_access(user_data["id"], role_ids):
            flash("⛔ You don't have permission to view this dashboard.", "danger")
            session.pop("user", None)
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return decorated_function


def carry_manager_required(f):
    """Like access_required, but also requires Carry Manager Settings access
    (either an admin, or a matching role from carry_manager_roles)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_data = session.get("user")
        if not user_data:
            flash("🔒 Please log in with Discord to access the dashboard.", "warning")
            return redirect(url_for("login"))
        if is_globally_blocked(user_data["id"]):
            flash("⛔ Your account is blocked from using this dashboard.", "danger")
            session.pop("user", None)
            return redirect(url_for("home"))
        role_ids = get_member_role_ids(user_data["id"])
        if not has_dashboard_access(user_data["id"], role_ids):
            flash("⛔ You don't have permission to view this dashboard.", "danger")
            session.pop("user", None)
            return redirect(url_for("home"))
        if not has_carry_manager_access(user_data["id"], role_ids):
            flash("⛔ You don't have permission to use Carry Manager Settings.", "danger")
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return decorated_function


def carry_application_manager_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = session.get("user")
        if not user:
            return redirect(url_for("login"))
        if is_globally_blocked(user["id"]):
            abort(403)
        roles = [] if is_admin(user["id"]) else get_member_role_ids(user["id"])
        if not has_dashboard_access(user["id"], roles) or not has_carry_application_management_access(user["id"], roles):
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def remove_cooldown_required(f):
    """Like access_required, but also requires Remove Cooldown access
    (either an admin, or a matching role from remove_cooldown_roles)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_data = session.get("user")
        if not user_data:
            flash("🔒 Please log in with Discord to access the dashboard.", "warning")
            return redirect(url_for("login"))
        role_ids = get_member_role_ids(user_data["id"])
        if not has_dashboard_access(user_data["id"], role_ids):
            flash("⛔ You don't have permission to view this dashboard.", "danger")
            session.pop("user", None)
            return redirect(url_for("home"))
        if not has_remove_cooldown_access(user_data["id"], role_ids):
            flash("⛔ You don't have permission to remove cooldowns.", "danger")
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Restricts a route to admins only (see utils.access.is_admin) — used
    for the Access Control page and its management routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_data = session.get("user")
        if not user_data:
            flash("🔒 Please log in with Discord to access the dashboard.", "warning")
            return redirect(url_for("login"))
        if is_globally_blocked(user_data["id"]):
            flash("⛔ Your account is blocked from using this dashboard.", "danger")
            session.pop("user", None)
            return redirect(url_for("home"))
        role_ids = get_member_role_ids(user_data["id"])
        if not has_dashboard_access(user_data["id"], role_ids):
            flash("⛔ You don't have permission to view this dashboard.", "danger")
            session.pop("user", None)
            return redirect(url_for("home"))
        if not is_admin(user_data["id"]):
            flash("⛔ Access Control is restricted to admins only.", "danger")
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return decorated_function


# --- FLASK ROUTES ---

def _carry_application_csrf_token():
    token = session.get("carry_application_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["carry_application_csrf"] = token
    return token


def _valid_carry_application_csrf():
    expected = session.get("carry_application_csrf", "")
    submitted = request.form.get("csrf_token", "")
    return bool(expected and submitted and hmac.compare_digest(expected, submitted))


def _carry_application_member_status(user_id):
    guild = bot.guilds[0] if bot.guilds else None
    if guild is None or not bot.is_ready() or bot.loop.is_closed():
        return None
    member = guild.get_member(int(user_id))
    if member is not None:
        return True
    try:
        future = asyncio.run_coroutine_threadsafe(
            guild.fetch_member(int(user_id)), bot.loop
        )
        future.result(timeout=10)
        return True
    except discord.NotFound:
        return False
    except Exception:
        app.logger.exception(
            "Could not verify guild membership for carry applicant=%s", user_id
        )
        return None


def _carry_application_user_or_redirect():
    user = session.get("user")
    if not user:
        session["application_return_to"] = url_for("carry_application")
        return None, redirect(url_for("login"))
    if is_globally_blocked(user.get("id")):
        return None, ("This Discord account cannot use the application portal.", 403)
    member_status = _carry_application_member_status(user.get("id"))
    if member_status is False:
        return None, ("You must be a member of the Carry Service Discord server to apply.", 403)
    if member_status is None:
        return None, ("The Discord bot is not connected to the server. Please try again when it is online.", 503)
    return user, None


def _carry_application_state_response(user, error, status_code=200):
    now = time.time()
    deadline = error.available_at.timestamp() if error.available_at else None
    return render_template(
        "carry_application_status.html", user=user, state=error.code,
        application=error.application, accepting_responses=error.accepting_responses,
        available_at=error.available_at, available_at_ts=deadline,
        server_now=now, remaining_seconds=max(0, int((deadline or now) - now + 0.999)),
    ), status_code


def _carry_application_gate(user):
    try:
        check_application_available(user["id"])
    except ApplicationStateError as error:
        code = 200 if request.method == "GET" else (429 if error.code == "cooldown" else 409)
        return _carry_application_state_response(user, error, code)
    except Exception:
        app.logger.exception("Could not check Carry application availability")
        return "Application records are temporarily unavailable. Please try again later.", 503
    return None


@app.route("/carry-application")
def carry_application():
    user, response = _carry_application_user_or_redirect()
    if response is not None:
        return response
    response = _carry_application_gate(user)
    if response is not None:
        return response
    if session.get("carry_application_rules_user") != str(user["id"]):
        session.pop("carry_application_rules_user", None)
        session.pop("carry_application_rules_accepted_at", None)
        return render_template(
            "carry_application_rules.html",
            user=user,
            csrf_token=_carry_application_csrf_token(),
        )
    return redirect(url_for("carry_application_form"))


@app.route("/carry-application/rules", methods=["POST"])
def carry_application_accept_rules():
    user, response = _carry_application_user_or_redirect()
    if response is not None:
        return response
    response = _carry_application_gate(user)
    if response is not None:
        return response
    if not _valid_carry_application_csrf():
        return "Your form expired. Reload the application page and try again.", 400
    if request.form.get("agree") != "yes":
        return render_template(
            "carry_application_rules.html",
            user=user,
            csrf_token=_carry_application_csrf_token(),
            error="You must agree to the application rules before continuing.",
        ), 400
    session["carry_application_rules_user"] = str(user["id"])
    session["carry_application_rules_accepted_at"] = datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()
    return redirect(url_for("carry_application_form"))


@app.route("/carry-application/form")
def carry_application_form():
    user, response = _carry_application_user_or_redirect()
    if response is not None:
        return response
    response = _carry_application_gate(user)
    if response is not None:
        return response
    if session.get("carry_application_rules_user") != str(user["id"]):
        return redirect(url_for("carry_application"))
    return render_template(
        "carry_application_form.html",
        user=user,
        answers={},
        csrf_token=_carry_application_csrf_token(),
    )


@app.route("/carry-application/submit", methods=["POST"])
def carry_application_submit():
    user, response = _carry_application_user_or_redirect()
    if response is not None:
        return response
    response = _carry_application_gate(user)
    if response is not None:
        return response
    if session.get("carry_application_rules_user") != str(user["id"]):
        return redirect(url_for("carry_application"))
    if not _valid_carry_application_csrf():
        return "Your form expired. Reload the application page and try again.", 400
    if request.form.get("final_agreement") != "yes":
        return render_template(
            "carry_application_form.html",
            user=user,
            answers=request.form,
            csrf_token=_carry_application_csrf_token(),
            error="Please check the agreement box before submitting.",
        ), 400

    answers = {
        "age_range": request.form.get("age_range", "").strip(),
        "timezone": request.form.get("timezone", "").strip(),
        "roblox_username": request.form.get("roblox_username", "").strip(),
        "tds_level": request.form.get("tds_level", "").strip(),
        "motivation": request.form.get("motivation", "").strip(),
        "daily_activity": request.form.get("daily_activity", "").strip(),
        "understands_terms": request.form.get("understands_terms", "").strip(),
        "strategy_rating": request.form.get("strategy_rating", "").strip(),
    }
    valid_age_ranges = {"13-15", "16-17", "18+"}
    if answers["age_range"] not in valid_age_ranges:
        error = "Choose one of the listed age ranges."
    elif answers["understands_terms"] not in {"Yes", "No"}:
        error = "Answer the Roblox Terms of Service question."
    elif not re.fullmatch(r"(?:[1-9]|10)", answers["strategy_rating"]):
        error = "Choose a strategy rating from 1 to 10."
    elif any(not value for key, value in answers.items() if key != "strategy_rating"):
        error = "Please answer every required question."
    elif len(answers["timezone"]) > 120 or len(answers["roblox_username"]) > 40 or len(answers["tds_level"]) > 40:
        error = "One or more short answers are too long."
    elif len(answers["motivation"]) > 2000 or len(answers["daily_activity"]) > 1200:
        error = "One or more written answers are too long."
    else:
        error = None
    if error:
        return render_template(
            "carry_application_form.html",
            user=user,
            answers=request.form,
            csrf_token=_carry_application_csrf_token(),
            error=error,
        ), 400

    try:
        application = submit_application(
            user["id"],
            user.get("username"),
            answers,
            session.get("carry_application_rules_accepted_at"),
        )
    except ApplicationStateError as error:
        return _carry_application_state_response(user, error, 429 if error.code == "cooldown" else 409)
    except Exception:
        app.logger.exception("Could not save carry application user=%s", user["id"])
        return "We couldn't save your application because storage is temporarily unavailable. Please try again later.", 503

    session.pop("carry_application_rules_user", None)
    session.pop("carry_application_rules_accepted_at", None)
    cog = bot.get_cog("CarryApplicationsCog")
    if cog and bot.is_ready():
        try:
            asyncio.run_coroutine_threadsafe(
                cog.process_application_notice(application["application_id"]),
                bot.loop,
            )
        except RuntimeError:
            app.logger.warning(
                "Application %s was saved; staff notification will retry when the bot reconnects.",
                application["application_id"],
            )
    return render_template("carry_application_submitted.html"), 201


@app.route("/carry-application/review/<application_id>")
def carry_application_review(application_id):
    if not re.fullmatch(r"[a-f0-9]{32}", application_id):
        return "Application not found.", 404
    user = session.get("user")
    if not user:
        session["application_return_to"] = url_for(
            "carry_application_review", application_id=application_id
        )
        return redirect(url_for("login"))
    if is_globally_blocked(user.get("id")):
        return "This Discord account cannot review Carry Team applications.", 403
    role_ids = get_member_role_ids(user.get("id"))
    manager_access = (has_dashboard_access(user.get("id"), role_ids)
                      and has_carry_application_management_access(user.get("id"), role_ids))
    if not (has_carry_application_verdict_access(user.get("id"), role_ids) or manager_access):
        return "You do not have permission to review Carry Team applications.", 403
    try:
        application = get_application(application_id)
    except Exception:
        app.logger.exception(
            "Could not load Carry application=%s for review", application_id
        )
        return "Application records are temporarily unavailable. Please try again later.", 503
    if not application:
        return "Application not found.", 404
    return render_template(
        "carry_application_review.html",
        application=application,
        answer_labels=(
            ("age_range", "Age range"),
            ("timezone", "Country and time zone"),
            ("roblox_username", "Roblox username"),
            ("tds_level", "Tower Defense Simulator level"),
            ("motivation", "Why they want to join"),
            ("daily_activity", "Daily activity"),
            ("understands_terms", "Understands the no-rewards policy"),
            ("strategy_rating", "Game strategy experience (1-10)"),
        ),
    )


@app.route("/carry-agreement")
def carry_agreement():
    try:
        agreement = None
        if session.get("user"):
            agreement = get_carry_rules_agreement(session["user"].get("id"))
        return render_template("carry_agreement.html", user=session.get("user"), agreement=agreement)
    except Exception:
        app.logger.exception("carry_agreement page crashed")
        return (
            "<h1>Something went wrong loading this page</h1>"
            "<p>Please try again shortly. If this keeps happening, contact the server admin.</p>"
            "<a href='/'>Back to home</a>",
            500,
        )


@app.route("/terms")
def terms_page():
    terms_path = Path(__file__).resolve().parent.parent / "docs" / "terms_and_conditions.txt"
    return render_template(
        "terms.html",
        user=session.get("user"),
        terms_content=terms_path.read_text(encoding="utf-8"),
    )


@app.route("/terms/unblock/<token>")
def terms_unblock(token):
    token_data = get_terms_unblock_token(token)
    if not token_data:
        return "This terms link is invalid, expired, or already used.", 410

    user = session.get("user")
    if not user:
        session["terms_unblock_token"] = token
        terms_path = Path(__file__).resolve().parent.parent / "docs" / "terms_and_conditions.txt"
        return render_template(
            "terms_unblock.html",
            user=None,
            terms_token=token,
            terms_content=terms_path.read_text(encoding="utf-8"),
        )
    if str(user.get("id")) != str(token_data["user_id"]):
        return (
            render_template("terms_account_mismatch.html", token=token),
            403,
        )
    terms_path = Path(__file__).resolve().parent.parent / "docs" / "terms_and_conditions.txt"
    terms_content = terms_path.read_text(encoding="utf-8")
    return render_template(
        "terms_unblock.html",
        user=user,
        terms_token=token,
        terms_content=terms_content,
    )


@app.route("/terms/unblock/<token>/switch-account")
def terms_unblock_switch_account(token):
    if not get_terms_unblock_token(token):
        return "This terms link is invalid, expired, or already used.", 410
    session.clear()
    session["terms_unblock_token"] = token
    return redirect(url_for("login"))


@app.route("/terms/unblock/<token>/accept", methods=["POST"])
@login_required
def accept_terms_unblock(token):
    token_data = get_terms_unblock_token(token)
    user = session.get("user") or {}
    if not token_data or str(user.get("id")) != str(token_data["user_id"]):
        return "This terms link is invalid or does not belong to this Discord account.", 403
    if not consume_terms_unblock_token(token, user["id"]):
        return "This terms link is invalid, expired, or already used.", 410
    if not remove_globally_blocked_user(user["id"]):
        return "This account is not currently globally blocked.", 409
    audit.emit_event(bot, "user_unblocked", user["id"], user_id=str(user["id"]), source="Terms accepted via unblock link")
    flash("✅ Terms accepted. Your global block has been removed.", "success")
    return redirect(url_for("home"))

@app.route("/carry-agreement/accept", methods=["POST"])
@login_required
def accept_carry_agreement():
    try:
        user = session.get("user") or {}
        user_id = str(user.get("id") or "")
        if not user_id:
            flash("Unable to identify your Discord account. Please log in again.", "danger")
            return redirect(url_for("carry_agreement"))

        if not CARRY_RULES_ROLE_ID.isdigit():
            flash("The Carry Rules role has not been configured yet.", "danger")
            return redirect(url_for("carry_agreement"))

        guild = bot.guilds[0] if bot.guilds else None
        if not guild:
            flash("The Discord bot is not connected to the server right now.", "danger")
            return redirect(url_for("carry_agreement"))

        member = guild.get_member(int(user_id))
        if member is None:
            future = asyncio.run_coroutine_threadsafe(guild.fetch_member(int(user_id)), bot.loop)
            member = future.result(timeout=10)

        role = guild.get_role(int(CARRY_RULES_ROLE_ID))
        if role is None:
            flash("The configured Carry Rules role could not be found.", "danger")
            return redirect(url_for("carry_agreement"))

        if role >= guild.me.top_role:
            flash("The bot cannot assign that role. Move the role below the bot's highest role.", "danger")
            return redirect(url_for("carry_agreement"))

        if role not in member.roles:
            future = asyncio.run_coroutine_threadsafe(member.add_roles(role, reason="Accepted Carry Service System Rules"), bot.loop)
            future.result(timeout=10)

        save_carry_rules_agreement(user_id, user.get("username"))
        flash("✅ Rules accepted. Your Carry Rules role has been granted.", "success")
    except Exception:
        app.logger.exception("Carry rules role assignment failed")
        flash("Something went wrong while assigning your Carry Rules role.", "danger")

    return redirect(url_for("carry_agreement"))


@app.route("/linked-role")
def linked_role_start():
    """The verification URL you paste into the Developer Portal's Linked
    Roles Verification URL field. Starts the OAuth2 flow — this is what
    makes the connect button send people through Discord's own OAuth
    screen, which is what gets this bot listed under Connections -> Apps.
    """
    try:
        if not CLIENT_ID or not CLIENT_SECRET:
            app.logger.error(
                "linked_role_start: DISCORD_CLIENT_ID or DISCORD_CLIENT_SECRET is missing"
            )
            return render_template(
                "linked_role.html", stage="error",
                error_message="This bot isn't configured for verification yet. Please contact the server admin."
            )
        discord_sess = make_linked_role_oauth_session()
        authorization_url, state = discord_sess.authorization_url(AUTHORIZATION_BASE_URL)
        session['linked_role_oauth_state'] = state
        return render_template("linked_role.html", stage="start", discord_auth_url=authorization_url)
    except Exception:
        app.logger.exception("linked_role_start crashed")
        return render_template(
            "linked_role.html", stage="error",
            error_message="Something went wrong starting verification. Please try again shortly."
        )


@app.route("/linked-role/callback")
def linked_role_callback():
    if request.args.get('error'):
        return render_template(
            "linked_role.html",
            stage="error",
            error_message=request.args['error']
        )

    try:
        discord_sess = make_linked_role_oauth_session(
            state=session.get('linked_role_oauth_state')
        )

        token = discord_sess.fetch_token(
            TOKEN_URL,
            client_secret=CLIENT_SECRET,
            authorization_response=request_url()
        )

        session['linked_role_access_token'] = token['access_token']

        user_data = discord_sess.get(
            'https://discord.com/api/users/@me'
        ).json()

        session['linked_role_username'] = user_data.get('username')

    except Exception:
        app.logger.exception("Linked role OAuth callback failed")
        return render_template(
            "linked_role.html",
            stage="error"
        )

    return render_template(
        "linked_role.html",
        stage="agree",
        username=session.get('linked_role_username')
    )
    
@app.route("/linked-role/agree", methods=["POST"])
def linked_role_agree():
    access_token = session.get('linked_role_access_token')
    if not access_token:
        return render_template("linked_role.html", stage="error", error_message="Your session expired — please start over.")

    if request.form.get("agree") != "on":
        return render_template("linked_role.html", stage="agree", username=session.get('linked_role_username'))

    try:
        from utils.linked_roles import push_role_connection
        push_role_connection(access_token, agreed=True)
    except Exception:
        app.logger.exception("Failed to push linked role connection metadata")
        return render_template("linked_role.html", stage="error", error_message="Discord rejected the verification. Please try again.")
    finally:
        session.pop('linked_role_access_token', None)

    return render_template("linked_role.html", stage="done")


@app.route("/dashboard/linked-role/register-metadata", methods=["POST"])
@admin_required
def register_linked_role_metadata():
    try:
        from utils.linked_roles import register_metadata
        register_metadata(CLIENT_ID, TOKEN)
        flash("✅ Linked Roles metadata registered. You can now add the 'Agreed to Rules' requirement to a role in Server Settings → Roles → Links.", "success")
    except Exception as e:
        app.logger.exception("Failed to register linked role metadata")
        flash(f"❌ Failed to register metadata: {e}", "danger")
    return redirect(url_for("home"))


@app.route("/privacy")
def privacy_policy():
    return render_template("privacy.html")


@app.route("/docs")
def docs():
    return render_template("docs.html", user=session.get("user"))


@app.route("/rules")
def rules():
    return render_template("rules.html")


@app.route("/guidelines")
def guidelines():
    return render_template("guidelines.html")


@app.route("/api/status")
def api_status():
    online = bool(bot.is_ready()) and not bot.is_closed()
    latency_ms = None
    if online:
        try:
            lat = bot.latency
            if lat is not None and lat == lat:  # filters out NaN
                latency_ms = round(lat * 1000)
        except Exception:
            latency_ms = None
    uptime_seconds = None
    if online and bot_ready_since:
        uptime_seconds = round(time.time() - bot_ready_since)
    guild_count = len(bot.guilds) if online else 0
    api_left = api_block_seconds_left()
    return jsonify({
        "online": online and api_left <= 0,
        "api_blocked": api_left > 0,
        "api_block_reason": api_block_reason if api_left > 0 else None,
        "api_retry_in_seconds": api_left or None,
        "api_retry_at": api_block_until_ts() or None,
        "latency_ms": latency_ms,
        "uptime_seconds": uptime_seconds,
        "guild_count": guild_count,
        "web_disabled": web_lockdown_seconds_left() > 0,
        "retry_in_seconds": web_lockdown_seconds_left() or None,
        "sleeping": bot_sleeping,
    })


@app.route("/api/wake", methods=["POST"])
def api_wake():
    """Wake the bot from idle sleep. Public on purpose (a customer who finds
    the bot asleep can wake it from the status page). It's a no-op unless the
    bot is actually sleeping, and only one wake can be in flight, so it can't
    be used to trigger repeated Discord logins."""
    global _waking
    if not bot_sleeping:
        return jsonify({"ok": True, "already_awake": True})
    if _waking:
        return jsonify({"ok": True, "waking": True})
    _waking = True
    import threading as _th
    _th.Timer(1.5, _wake_process).start()  # let this response go out first
    return jsonify({"ok": True, "waking": True})


def _wake_process():
    import sys
    print("☀️ Waking bot from idle sleep - restarting process to log in again...")
    _send_alert("☀️ Carry Ticket Bot is waking up from idle sleep.")
    os.environ["BOT_START_ATTEMPT"] = "0"
    os.execv(sys.executable, [sys.executable] + sys.argv)


@app.route("/api/status-history")
def api_status_history():
    from utils.status_history import get_daily_uptime, get_overall_uptime_pct, get_incidents_by_month
    return jsonify({
        "daily": get_daily_uptime(90),
        "overall_uptime_pct": get_overall_uptime_pct(90),
        "months": get_incidents_by_month(3),
    })


def _public_schedule_context():
    """Shared status-page context, reading the configured schedule once."""
    schedule = get_offline_schedule()
    try:
        schedule_timezone = schedule.get("timezone") or "America/New_York"
        ZoneInfo(schedule_timezone)
    except Exception:
        schedule_timezone = "America/New_York"
    day_names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    schedule_days = [
        {"name": day, "entries": schedule.get("days", {}).get(day, []) or []}
        for day in day_names
    ]
    schedule_active = _offline_schedule_is_active(schedule=schedule)
    api_retry_in_seconds = api_block_seconds_left()
    return {
        "schedule_days": schedule_days,
        "schedule_timezone": schedule_timezone,
        "schedule_enabled": bool(schedule.get("enabled", False)),
        "schedule_active": schedule_active,
        "bot_active": bool(bot.is_ready()) and not bot.is_closed(),
        "api_blocked": api_retry_in_seconds > 0,
        "api_retry_in_seconds": api_retry_in_seconds,
        "api_retry_at": api_block_until_ts(),
        "bot_sleeping": bot_sleeping,
    }


@app.route("/downtime")
def downtime_page():
    return render_template("downtime.html", **_public_schedule_context())


@app.route("/status")
def status_page():
    return render_template("status.html", base_url=current_base_url(), **_public_schedule_context())


DASHBOARD_PAGES = {"overview", "analytics", "transcripts", "ticket-logs", "blacklists", "warnings", "carry-settings", "carry-applications", "trial-schedules", "warning-builder", "access-control", "points"}


@app.route("/")
def landing():
    """Public product introduction; the existing panel lives at /dashboard."""
    return render_template("landing.html", user=session.get("user"))


@app.route("/dashboard")
@app.route("/dashboard/<page>")
def home(page="overview"):
    # Home is the public label; preserve /dashboard/overview bookmarks.
    if page == "home":
        page = "overview"
    if page not in DASHBOARD_PAGES:
        abort(404)
    user_data = session.get("user", None)
    if not user_data:
        if page not in ("overview", "transcripts"):
            return redirect(url_for("home", page="overview"))
        return render_template(
            "dashboard.html",
            active_page=page,
            transcripts_page=1,
            transcripts_total_pages=1,
            transcripts_total=1,
            user=None,
            preview_mode=True,
            guild_name="Carry Ticket Bot Demo",
            active_tickets=[
                {
                    "channel_id": "demo-ticket-1042",
                    "user_id": "123456789012345678",
                    "username": "ExampleUser",
                    "created_at": "2026-09-07T13:42:00+00:00",
                },
            ],
            total_tickets=1284,
            cooldowns=[
                {
                    "user_id": "987654321098765432",
                    "username": "SampleMember",
                    "expires_at": "2026-09-07 14:30 UTC",
                    "expires_ts": "2026-09-07T14:30:00+00:00",
                },
            ],
            can_remove_cooldown=False,
            can_access_analytics=False,
            can_access_transcripts=True,
            transcripts=[
                {
                    "filename": "preview-transcript.html",
                    "ticket_number": "1041",
                    "user_id": "123456789012345678",
                    "username": "ExampleUser",
                },
            ],
            is_admin_user=False,
            panel_draft={},
            redirect_message={"content": ""},
            welcome_message={},
        )

    if is_globally_blocked(user_data["id"]):
        flash("⛔ Your account is blocked from using this dashboard.", "danger")
        session.pop("user", None)
        return redirect(url_for("home"))

    # An ID-based admin requires no blocking Discord role fetch just to view the dashboard.
    role_ids = [] if is_admin(user_data["id"]) else get_member_role_ids(user_data["id"])
    if not has_dashboard_access(user_data["id"], role_ids):
        flash("⛔ You don't have permission to view this dashboard. Ask an admin to add your user ID or role in Access Control.", "danger")
        session.pop("user", None)
        return redirect(url_for("home"))

    guild = bot.guilds[0] if bot.guilds else None
    # Only populate channel and role lists on pages that actually render them.
    channels = guild.text_channels if guild and page in ("carry-settings", "trial-schedules", "access-control", "ticket-logs", "points") else []
    categories = guild.categories if guild and page == "carry-settings" else []
    roles = guild.roles if guild and page in ("carry-settings", "warning-builder", "access-control", "points") else []

    # Never read unrelated collections while opening a single dashboard page.
    tickets_info = get_tickets_data() if page == "overview" else {}
    blacklist_info = get_blacklist_data(include_expired=True) if page == "blacklists" else {}
    settings = get_settings() if page in ("carry-settings", "access-control") else {}
    warning_presets = get_premade_warning_reasons() if page == "warning-builder" else []
    warning_builder = get_warning_builder() if page == "warning-builder" else {}
    warning_records = get_all_warnings() if page == "warnings" else []
    generated_unblock_link = session.pop("generated_unblock_link", None) if page == "access-control" else None
    active_unblock_links = [
        {**entry, "url": external_url("terms_unblock", token=entry["token"])}
        for entry in get_active_terms_unblock_tokens()
    ] if page == "access-control" else []
    access_settings = get_access_settings() if page in ("access-control", "blacklists", "ticket-logs") else {}

    allowed_users = []
    for uid in access_settings.get("allowed_users", []):
        member = guild.get_member(int(uid)) if guild else None
        allowed_users.append({"id": uid, "name": str(member) if member else None})

    allowed_roles = []
    for rid in access_settings.get("allowed_roles", []):
        role = guild.get_role(int(rid)) if guild else None
        allowed_roles.append({"id": rid, "name": role.name if role else None})

    carry_manager_roles = []
    for rid in access_settings.get("carry_manager_roles", []):
        role = guild.get_role(int(rid)) if guild else None
        carry_manager_roles.append({"id": rid, "name": role.name if role else None})

    transcripts_roles = []
    for rid in access_settings.get("transcripts_roles", []):
        role = guild.get_role(int(rid)) if guild else None
        transcripts_roles.append({"id": rid, "name": role.name if role else None})

    remove_cooldown_roles = []
    for rid in access_settings.get("remove_cooldown_roles", []):
        role = guild.get_role(int(rid)) if guild else None
        remove_cooldown_roles.append({"id": rid, "name": role.name if role else None})

    analytics_roles = []
    for rid in access_settings.get("analytics_roles", []):
        role = guild.get_role(int(rid)) if guild else None
        analytics_roles.append({"id": rid, "name": role.name if role else None})

    pin_message_roles = []
    for rid in access_settings.get("pin_message_roles", []):
        role = guild.get_role(int(rid)) if guild else None
        pin_message_roles.append({"id": rid, "name": role.name if role else None})

    carry_application_verdict_roles = []
    for rid in access_settings.get("carry_application_verdict_roles", []):
        role = guild.get_role(int(rid)) if guild else None
        carry_application_verdict_roles.append(
            {"id": rid, "name": role.name if role else None}
        )
    delivery_settings = settings.get("carry_application_delivery") or {}
    carry_application_delivery = {
        "mode": delivery_settings.get("mode", "bot"),
        "channel_id": delivery_settings.get("channel_id"),
        "webhook_configured": bool(delivery_settings.get("webhook_url")),
    }

    is_admin_user = is_admin(user_data["id"])
    can_access_carry_settings = has_carry_manager_access(user_data["id"], role_ids)
    can_manage_carry_applications = has_carry_application_management_access(user_data["id"], role_ids)
    can_access_transcripts = has_transcripts_access(user_data["id"], role_ids)
    can_remove_cooldown = has_remove_cooldown_access(user_data["id"], role_ids)
    can_access_analytics = has_analytics_access(user_data["id"], role_ids)
    can_manage_points = has_carry_manager_access(user_data["id"], role_ids)
    points_config = points.default_points_config()
    points_config_unavailable = False
    points_leaderboard = {"items": [], "total": 0, "page": 1, "pages": 1, "per_page": points.PAGE_SIZE, "query": ""}
    points_activity = {"items": [], "total": 0, "page": 1, "pages": 1, "per_page": points.PAGE_SIZE, "query": "", "action": ""}
    if page == "access-control":
        try:
            points_config = points.get_points_config(guild.id if guild else "global")
        except Exception:
            # Keep existing Access Control usable during a Points-specific database outage.
            # The partial displays an explicit warning, and the save endpoint remains strict.
            points_config_unavailable = True
            app.logger.exception("Could not load Points configuration for Access Control")
    elif page == "points":
        try:
            points_config = points.get_points_config(guild.id if guild else "global")
            points_query = request.args.get("points_query", "").strip()[:100]
            points_log_query = request.args.get("points_log_query", "").strip()[:100]
            points_log_action = request.args.get("points_log_action", "").strip()
            points_leaderboard = points.get_points_leaderboard(
                guild.id if guild else "global",
                _application_page_number(request.args.get("points_page")),
                points_query,
            )
            points_activity = points.get_points_activity(
                guild.id if guild else "global",
                _application_page_number(request.args.get("points_log_page")),
                points_log_query,
                points_log_action,
            )
            for row in points_leaderboard["items"]:
                member = guild.get_member(int(row["user_id"])) if guild and str(row["user_id"]).isdigit() else None
                if member:
                    row["username"] = getattr(member, "display_name", str(member))
        except Exception:
            app.logger.exception("Could not load Carry Team Points leaderboard or audit history")
            return "Points data is temporarily unavailable. Please try again later.", 503

    # Pages are separate URLs: enforce the same restrictions server-side.
    if page == "carry-applications" and not can_manage_carry_applications:
        abort(403)
    if page in ("warnings", "warning-builder", "access-control") and not is_admin_user:
        abort(403)
    if page in ("carry-settings", "trial-schedules") and not (is_admin_user and can_access_carry_settings):
        abort(403)
    if page == "analytics" and not can_access_analytics:
        abort(403)
    if page == "transcripts" and not can_access_transcripts:
        abort(403)

    ticket_logging = get_ticket_logging_config(access_settings) if page in ("ticket-logs", "access-control") else None
    ticket_log_records = None
    ticket_log_query = request.args.get("log_query", "").strip()[:100]
    ticket_log_action = request.args.get("log_action", "")
    if page == "ticket-logs":
        try:
            ticket_log_records = ticket_log_page(_application_page_number(request.args.get("log_page")),
                                                  ticket_log_action, ticket_log_query)
        except ValueError as error:
            abort(400, str(error))
        except Exception:
            app.logger.exception("Could not read ticket activity logs")
            return "Ticket logs are temporarily unavailable. Please try again later.", 503

    application_config = None
    application_records = None
    application_status = request.args.get("application_status", "all")
    if application_status not in {"all", "pending", "accepted", "denied", "removed"}:
        application_status = "all"
    if page == "carry-applications":
        try:
            application_config = get_application_config()
            application_records = get_application_page(
                _application_page_number(request.args.get("application_page")), application_status
            )
            for item in application_records["items"]:
                deadline = get_application_cooldown_end(item, application_config)
                item["cooldown_end_ts"] = deadline.timestamp() if deadline else None
                item["cooldown_end_label"] = deadline.strftime("%Y-%m-%d %H:%M UTC") if deadline else None
                item["cooldown_active"] = bool(deadline and deadline.timestamp() > time.time())
        except Exception:
            app.logger.exception("Could not load Carry Team Application management")
            return "Application records are temporarily unavailable. Please try again later.", 503

    analytics = None
    analytics_period = 7
    if page == "analytics" and can_access_analytics:
        try:
            analytics_period = int(request.args.get("analytics_period", 7))
        except (TypeError, ValueError):
            analytics_period = 7
        if analytics_period not in (7, 14, 30):
            analytics_period = 7
        analytics_category = request.args.get("analytics_category", "").strip()[:100]
        analytics = get_ticket_analytics(analytics_period, analytics_category)
        # Resolve from the bot cache only; page loads never fetch every member from Discord.
        from utils.analytics import avatar_url
        for row in analytics["staff_leaderboard"]:
            member = guild.get_member(int(row["id"])) if guild and row["id"].isdigit() else None
            member = member or (bot.get_user(int(row["id"])) if row["id"].isdigit() else None)
            if member:
                row["name"] = getattr(member, "display_name", str(member))
                row["avatar_url"] = avatar_url(row["id"], str(member.display_avatar.url))

    blacklist_roles = []
    for rid in access_settings.get("blacklist_roles", []):
        role = guild.get_role(int(rid)) if guild else None
        blacklist_roles.append({"id": rid, "name": role.name if role else None})

    ticket_viewer_roles = []
    for rid in access_settings.get("ticket_viewer_roles", []):
        role = guild.get_role(int(rid)) if guild else None
        ticket_viewer_roles.append({"id": rid, "name": role.name if role else None})

    powerful_command_roles = []
    for rid in access_settings.get("powerful_command_roles", []):
        role = guild.get_role(int(rid)) if guild else None
        powerful_command_roles.append({"id": rid, "name": role.name if role else None})
    powerful_command_users = access_settings.get("powerful_command_users", [])

    moderation_command_roles = []
    for rid in access_settings.get("moderation_command_roles", []):
        role = guild.get_role(int(rid)) if guild else None
        moderation_command_roles.append({"id": rid, "name": role.name if role else None})
    moderation_command_users = access_settings.get("moderation_command_users", [])

    basic_command_roles = []
    for rid in access_settings.get("basic_command_roles", []):
        role = guild.get_role(int(rid)) if guild else None
        basic_command_roles.append({"id": rid, "name": role.name if role else None})
    basic_command_users = access_settings.get("basic_command_users", [])

    # members who are blocked from creating tickets via a Ticket Blacklist Role
    # (in addition to the individually-blacklisted user IDs above)
    role_blacklisted_members = []
    if guild and page == "blacklists":
        blacklist_role_id_set = {str(rid) for rid in access_settings.get("blacklist_roles", [])}
        if blacklist_role_id_set:
            for member in guild.members:
                member_role_ids = {str(r.id) for r in member.roles}
                matched = member_role_ids.intersection(blacklist_role_id_set)
                if matched:
                    matched_role = guild.get_role(int(next(iter(matched))))
                    role_blacklisted_members.append({
                        "id": str(member.id),
                        "name": str(member),
                        "role_name": matched_role.name if matched_role else None,
                    })

    active_tickets = []
    for t in tickets_info.get("active_tickets", []):
        t = dict(t)
        t["username"] = resolve_username(guild, t.get("user_id"), cached_only=True) or t.get("username")
        active_tickets.append(t)

    cooldowns = []
    for cd in tickets_info.get("cooldowns", []):
        cd = dict(cd)
        cd["username"] = resolve_username(guild, cd.get("user_id"), cached_only=True) or cd.get("username")
        cooldowns.append(cd)

    transcripts = []
    transcripts_page = 1
    transcripts_total_pages = 1
    transcripts_total = 0
    if page == "transcripts":
        try:
            transcripts_page = max(1, int(request.args.get("transcripts_page", 1)))
        except (TypeError, ValueError):
            transcripts_page = 1
        transcripts, transcripts_total = get_transcript_page(transcripts_page, per_page=50)
        transcripts_total_pages = max(1, (transcripts_total + 49) // 50)
        if transcripts_page > transcripts_total_pages:
            return redirect(url_for("home", page="transcripts", transcripts_page=transcripts_total_pages))

    blacklisted_users = []
    for entry in blacklist_info.get("blacklisted_users", []):
        if isinstance(entry, dict):
            entry = dict(entry)
            entry["username"] = resolve_username(guild, entry.get("user_id"), cached_only=True) or entry.get("username")
        else:
            entry = {
                "user_id": str(entry),
                "username": resolve_username(guild, entry, cached_only=True),
                "reason": None,
                "expires_ts": None,
            }
        blacklisted_users.append(entry)

    return render_template(
        "dashboard.html",
        active_page=page,
        user=user_data,
        guild_name=guild.name if guild else None,
        guild_icon_url=str(guild.icon.url) if guild and getattr(guild, "icon", None) else None,
        channels=channels,
        categories=categories,
        roles=roles,
        tickets_enabled=settings.get("tickets_enabled", True),
        april_fools_enabled=get_april_fools_enabled() if page == "carry-settings" else False,
        ad_config=get_april_fools_ad_config() if page == "carry-settings" else {},
        admin_ids=get_admin_ids(),
        total_tickets=tickets_info.get("ticket_counter", 0),
        active_tickets=active_tickets,
        transcripts=transcripts,
        transcripts_page=transcripts_page,
        transcripts_total_pages=transcripts_total_pages,
        transcripts_total=transcripts_total,
        cooldowns=cooldowns,
        blacklisted_users=blacklisted_users,
        role_blacklisted_members=role_blacklisted_members,
        allowed_users=allowed_users,
        allowed_roles=allowed_roles,
        blacklist_roles=blacklist_roles,
        blacklist_types=blacklists.get_types(guild) if page == "access-control" else [],
        ticket_viewer_roles=ticket_viewer_roles,
        powerful_command_roles=powerful_command_roles,
        powerful_command_users=powerful_command_users,
        moderation_command_roles=moderation_command_roles,
        moderation_command_users=moderation_command_users,
        basic_command_roles=basic_command_roles,
        basic_command_users=basic_command_users,
        carry_manager_roles=carry_manager_roles,
        transcripts_roles=transcripts_roles,
        remove_cooldown_roles=remove_cooldown_roles,
        analytics_roles=analytics_roles,
        pin_message_roles=pin_message_roles,
        carry_application_verdict_roles=carry_application_verdict_roles,
        carry_application_delivery=carry_application_delivery,
        is_admin_user=is_admin_user,
        can_access_carry_settings=can_access_carry_settings,
        can_manage_carry_applications=can_manage_carry_applications,
        dashboard_csrf_token=_carry_application_csrf_token() if page in ("access-control", "carry-settings", "carry-applications", "ticket-logs", "blacklists", "points") else None,
        ticket_logging=ticket_logging,
        audit_delivery_events=audit.AUDIT_EVENTS,
        ticket_log_records=ticket_log_records,
        ticket_log_query=ticket_log_query,
        ticket_log_action=ticket_log_action,
        ticket_log_actions=LOG_ACTIONS,
        ticket_delivery_events=DELIVERY_EVENTS,
        application_config=application_config,
        application_records=application_records,
        application_status=application_status,
        application_csrf_token=_carry_application_csrf_token() if page == "carry-applications" else None,
        application_server_now=time.time() if page == "carry-applications" else None,
        can_access_transcripts=can_access_transcripts,
        can_remove_cooldown=can_remove_cooldown,
        can_access_analytics=can_access_analytics,
        can_manage_points=can_manage_points,
        points_config=points_config,
        points_config_unavailable=points_config_unavailable,
        points_leaderboard=points_leaderboard,
        points_activity=points_activity,
        points_log_events=points.LOG_EVENTS,
        points_action_icons={
            "manual_add": "＋", "manual_subtract": "−", "manual_set": "＝",
            "leaderboard_removed": "×", "excluded": "⊘", "included": "✓",
            "automatic_awarded": "✦", "automatic_skipped": "!", "duplicate_ticket": "⛨",
            "format_passed": "✓", "format_rejected": "!", "thread_created": "↗",
            "thread_archived": "▣", "thread_failed": "!", "settings_updated": "⚙",
        },
        analytics=analytics,
        analytics_period=analytics_period,
        ticket_categories=get_ticket_categories() if page == "carry-settings" else [],
        ticket_badge_config=get_ticket_badge_config() if page == "carry-settings" else {},
        panel_draft=get_ticket_panel_draft() if page == "carry-settings" else {},
        redirect_message=get_redirect_message() if page == "carry-settings" else {},
        welcome_message=get_welcome_message() if page == "carry-settings" else {},
        trial_schedule=get_trial_schedule_settings() if page == "trial-schedules" else {},
        warning_presets=warning_presets,
        warning_builder=warning_builder,
        warning_records=warning_records,
        log_channel_id=access_settings.get("log_channel_id"),
        blacklist_log_channel_id=access_settings.get("blacklist_log_channel_id"),
        warning_log_channel_id=access_settings.get("warning_log_channel_id"),
        offline_schedule=get_offline_schedule() if page == "access-control" else {},
        globally_blocked_users=get_globally_blocked_users() if page == "access-control" else [],
        generated_unblock_link=generated_unblock_link,
        active_unblock_links=active_unblock_links,
    )


def _application_page_number(value):
    try:
        return max(1, int(value))
    except (ValueError, TypeError):
        return 1


@app.post("/dashboard/carry-applications/settings")
@carry_application_manager_required
def save_carry_application_config_route():
    if not _valid_carry_application_csrf():
        abort(400, "Your form expired. Reload the application manager and try again.")
    try:
        accepting = request.form.get("accepting_responses")
        if accepting not in {"yes", "no"}:
            raise ValueError("Choose whether applications accept responses.")
        days = request.form.get("denial_cooldown_days", "")
        if not re.fullmatch(r"[0-9]{1,3}", days):
            raise ValueError("The denial cooldown must be a whole number from 0 to 365 days.")
        save_application_config(accepting == "yes", int(days))
    except ValueError as error:
        flash(str(error), "danger")
    except Exception:
        app.logger.exception("Could not save Carry application configuration")
        flash("Could not save application settings. Please try again.", "danger")
    else:
        flash("Carry Team application settings saved.", "success")
    return redirect(url_for("home", page="carry-applications"))


@app.post("/dashboard/carry-applications/manage")
@carry_application_manager_required
def manage_carry_applications_route():
    if not _valid_carry_application_csrf():
        abort(400, "Your form expired. Reload the application manager and try again.")
    ids = request.form.getlist("application_ids")
    action = request.form.get("action")
    if not ids or len(ids) > 100 or any(not re.fullmatch(r"[a-f0-9]{32}", value) for value in ids):
        abort(400, "Select valid applications from the list.")
    if action not in {"remove_accepted", "clear_cooldowns"}:
        abort(400, "Choose a valid application action.")
    try:
        changed = manage_applications(ids, action, session["user"]["id"])
    except Exception:
        app.logger.exception("Could not manage selected Carry applications")
        flash("The selected applications could not be updated. Please try again.", "danger")
    else:
        label = "accepted application(s) removed" if action == "remove_accepted" else "denial cooldown(s) cleared"
        flash(f"{changed} {label}.", "success" if changed else "warning")
    status = request.form.get("application_status", "all")
    if status not in {"all", "pending", "accepted", "denied", "removed"}:
        status = "all"
    return redirect(url_for("home", page="carry-applications", application_status=status,
                            application_page=_application_page_number(request.form.get("application_page"))))


@app.route("/dashboard/trial-schedule/save", methods=["POST"])
@carry_manager_required
def save_trial_schedule_route():
    channel_id = request.form.get("trial_channel_id", "").strip()
    enabled = request.form.get("trial_enabled") == "yes"

    if enabled and not channel_id:
        flash("❌ Choose a channel before enabling Trial Schedules.", "danger")
        return redirect(url_for("home", page="trial-schedules"))

    old = get_trial_schedule_settings()
    # When the configured channel changes, forget the old message ID so the
    # bot creates one clean schedule post in the new channel instead of trying
    # to fetch a message from the wrong place.
    update = {"enabled": enabled, "channel_id": channel_id or None}
    display_mode = request.form.get("trial_display_mode", "embed").strip().lower()
    update["display_mode"] = (
        display_mode if display_mode in {"embed", "components_v2"} else "embed"
    )
    if str(old.get("channel_id") or "") != channel_id:
        update["message_id"] = None
    save_trial_schedule_settings(update)

    if enabled and channel_id:
        cog = bot.get_cog("TrialSchedule")
        if cog:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    cog.publish_or_update(channel_id=channel_id, force=True), bot.loop
                )
                message = future.result(timeout=15)
                if message:
                    flash("✅ Trial Schedule saved and published/updated.", "success")
                else:
                    flash("⚠️ Settings saved, but the bot could not publish the Trial Schedule. Check channel permissions.", "warning")
            except Exception:
                flash("⚠️ Settings saved. The bot will retry the Trial Schedule automatically.", "warning")
        else:
            flash("✅ Trial Schedule settings saved. The scheduler will start when the bot cog is loaded.", "success")
    else:
        flash("✅ Trial Schedule settings saved.", "success")

    return redirect(url_for("home", page="trial-schedules"))


@app.route("/login")
def login():
    discord_sess = make_oauth_session()
    authorization_url, state = discord_sess.authorization_url(AUTHORIZATION_BASE_URL)
    session['oauth2_state'] = state
    return redirect(authorization_url)


@app.route("/callback")
def callback():
    if request.args.get('error'):
        return request.args['error']
    
    discord_sess = make_oauth_session(state=session.get('oauth2_state'))
    token = discord_sess.fetch_token(
        TOKEN_URL,
        client_secret=CLIENT_SECRET,
        authorization_response=request_url()
    )
    session['oauth2_token'] = token
    
    user_data = discord_sess.get('https://discord.com/api/users/@me').json()
    user_id = user_data.get('id')
    avatar_hash = user_data.get('avatar')
    
    from utils.analytics import avatar_url as discord_avatar
    avatar_url = discord_avatar(user_id, f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{'gif' if str(avatar_hash).startswith('a_') else 'png'}?size=128" if avatar_hash else None)

    session['user'] = {
        'username': user_data.get('username'),
        'id': user_id,
        'avatar_url': avatar_url
    }
    
    flash(f"👋 Welcome back, {user_data.get('username')}!", "success")
    terms_token = session.pop("terms_unblock_token", None)
    if terms_token:
        return redirect(url_for("terms_unblock", token=terms_token))
    ad_return_to = session.pop("ad_return_to", None)
    if ad_return_to:
        return redirect(ad_return_to)
    transcript_return_to = session.pop("transcript_return_to", None)
    if transcript_return_to:
        return redirect(transcript_return_to)
    application_return_to = session.pop("application_return_to", None)
    if application_return_to and application_return_to.startswith("/carry-application"):
        return redirect(application_return_to)
    return redirect(url_for('home', page="overview"))


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for('home'))

@app.route("/dashboard/warnings/add-preset", methods=["POST"])
@admin_required
def add_warning_preset_route():
    name = request.form.get("name", "").strip()
    reason = request.form.get("reason", "").strip()

    try:
        add_premade_warning_reason(name, reason)
        flash("Warning preset added.", "success")
    except ValueError as error:
        flash(str(error), "danger")
    except Exception:
        app.logger.exception("Failed to add warning preset")
        flash("Could not add the warning preset.", "danger")

    return redirect(url_for("home", page="warning-builder"))


@app.route("/dashboard/warnings/save-builder/<tier>", methods=["POST"])
@admin_required
def save_warning_builder_route(tier):
    tier = str(tier).upper()
    try:
        save_warning_builder(tier, {
            "title": request.form.get("title", ""),
            "description": request.form.get("description", ""),
            "color": request.form.get("color", ""),
            "footer": request.form.get("footer", ""),
            "role_id": request.form.get("role_id", ""),
        })
        flash(f"{tier} warning embed and role settings saved.", "success")
    except ValueError as error:
        flash(str(error), "danger")
    except Exception:
        app.logger.exception("Failed to save warning builder settings")
        flash("Could not save warning builder settings.", "danger")
    return redirect(url_for("home", page="warning-builder"))


@app.route("/dashboard/warnings/edit-preset/<int:preset_id>", methods=["POST"])
@admin_required
def edit_warning_preset_route(preset_id):
    name = request.form.get("name", "").strip()
    reason = request.form.get("reason", "").strip()

    preset = update_premade_warning_reason(preset_id, name, reason)

    if preset:
        flash("Warning preset updated.", "success")
    else:
        flash("Could not update that warning preset.", "danger")

    return redirect(url_for("home", page="warning-builder"))


@app.route("/dashboard/warnings/delete-preset/<int:preset_id>", methods=["POST"])
@admin_required
def delete_warning_preset_route(preset_id):
    if remove_premade_warning_reason(preset_id):
        flash("Warning preset deleted.", "success")
    else:
        flash("Could not find that warning preset.", "danger")

    return redirect(url_for("home", page="warning-builder"))

@app.route("/dashboard/toggle-tickets", methods=["POST"])
@carry_manager_required
def toggle_tickets():
    is_enabled = request.form.get("tickets_enabled") in ["on", "true", "True"]
    set_tickets_enabled(is_enabled)
    
    status_text = "enabled" if is_enabled else "disabled"
    flash(f"⚙️ Ticket creation has been {status_text}.", "success" if is_enabled else "warning")
    return redirect(url_for("home", page="overview"))


@app.route("/dashboard/toggle-april-fools", methods=["POST"])
@carry_manager_required
def toggle_april_fools():
    is_enabled = request.form.get("april_fools_enabled") in ["on", "true", "True"]
    set_april_fools_enabled(is_enabled)

    status_text = "enabled" if is_enabled else "disabled"
    flash(f"📺 April Fools ad gag has been {status_text}.", "success" if is_enabled else "warning")
    return redirect(url_for("home", page="carry-settings", _anchor="ad-gag-settings"))


# --- April Fools: web ad gate before a ticket form opens ---
def _ad_page_guard(token):
    """Shared checks for the ad pages. Returns (entry, error_response).

    Deliberately NOT using admin_required/login_required: an ordinary
    member opening a ticket needs to prove Discord identity only, not
    hold any dashboard permission.
    """
    entry = get_ticket_ad_verification(token)
    if not entry:
        return None, render_template(
            "ticket_ad_verification.html",
            invalid=True,
            ad=pick_april_fools_ad(token),
        )

    user_data = session.get("user")
    if not user_data:
        session["ad_return_to"] = url_for("ticket_ad_page", token=token)
        return None, redirect(url_for("login"))

    if str(user_data.get("id")) != str(entry.get("user_id")):
        return None, render_template(
            "ticket_ad_verification.html",
            mismatch=True,
            user=user_data,
            ad=pick_april_fools_ad(token),
        )

    return entry, None


@app.route("/ticket-ad/<token>")
def ticket_ad_page(token):
    entry, error = _ad_page_guard(token)
    if error:
        return error

    cfg = get_april_fools_ad_config()
    ad = pick_april_fools_ad(token)
    countdown = cfg["countdown_seconds"]
    mark_ticket_ad_viewed(token)
    remaining = ticket_ad_seconds_remaining(token, countdown)

    from utils.storage import youtube_embed_url

    return render_template(
        "ticket_ad_verification.html",
        user=session.get("user"),
        ad=ad,
        youtube_embed_url=youtube_embed_url(ad.get("link_url")) if ad.get("type") == "video" else None,
        verification_token=token,
        countdown_seconds=countdown,
        completed=bool(entry.get("completed")),
        seconds_remaining=remaining if remaining is not None else countdown,
    )


@app.route("/ticket-ad/<token>/complete", methods=["POST"])
def ticket_ad_complete(token):
    entry, error = _ad_page_guard(token)
    if error:
        return error

    cfg = get_april_fools_ad_config()
    ad = pick_april_fools_ad(token)
    countdown = cfg["countdown_seconds"]
    if complete_ticket_ad_verification(token, min_watch_seconds=countdown):
        return render_template(
            "ticket_ad_verification.html",
            user=session.get("user"),
            ad=ad,
            verification_token=token,
            countdown_seconds=countdown,
            completed=True,
            ready_to_return=True,
            seconds_remaining=0,
        )

    remaining = ticket_ad_seconds_remaining(token, countdown)
    from utils.storage import youtube_embed_url
    return render_template(
        "ticket_ad_verification.html",
        user=session.get("user"),
        ad=ad,
        youtube_embed_url=youtube_embed_url(ad.get("link_url")) if ad.get("type") == "video" else None,
        verification_token=token,
        countdown_seconds=countdown,
        completed=False,
        too_early=True,
        seconds_remaining=remaining if remaining is not None else countdown,
    )


@app.route("/admin")
@admin_required
def admin_panel():
    # Redirect old /admin bookmarks into the integrated dashboard settings.
    return redirect(url_for("home", page="carry-settings", _anchor="ad-gag-settings"))


@app.route("/admin/april-fools", methods=["POST"])
@app.route("/dashboard/ad-gag/save", methods=["POST"])
@admin_required
def admin_save_april_fools():
    try:
        countdown = int(request.form.get("countdown_seconds", 15))
    except (TypeError, ValueError):
        countdown = 15

    # The ad list is built client-side (add/remove rows) and posted as JSON,
    # same pattern the panel builder uses.
    try:
        ads = json.loads(request.form.get("ads_json") or "[]")
    except Exception:
        ads = []
    if not isinstance(ads, list):
        ads = []

    # Drop rows the admin left completely blank rather than saving empties.
    ads = [
        a for a in ads
        if isinstance(a, dict) and any(
            str(a.get(k, "")).strip()
            for k in ("headline", "body", "image_url", "link_url")
        )
    ]

    if not ads:
        flash("❌ Add at least one ad with some content before saving.", "danger")
        return redirect(url_for("home", page="carry-settings", _anchor="ad-gag-settings"))

    save_april_fools_ad_config(
        ads=ads,
        countdown_seconds=max(1, min(countdown, 120)),
        randomize=request.form.get("randomize") in ("on", "true", "True"),
    )
    flash(f"📺 Saved {len(ads)} April Fools ad(s).", "success")
    return redirect(url_for("home", page="carry-settings", _anchor="ad-gag-settings"))


@app.route("/transcripts/<path:filename>")
def get_transcript(filename):
    from flask import request, abort, Response

    transcript_info = get_transcript_info(filename)
    if not transcript_info:
        abort(404)

    token = request.args.get("token")
    if token:
        from utils.storage import verify_transcript_token
        signed_info = verify_transcript_token(token)
        if not signed_info or signed_info.get("filename") != filename:
            abort(403)

    created_logs = get_logs_for_ticket(filename.removeprefix("ticket-").removesuffix(".html"))
    created = next(
        (entry for entry in created_logs if entry.get("action") == "created"),
        None,
    )
    creator = (created or {}).get("creator") or {}
    creator_id = str(creator.get("id") or "")
    current_user = session.get("user") or {}

    if not current_user:
        # Keep the signed URL in the session so OAuth returns to this exact
        # transcript instead of dropping the user on the dashboard.
        session["transcript_return_to"] = request.full_path
        flash("🔒 Authorize with Discord to verify your account before viewing this transcript.", "warning")
        return redirect(url_for("login"))

    current_user_id = str(current_user.get("id") or "")
    transcript_staff_access = has_transcripts_access(
        current_user_id,
        get_member_role_ids(current_user_id),
    )
    if (
        not transcript_staff_access
        and current_user_id != creator_id
    ):
        return "This transcript belongs to a different Discord account.", 403

    def _serve():
        html = get_transcript_html(filename)
        if html is None:
            abort(404)
        return Response(theme_saved_transcript(html), mimetype="text/html")

    return _serve()


@app.route("/preview/transcript")
def preview_transcript():
    return Response(render_preview_transcript(back_url=url_for("home", page="transcripts")), mimetype="text/html")


@app.route("/tickets/logs")
@access_required
def view_ticket_logs():
    return redirect(url_for("home", page="ticket-logs"))


# Keep diagnostic history behind the same dashboard permission check.
@app.route("/debug/ticket-logs")
@access_required
def debug_ticket_logs():
    try:
        last = ticket_log_page(per_page=100)["items"]
        return jsonify({"ok": True, "count": len(last), "logs": last})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/tickets/<ticket_id>")
@access_required
def view_ticket(ticket_id):
    logs = get_logs_for_ticket(ticket_id)
    ticket = None
    if logs:
        # take earliest create log as ticket meta
        ticket = logs[0]
        created = next((l for l in logs if l.get("action") == "created"), None)
    else:
        ticket = {"ticket_id": ticket_id, "fields": {}, "ticket_name": None}
        created = None

    # try to find a transcript file containing the ticket name or id
    transcript_url = None
    try:
        import glob
        import os
        for f in glob.glob(f"{TRANSCRIPTS_DIR}/*.html"):
            name = os.path.basename(f)
            if ticket_id in name or (ticket.get("ticket_name") and ticket.get("ticket_name") in name):
                transcript_url = f"/transcripts/{name}"
                break
    except Exception:
        transcript_url = None

    created_at = created.get("timestamp") if created else None
    return render_template("ticket_detail.html", ticket=ticket, logs=logs, transcript_url=transcript_url, created_at=created_at)


@app.route("/api/unblacklist/<user_id>", methods=["POST"])
@admin_required
def api_unblacklist(user_id):
    if not _valid_carry_application_csrf():
        return jsonify(success=False, error="Form expired. Reload and try again."), 400
    if not user_id.isdigit():
        abort(400)
    async def remove_saved_blacklist():
        entry = next((row for row in get_blacklist_data(include_expired=True)["blacklisted_users"]
                      if str(row.get("user_id")) == user_id), None)
        if entry is None:
            return False
        guild_id = entry.get("guild_id")
        guilds = [bot.get_guild(int(guild_id))] if guild_id else bot.guilds
        if not guilds or any(guild is None for guild in guilds):
            raise RuntimeError("The blacklist server is unavailable.")
        for guild in guilds:
            try:
                member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
            except discord.NotFound:
                continue
            assigned = blacklists.role_id(entry)
            role = guild.get_role(int(assigned)) if assigned else None
            if role and any(item.id == role.id for item in member.roles):
                await audit.change_role(member, role, add=False, reason="Blacklist removed from dashboard")
        return blacklists.remove(user_id, expected_entry=entry)
    if not bot.is_ready():
        return jsonify(success=False, error="The bot must be online to remove the assigned role."), 503
    try:
        removed = asyncio.run_coroutine_threadsafe(remove_saved_blacklist(), bot.loop).result(timeout=20)
    except Exception:
        app.logger.exception("Could not remove blacklist from dashboard")
        return jsonify(success=False, error="Could not confirm removal. Check the bot's Manage Roles permission and role position, then retry."), 503
    if removed:
        from cogs.tickets import send_blacklist_log
        from utils.storage import append_ticket_log
        append_ticket_log({"ticket_id": "global", "action": "blacklist_removed", "target": {"id": user_id},
            "executor": {"id": str(session["user"]["id"])}, "reason": "Removed from dashboard",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()})
        asyncio.run_coroutine_threadsafe(send_blacklist_log(bot, "blacklist_removed", user=user_id,
            reason="Removed from dashboard", executor=str(session["user"]["id"])), bot.loop)
    return jsonify(success=removed)



@app.route("/api/remove-cooldown/<user_id>", methods=["POST"])
@remove_cooldown_required
def api_remove_cooldown(user_id):
    from utils.storage import remove_cooldown
    ok = remove_cooldown(user_id)
    if ok:
        audit.emit_event(bot, "cooldown_removed", session["user"]["id"], user_id=user_id, source="Dashboard")
    return jsonify({"success": ok})


def _safe_int(val):
    try:
        return int(val) if val and str(val).strip() else None
    except ValueError:
        return None


def _panel_draft_from_form():
    raw_fields_json = request.form.get("fields_json", "[]")
    raw_components_json = request.form.get("components_json", "[]")
    try:
        fields = json.loads(raw_fields_json)
    except Exception:
        fields = []
    try:
        components = json.loads(raw_components_json)
        if not isinstance(components, list):
            components = []
    except Exception:
        components = []

    return {
        "channel_id": _safe_int(request.form.get("channel_id")),
        "category_id": _safe_int(request.form.get("category_id")),
        "support_role_id": _safe_int(request.form.get("support_role_id")),
        "title": request.form.get("title", "Request Carry"),
        "description": request.form.get(
            "description", "Click below to request a carry ticket!"
        ),
        "embed_color": request.form.get("embed_color", "#58b9ff"),
        "image_url": request.form.get("image_url", "").strip() or None,
        "thumbnail_url": request.form.get("thumbnail_url", "").strip() or None,
        "footer_text": request.form.get("footer_text", "").strip() or None,
        "fields": fields,
        "components": components,
    }


@app.route("/dashboard/tickets/save-draft", methods=["POST"])
@carry_manager_required
def save_ticket_panel_draft_route():
    draft = _panel_draft_from_form()
    save_ticket_panel_draft(draft)
    flash("💾 Panel draft saved. It'll be pre-filled next time you open this builder.", "success")
    return redirect(url_for("home", page="carry-settings"))


@app.route("/dashboard/tickets", methods=["POST"])
@carry_manager_required
def deploy_ticket_panel():
    draft = _panel_draft_from_form()
    # Deploying also remembers this config, so it's pre-filled next time -
    # not just an explicit "Save Draft" click.
    save_ticket_panel_draft(draft)

    channel_id = draft["channel_id"]
    category_id = draft["category_id"]
    support_role_id = draft["support_role_id"]

    if not channel_id:
        flash("❌ Please select a valid target channel.", "danger")
        return redirect(url_for("home", page="carry-settings"))

    title = draft["title"]
    description = draft["description"]
    embed_color = draft["embed_color"]
    image_url = draft["image_url"]
    thumbnail_url = draft["thumbnail_url"]
    footer_text = draft["footer_text"]
    fields = draft["fields"]

    cog = bot.get_cog("TicketsCog") or bot.get_cog("Tickets")
    if cog:
        future = asyncio.run_coroutine_threadsafe(
            cog.deploy_panel_from_dashboard(
                channel_id=channel_id,
                title=title,
                description=description,
                category_id=category_id,
                support_role_id=support_role_id,
                color=embed_color,
                image_url=image_url,
                thumbnail_url=thumbnail_url,
                footer_text=footer_text,
                fields=fields,
                components=draft.get("components") or []
            ),
            bot.loop
        )
        try:
            success, msg = future.result(timeout=10)
            if success:
                flash("🎉 Carry Panel deployed successfully!", "success")
            else:
                flash(f"❌ {msg}", "danger")
        except Exception as e:
            flash(f"❌ Error deploying panel: {e}", "danger")
    else:
        flash("❌ Ticket cog not found.", "danger")

    return redirect(url_for("home", page="carry-settings"))



@app.post("/dashboard/points/settings")
@admin_required
def points_save_settings_route():
    if not _valid_carry_application_csrf():
        abort(400, "Your Points settings form expired. Reload Access Control and try again.")
    guild = bot.guilds[0] if bot.guilds else None
    try:
        command_roles = request.form.getlist("command_role_ids")
        thread_channels = request.form.getlist("thread_channel_ids")
        checker_channels = request.form.getlist("format_checker_channel_ids")
        exempt_roles = request.form.getlist("format_checker_exempt_role_ids")
        log_channel_id = request.form.get("points_log_channel_id", "").strip()

        if not guild and any((thread_channels, checker_channels, command_roles, exempt_roles, log_channel_id)):
            raise ValueError("The connected Discord server is unavailable. Try again when the bot is online.")
        if guild:
            for channel_id in thread_channels + checker_channels + ([log_channel_id] if log_channel_id else []):
                channel = guild.get_channel(int(channel_id)) if str(channel_id).isdigit() else None
                if not isinstance(channel, discord.TextChannel):
                    raise ValueError("Choose only text channels from the connected server.")
            for role_id in command_roles + exempt_roles:
                role = guild.get_role(int(role_id)) if str(role_id).isdigit() else None
                if role is None or role.is_default():
                    raise ValueError("Choose valid server roles for Points access and exemptions.")

        gamemode_texts = request.form.getlist("gamemode_text")
        gamemode_points = request.form.getlist("gamemode_points")
        gamemode_reasons = request.form.getlist("gamemode_reason")
        gamemodes = []
        for index, text_value in enumerate(gamemode_texts):
            amount = gamemode_points[index] if index < len(gamemode_points) else ""
            reason = gamemode_reasons[index] if index < len(gamemode_reasons) else ""
            if not str(text_value or "").strip() and not str(amount or "").strip() and not str(reason or "").strip():
                continue
            gamemodes.append({"text": text_value, "points": amount or 0, "reason": reason})

        config = {
            "command_role_ids": command_roles,
            "thread_automation": {
                "enabled": request.form.get("thread_automation_enabled") == "yes",
                "channel_ids": thread_channels,
                "thread_name": request.form.get("thread_name", "{author} - {ticket_number}"),
            },
            "format_checker": {
                "enabled": request.form.get("format_checker_enabled") == "yes",
                "channel_ids": checker_channels,
                "exempt_role_ids": exempt_roles,
            },
            "gamemodes": gamemodes,
            "logging": {
                "enabled": request.form.get("points_logging_enabled") == "yes",
                "channel_id": log_channel_id or None,
                "events": request.form.getlist("points_log_events"),
            },
        }
        if config["thread_automation"]["enabled"] and not thread_channels:
            raise ValueError("Choose at least one channel before enabling Thread Automation.")
        if config["format_checker"]["enabled"] and not checker_channels:
            raise ValueError("Choose at least one channel before enabling the Format Checker.")
        if config["logging"]["enabled"] and not log_channel_id:
            raise ValueError("Choose a logging channel before enabling Discord Points Logging.")
        saved = points.save_points_config(guild.id if guild else "global", config)
        if guild:
            try:
                points.record_event(
                    guild.id, "settings_updated",
                    actor_id=session["user"]["id"],
                    actor_name=session["user"].get("username", "Dashboard"),
                    details="Gamemodes, command access, automation, or logging settings were updated.",
                )
                cog = bot.get_cog("PointsCog")
                if cog and bot.loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        cog._deliver_log(
                            guild, "settings_updated",
                            actor_id=session["user"]["id"],
                            actor_name=session["user"].get("username", "Dashboard"),
                            details="Points dashboard settings were updated.",
                        ),
                        bot.loop,
                    )
            except Exception:
                app.logger.exception("Could not record Points settings update")
        if saved["format_checker"]["enabled"] and not bot.intents.message_content:
            flash("Points settings saved. Enable Message Content Intent in Discord and set DISCORD_MESSAGE_CONTENT_INTENT=true for Format Checker scanning.", "warning")
        else:
            flash("Points settings saved successfully.", "success")
    except ValueError as error:
        flash(str(error), "danger")
    except Exception:
        app.logger.exception("Could not save Points settings")
        flash("Points settings could not be saved. Please retry.", "danger")
    return redirect(url_for("home", page="access-control", _anchor="access-points"))


@app.post("/dashboard/points/adjust")
@carry_manager_required
def points_adjust_route():
    if not _valid_carry_application_csrf():
        abort(400, "Your Points form expired. Reload the page and try again.")
    user_id = request.form.get("user_id", "").strip()
    username = request.form.get("username", "").strip()[:100]
    operation = request.form.get("operation", "").strip().lower()
    reason = request.form.get("reason", "").strip()
    try:
        amount = int(request.form.get("amount", ""))
    except (TypeError, ValueError):
        amount = -1
    if not user_id.isdigit():
        flash("Enter a valid Discord user ID.", "danger")
        return redirect(url_for("home", page="points", _anchor="points-manager"))
    try:
        if operation not in {"set", "add", "subtract"}:
            raise ValueError("Choose Set, Add, or Subtract.")
        if amount < 0 or (operation != "set" and amount < 1):
            raise ValueError("Set may use 0; Add and Subtract require at least 1 point.")
        guild = bot.guilds[0] if bot.guilds else None
        member = guild.get_member(int(user_id)) if guild else None
        username = username or (getattr(member, "display_name", None) if member else None) or user_id
        updated = points.adjust_points(
            guild.id if guild else "global", user_id, username,
            operation, amount, reason,
            session["user"]["id"], session["user"].get("username", "Dashboard"),
        )
        if guild:
            action = {"add": "manual_add", "subtract": "manual_subtract", "set": "manual_set"}[operation]
            cog = bot.get_cog("PointsCog")
            if cog and bot.loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    cog._deliver_log(
                        guild, action, user_id=user_id, username=username,
                        amount=amount if operation != "subtract" else -amount,
                        points_after=int(updated.get("points", 0)), reason=reason,
                        actor_id=session["user"]["id"],
                        actor_name=session["user"].get("username", "Dashboard"),
                    ),
                    bot.loop,
                )
        flash(f"Points updated for {username}. New balance: {int(updated.get('points', 0)):,}.", "success")
    except ValueError as error:
        flash(str(error), "danger")
    except Exception:
        app.logger.exception("Dashboard Points adjustment failed")
        flash("The Points balance could not be updated. Please retry.", "danger")
    return redirect(url_for("home", page="points", _anchor="points-manager"))


@app.post("/dashboard/access/bulk-remove")
@admin_required
def bulk_remove_access_route():
    if not _valid_carry_application_csrf():
        abort(400, "Your form expired. Reload Access Control and try again.")
    kind = request.form.get("list_kind", "")
    try:
        previous_values = {str(value) for value in get_access_settings().get(kind, [])} if kind in ACCESS_LISTS else set()
        selected = request.form.getlist("selected_ids")
        changed = pin_permissions.change_roles(remove=selected) if kind == "pin_message_roles" else remove_access_entries(kind, selected)
    except ValueError as error:
        abort(400, str(error))
    except Exception:
        app.logger.exception("Could not remove selected access entries")
        flash("The selected entries could not be removed. Please retry.", "danger")
    else:
        if changed:
            action = "user_unblocked" if kind == "globally_blocked_users" else "blacklist_role_deleted" if kind == "blacklist_roles" else "access_entries_removed"
            audit.emit_event(bot, action, session["user"]["id"], access_list=ACCESS_LISTS[kind],
                count=changed, removed_ids=", ".join(sorted(previous_values.intersection(selected))), source="Bulk Access Control")
        if changed and kind == "pin_message_roles":
            _queue_native_pin_sync()
        flash(f"Removed {changed} selected {ACCESS_LISTS[kind]}.", "success" if changed else "info")
    section = request.form.get("section", "")
    if not re.fullmatch(r"[a-z0-9-]{1,100}", section):
        section = ""
    return redirect(url_for("home", page="access-control", _anchor=section))


@app.post("/dashboard/ticket-badge/bulk-remove")
@carry_manager_required
def bulk_remove_ticket_badges_route():
    if not _valid_carry_application_csrf():
        abort(400, "Your form expired. Reload Carry Manager Settings and try again.")
    try:
        changed = remove_blocked_badges(request.form.getlist("selected_ids"))
    except ValueError as error:
        abort(400, str(error))
    except Exception:
        app.logger.exception("Could not remove selected badge entries")
        flash("The selected badges could not be removed. Please retry.", "danger")
    else:
        flash(f"Removed {changed} selected blocked badges.", "success" if changed else "info")
    return redirect(url_for("home", page="carry-settings", _anchor="carry-verification"))


@app.post("/dashboard/ticket-logs/settings")
@admin_required
def save_ticket_logging_route():
    if not _valid_carry_application_csrf():
        abort(400, "Your form expired. Reload Ticket Logging and try again.")
    try:
        enabled = request.form.get("logging_enabled")
        if enabled not in {"yes", "no"}:
            raise ValueError("Choose whether Discord ticket logging is enabled.")
        audit_config = None
        if request.form.get("audit_settings_present") == "yes":
            if request.form.get("audit_logging_enabled") not in {"yes", "no"}:
                raise ValueError("Choose whether access audit delivery is enabled.")
            audit_config = {"enabled": request.form.get("audit_logging_enabled") == "yes",
                "events": request.form.getlist("audit_events"), "channel_id": request.form.get("access_log_channel_id", "")}
        save_ticket_logging_config(enabled == "yes", request.form.getlist("log_events"),
            {key: request.form.get(key, "") for key in ("log_channel_id", "blacklist_log_channel_id", "warning_log_channel_id")}, audit=audit_config)
    except ValueError as error:
        flash(str(error), "danger")
    except Exception:
        app.logger.exception("Could not save ticket logging settings")
        flash("Ticket logging settings could not be saved. Please retry.", "danger")
    else:
        flash("Ticket logging settings saved.", "success")
    return redirect(url_for("home", page="access-control" if request.form.get("settings_page") == "access-control" else "ticket-logs", _anchor="access-loggings" if request.form.get("settings_page") == "access-control" else "ticket-log-delivery"))


@app.route("/dashboard/access/add-user", methods=["POST"])
@admin_required
def access_add_user():
    user_id = request.form.get("user_id", "").strip()
    if user_id.isdigit():
        add_allowed_user(user_id)
        flash("✅ User added to the dashboard allow-list.", "success")
    else:
        flash("❌ Please enter a valid numeric Discord user ID.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/add-global-block", methods=["POST"])
@admin_required
def access_add_global_block():
    user_id = request.form.get("user_id", "").strip()
    if add_globally_blocked_user(user_id):
        audit.emit_event(bot, "user_blocked", session["user"]["id"], user_id=user_id, source="Access Control")
        flash("✅ User globally blocked from slash commands and dashboard access.", "success")
    else:
        flash("❌ Enter a valid, non-duplicate numeric Discord user ID.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/remove-global-block/<user_id>", methods=["POST"])
@admin_required
def access_remove_global_block(user_id):
    if remove_globally_blocked_user(user_id):
        audit.emit_event(bot, "user_unblocked", session["user"]["id"], user_id=user_id, source="Access Control")
        flash("✅ Global block removed.", "success")
    else:
        flash("❌ That user is not globally blocked.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/generate-unblock-link", methods=["POST"])
@admin_required
def access_generate_unblock_link():
    user_id = request.form.get("user_id", "").strip()
    try:
        token = create_unblock_terms_token(user_id)
        link = external_url("terms_unblock", token=token)
        session["generated_unblock_link"] = link
        audit.emit_event(bot, "unblock_link_created", session["user"]["id"], user_id=user_id, source="Access Control")
        flash(f"Terms unblock link generated for {user_id}.", "success")
    except ValueError as error:
        flash(str(error), "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/revoke-unblock-link", methods=["POST"])
@admin_required
def access_revoke_unblock_link():
    token = request.form.get("token", "").strip()
    token_data = get_terms_unblock_token(token)
    if revoke_terms_unblock_token(token):
        audit.emit_event(bot, "unblock_link_revoked", session["user"]["id"], user_id=(token_data or {}).get("user_id"), source="Access Control")
        if session.get("generated_unblock_link") == external_url(
            "terms_unblock", token=token
        ):
            session.pop("generated_unblock_link", None)
        flash("✅ Terms unblock link revoked.", "success")
    else:
        flash("❌ That link is already used, expired, or revoked.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/remove-user/<user_id>", methods=["POST"])
@admin_required
def access_remove_user(user_id):
    remove_allowed_user(user_id)
    flash("🗑️ User removed from the allow-list.", "info")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/add-role", methods=["POST"])
@admin_required
def access_add_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_allowed_role(role_id)
        flash("✅ Role added to the dashboard allow-list.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/remove-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_role(role_id):
    remove_allowed_role(role_id)
    flash("🗑️ Role removed from the allow-list.", "info")
    return redirect(url_for("home", page="access-control"))


@app.post("/dashboard/access/blacklist-types")
@admin_required
def access_save_blacklist_types():
    if not _valid_carry_application_csrf():
        abort(400, "Your form expired. Reload Access Control and try again.")
    try:
        names = ("blacklist_name", "blacklist_role", "blacklist_reason", "blacklist_scope")
        values = [request.form.getlist(name) for name in names]
        if len({len(items) for items in values}) != 1:
            raise ValueError("Incomplete blacklist fields. Reload and retry.")
        rows = []
        for name, role, reason, scope in zip(*values):
            if scope not in {"all", "categories"}:
                raise ValueError("Choose a valid blacklist scope.")
            rows.append(dict(id=role, name=name, role_id=role, reason=reason, block_all=scope == "all"))
        previous_roles = blacklists.get_types()
        saved_roles = blacklists.save_types(rows)
    except ValueError as error:
        flash(str(error), "danger")
    except Exception:
        app.logger.exception("Could not save blacklist types")
        flash("Blacklist settings could not be saved. Please retry.", "danger")
    else:
        audit.role_config_events(bot, previous_roles, saved_roles, session["user"]["id"])
        flash("Blacklist roles saved. Existing blacklists keep their assigned roles and restrictions.", "success")
    return redirect(url_for("home", page="access-control", _anchor="access-blacklist-types"))


@app.route("/dashboard/access/add-blacklist-role", methods=["POST"])
@admin_required
def access_add_blacklist_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        previous_roles = blacklists.get_types()
        if add_blacklist_role(role_id):
            audit.role_config_events(bot, previous_roles, blacklists.get_types(), session["user"]["id"])
        flash("✅ Role added to the Ticket Blacklist.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/remove-blacklist-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_blacklist_role(role_id):
    previous_roles = blacklists.get_types()
    if remove_blacklist_role(role_id):
        audit.role_config_events(bot, previous_roles, blacklists.get_types(), session["user"]["id"])
    flash("🗑️ Role removed from the Ticket Blacklist.", "info")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/add-viewer-role", methods=["POST"])
@admin_required
def access_add_viewer_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_ticket_viewer_role(role_id)
        flash("✅ Role added as a Ticket Viewer Role.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/remove-viewer-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_viewer_role(role_id):
    remove_ticket_viewer_role(role_id)
    flash("🗑️ Role removed from Ticket Viewer Roles.", "info")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/add-powerful-command-role", methods=["POST"])
@admin_required
def access_add_powerful_command_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_powerful_command_role(role_id)
        flash("✅ Role added to Powerful Command Access.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/remove-powerful-command-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_powerful_command_role(role_id):
    remove_powerful_command_role(role_id)
    flash("🗑️ Role removed from Powerful Command Access.", "info")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/add-powerful-command-user", methods=["POST"])
@admin_required
def access_add_powerful_command_user():
    user_id = request.form.get("user_id", "").strip()
    if user_id.isdigit():
        add_powerful_command_user(user_id)
        flash("✅ User added to Powerful Command Access.", "success")
    else:
        flash("❌ Please enter a valid user ID.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/remove-powerful-command-user/<user_id>", methods=["POST"])
@admin_required
def access_remove_powerful_command_user(user_id):
    remove_powerful_command_user(user_id)
    flash("🗑️ User removed from Powerful Command Access.", "info")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/add-moderation-command-role", methods=["POST"])
@admin_required
def access_add_moderation_command_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_moderation_command_role(role_id)
        flash("✅ Role added to Moderation Command Access.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/remove-moderation-command-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_moderation_command_role(role_id):
    remove_moderation_command_role(role_id)
    flash("🗑️ Role removed from Moderation Command Access.", "info")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/add-moderation-command-user", methods=["POST"])
@admin_required
def access_add_moderation_command_user():
    user_id = request.form.get("user_id", "").strip()
    if user_id.isdigit():
        add_moderation_command_user(user_id)
        flash("✅ User added to Moderation Command Access.", "success")
    else:
        flash("❌ Please enter a valid user ID.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/remove-moderation-command-user/<user_id>", methods=["POST"])
@admin_required
def access_remove_moderation_command_user(user_id):
    remove_moderation_command_user(user_id)
    flash("🗑️ User removed from Moderation Command Access.", "info")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/add-basic-command-role", methods=["POST"])
@admin_required
def access_add_basic_command_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_basic_command_role(role_id)
        flash("✅ Role added to Basic Command Access.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/remove-basic-command-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_basic_command_role(role_id):
    remove_basic_command_role(role_id)
    flash("🗑️ Role removed from Basic Command Access.", "info")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/add-basic-command-user", methods=["POST"])
@admin_required
def access_add_basic_command_user():
    user_id = request.form.get("user_id", "").strip()
    if user_id.isdigit():
        add_basic_command_user(user_id)
        flash("✅ User added to Basic Command Access.", "success")
    else:
        flash("❌ Please enter a valid user ID.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/remove-basic-command-user/<user_id>", methods=["POST"])
@admin_required
def access_remove_basic_command_user(user_id):
    remove_basic_command_user(user_id)
    flash("🗑️ User removed from Basic Command Access.", "info")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/ticket-badge/save", methods=["POST"])
@carry_manager_required
def save_ticket_badge_route():
    badge_id=request.form.get("ticket_badge_id", "").strip()
    if badge_id and not badge_id.isdigit():
        flash("❌ Badge ID must contain numbers only.", "danger")
        return redirect(url_for("home", page="carry-settings"))
    save_ticket_badge_config(badge_id)
    flash("✅ Ticket badge verification settings saved.", "success")
    return redirect(url_for("home", page="carry-settings"))


@app.route("/dashboard/ticket-badge/add", methods=["POST"])
@carry_manager_required
def add_ticket_blocked_badge_route():
    badge_id=request.form.get("blocked_badge_id", "").strip()
    if not badge_id.isdigit():
        flash("❌ Blocked badge ID must contain numbers only.", "danger")
        return redirect(url_for("home", page="carry-settings"))
    if add_blocked_ticket_badge(badge_id):
        flash(f"✅ Badge {badge_id} added to the blocked badge list.", "success")
    else:
        flash("ℹ️ That badge is already in the blocked badge list.", "info")
    return redirect(url_for("home", page="carry-settings"))


@app.route("/dashboard/ticket-badge/remove/<badge_id>", methods=["POST"])
@carry_manager_required
def remove_ticket_blocked_badge_route(badge_id):
    if remove_blocked_ticket_badge(badge_id):
        flash(f"🗑️ Badge {badge_id} removed from the blocked badge list.", "info")
    else:
        flash("❌ That badge was not found in the blocked badge list.", "danger")
    return redirect(url_for("home", page="carry-settings"))


@app.route("/dashboard/ticket-categories/save", methods=["POST"])
@carry_manager_required
def save_ticket_categories_route():
    if request.accept_mimetypes.best == "application/json" and not _valid_carry_application_csrf():
        return jsonify({"ok": False, "error": "Your form expired. Reload and try again."}), 400
    labels = request.form.getlist("cat_label")
    descriptions = request.form.getlist("cat_description")
    emojis = request.form.getlist("cat_emoji")
    blacklist_roles_raw = request.form.getlist("cat_blacklist_roles")
    name_prefixes = request.form.getlist("cat_name_prefix")
    open_notes = request.form.getlist("cat_open_note")
    discord_category_ids = request.form.getlist("cat_discord_category_id")
    dropdown_enabled_raw = request.form.getlist("cat_dropdown_enabled")
    variables_raw = request.form.getlist("cat_variables")
    badge_mode_raw = request.form.getlist("cat_badge_mode")
    badge_ids_raw = request.form.getlist("cat_badge_ids")

    # these lists aren't guaranteed to line up 1:1 with the other lists
    # (older cached pages, etc.) so pad them out defensively
    for lst in (blacklist_roles_raw, name_prefixes, open_notes, discord_category_ids, dropdown_enabled_raw, variables_raw, badge_mode_raw, badge_ids_raw):
        while len(lst) < len(labels):
            lst.append("")

    from utils.storage import slugify

    categories = []
    for label, desc, emoji, bl_raw, prefix_raw, note_raw, disc_cat_raw, dd_enabled, vars_raw, badge_mode, badge_ids_item in zip(
        labels, descriptions, emojis, blacklist_roles_raw,
        name_prefixes, open_notes, discord_category_ids, dropdown_enabled_raw, variables_raw, badge_mode_raw, badge_ids_raw,
    ):
        label = label.strip()
        if not label:
            continue
        blacklist_roles = [r.strip() for r in bl_raw.split(",") if r.strip()]
        badge_ids = []
        for badge_id in str(badge_ids_item or "").split(","):
            badge_id = badge_id.strip()
            if badge_id.isdigit() and badge_id not in badge_ids:
                badge_ids.append(badge_id)
        prefix = slugify(prefix_raw.strip() or label)
        variables = {}
        try:
            parsed_vars = json.loads(vars_raw or "{}")
            if isinstance(parsed_vars, dict):
                for k, v in parsed_vars.items():
                    key = str(k).strip().lower()
                    if key and re.match(r"^[a-z0-9_\-]{1,32}$", key):
                        variables[key] = str(v)[:100]
        except Exception:
            variables = {}
        categories.append({
            "label": label[:100],
            "description": desc.strip()[:100],
            "emoji": emoji.strip() or None,
            "blacklist_roles": blacklist_roles,
            "badge_mode": badge_mode if badge_mode in {"off", "require_badge", "block_badge"} else "off",
            "badge_ids": badge_ids,
            "name_prefix": prefix,
            "open_note": note_raw.strip()[:200],
            "discord_category_id": disc_cat_raw.strip() or None,
            "dropdown_enabled": dd_enabled == "true",
            "variables": variables,
        })

    if not categories:
        if request.accept_mimetypes.best == "application/json":
            return jsonify({"ok": False, "error": "Keep at least one category with a label."}), 400
        flash("❌ Add at least one category with a label.", "danger")
        return redirect(url_for("home", page="carry-settings"))

    fully_saved = len(categories) == len(labels) and len(categories) <= 25
    if len(categories) > 25:
        categories = categories[:25]
        flash("⚠️ Only the first 25 categories were saved (Discord's dropdown limit).", "warning")

    try:
        save_categories_checked(categories)
    except Exception:
        app.logger.exception("Could not save ticket categories")
        if request.accept_mimetypes.best == "application/json":
            return jsonify({"ok": False, "error": "Categories could not be saved. Please retry."}), 503
        flash("Categories could not be saved. Your saved categories were not changed.", "danger")
        return redirect(url_for("home", page="carry-settings"))
    if request.accept_mimetypes.best == "application/json":
        return jsonify({"ok": True, "fully_saved": fully_saved})
    flash("✅ Ticket dropdown categories saved. New panels you deploy will use them; existing panels update after the bot restarts.", "success")
    return redirect(url_for("home", page="carry-settings"))


@app.route("/dashboard/ticket-messages/save-redirect", methods=["POST"])
@carry_manager_required
def save_redirect_message_route():
    content = request.form.get("redirect_content", "").strip()
    if not content:
        flash("❌ Redirect message can't be empty.", "danger")
        return redirect(url_for("home", page="carry-settings"))
    save_redirect_message(content[:1000])
    flash("✅ 'Ticket created' redirect message saved.", "success")
    return redirect(url_for("home", page="carry-settings"))


@app.route("/dashboard/ticket-messages/save-welcome", methods=["POST"])
@carry_manager_required
def save_welcome_message_route():
    use_embed = request.form.get("welcome_use_embed") == "yes"
    data = {
        "use_embed": use_embed,
        "content": request.form.get("welcome_content", "").strip()[:500],
        "title": request.form.get("welcome_title", "").strip()[:256],
        "description": request.form.get("welcome_description", "").strip()[:2000],
        "color": request.form.get("welcome_color", "#3498db").strip() or "#3498db",
        "footer": request.form.get("welcome_footer", "").strip()[:200],
        "show_timezone": request.form.get("welcome_show_timezone") == "yes",
        "show_display_name": request.form.get("welcome_show_display_name") == "yes",
        "show_can_join": request.form.get("welcome_show_can_join") == "yes",
        "show_tds_level": request.form.get("welcome_show_tds_level") == "yes",
    }
    save_welcome_message(data)
    flash("✅ Ticket-channel welcome message saved.", "success")
    return redirect(url_for("home", page="carry-settings"))


@app.route("/dashboard/access/add-carry-manager-role", methods=["POST"])
@admin_required
def access_add_carry_manager_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_carry_manager_role(role_id)
        flash("✅ Role added to Carry Manager Settings access.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/remove-carry-manager-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_carry_manager_role(role_id):
    remove_carry_manager_role(role_id)
    flash("🗑️ Role removed from Carry Manager Settings access.", "info")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/add-transcripts-role", methods=["POST"])
@admin_required
def access_add_transcripts_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_transcripts_role(role_id)
        flash("✅ Role added to Transcripts access.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/remove-transcripts-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_transcripts_role(role_id):
    remove_transcripts_role(role_id)
    flash("🗑️ Role removed from Transcripts access.", "info")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/add-analytics-role", methods=["POST"])
@admin_required
def access_add_analytics_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_analytics_role(role_id)
        flash("✅ Role added to Analytics access.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/remove-analytics-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_analytics_role(role_id):
    remove_analytics_role(role_id)
    flash("🗑️ Role removed from Analytics access.", "info")
    return redirect(url_for("home", page="access-control"))


def _queue_native_pin_sync():
    if not bot.is_ready():
        return False
    cog = bot.get_cog("TicketsCog")
    if cog is None:
        return False
    coroutine = cog.sync_native_pin_permissions()
    try:
        future = asyncio.run_coroutine_threadsafe(coroutine, bot.loop)
        def finished(result):
            try:
                summary = result.result()
                app.logger.info("Native ticket pin sync: %s", summary)
            except Exception:
                app.logger.exception("Native ticket pin sync could not run")
        future.add_done_callback(finished)
        return True
    except Exception:
        coroutine.close()
        app.logger.exception("Could not schedule native ticket pin synchronization")
        return False


def _native_pin_saved_notice():
    if _queue_native_pin_sync():
        flash("Pin Messages access saved. Existing ticket channels are updating; failed updates retry automatically.", "success")
    else:
        flash("Pin Messages access saved. Existing tickets will update when the bot is online.", "info")


@app.route("/dashboard/access/add-pin-message-role", methods=["POST"])
@admin_required
def access_add_pin_message_role_route():
    if not _valid_carry_application_csrf():
        abort(400, "Your form expired. Reload Access Control and retry.")
    try:
        pin_permissions.change_roles(add=[request.form.get("role_id", "").strip()])
    except (ValueError, RuntimeError) as error:
        flash(str(error), "danger")
    except Exception:
        app.logger.exception("Could not save native pin role")
        flash("Could not save pin access. Please retry.", "danger")
    else:
        _native_pin_saved_notice()
    return redirect(url_for("home", page="access-control", _anchor="access-message-pinning-access"))


@app.route("/dashboard/access/remove-pin-message-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_pin_message_role_route(role_id):
    if not _valid_carry_application_csrf():
        abort(400, "Your form expired. Reload Access Control and retry.")
    try:
        pin_permissions.change_roles(remove=[role_id])
    except (ValueError, RuntimeError) as error:
        flash(str(error), "danger")
    except Exception:
        app.logger.exception("Could not remove native pin role")
        flash("Could not save pin access. Please retry.", "danger")
    else:
        _native_pin_saved_notice()
    return redirect(url_for("home", page="access-control", _anchor="access-message-pinning-access"))


@app.post("/dashboard/access/sync-pin-permissions")
@admin_required
def access_sync_pin_permissions_route():
    if not _valid_carry_application_csrf():
        abort(400, "Your form expired. Reload Access Control and retry.")
    _native_pin_saved_notice()
    return redirect(url_for("home", page="access-control", _anchor="access-message-pinning-access"))


@app.route("/dashboard/access/add-carry-application-verdict-role", methods=["POST"])
@admin_required
def access_add_carry_application_verdict_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_carry_application_verdict_role(role_id)
        flash("✅ Role can now decide Carry Team applications.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route(
    "/dashboard/access/remove-carry-application-verdict-role/<role_id>",
    methods=["POST"],
)
@admin_required
def access_remove_carry_application_verdict_role(role_id):
    remove_carry_application_verdict_role(role_id)
    flash("🗑️ Role removed from Carry Team application verdict access.", "info")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/carry-application-delivery", methods=["POST"])
@admin_required
def save_carry_application_delivery_route():
    mode = request.form.get("delivery_mode", "").strip()
    channel_id = request.form.get("channel_id", "").strip()
    webhook_url = request.form.get("webhook_url", "").strip()
    clear_webhook = request.form.get("clear_webhook") == "yes"
    if mode not in {"bot", "webhook"}:
        flash("❌ Choose Bot or Webhook delivery.", "danger")
        return redirect(url_for("home", page="access-control"))

    if webhook_url:
        parsed = urlsplit(webhook_url)
        try:
            webhook_port = parsed.port
        except ValueError:
            webhook_port = -1
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {
                "discord.com",
                "canary.discord.com",
                "ptb.discord.com",
            }
            or not re.fullmatch(
                r"/api/webhooks/[0-9]{17,20}/[A-Za-z0-9._-]{60,}",
                parsed.path,
            )
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.query
            or webhook_port not in {None, 443}
        ):
            flash("❌ Enter a valid HTTPS Discord webhook URL.", "danger")
            return redirect(url_for("home", page="access-control"))

    if mode == "bot":
        guild = bot.guilds[0] if bot.guilds else None
        channel = guild.get_channel(int(channel_id)) if guild and channel_id.isdigit() else None
        if channel is None or not isinstance(channel, discord.TextChannel):
            flash("❌ Select a text channel in the connected Carry server.", "danger")
            return redirect(url_for("home", page="access-control"))

    settings = get_settings()
    delivery = dict(settings.get("carry_application_delivery") or {})
    delivery["mode"] = mode
    if mode == "bot":
        delivery["channel_id"] = channel_id
    if clear_webhook:
        delivery.pop("webhook_url", None)
    elif webhook_url:
        delivery["webhook_url"] = webhook_url
    if mode == "webhook" and not delivery.get("webhook_url"):
        flash("❌ Configure a webhook URL before selecting Webhook delivery.", "danger")
        return redirect(url_for("home", page="access-control"))
    try:
        save_carry_application_delivery(delivery)
    except Exception:
        app.logger.exception("Failed to save Carry Team application delivery settings")
        flash("❌ Could not save the application delivery settings.", "danger")
        return redirect(url_for("home", page="access-control"))
    try:
        reset_pending_application_notifications()
    except Exception:
        app.logger.exception("Could not expedite queued Carry application notices")
        flash(
            "✅ Settings saved. Pending notices will retry on their existing schedule.",
            "warning",
        )
        return redirect(url_for("home", page="access-control"))
    flash("✅ Carry Team application delivery settings saved.", "success")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/add-remove-cooldown-role", methods=["POST"])
@admin_required
def access_add_remove_cooldown_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_remove_cooldown_role(role_id)
        flash("✅ Role added to Remove Cooldown access.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/remove-remove-cooldown-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_remove_cooldown_role(role_id):
    remove_remove_cooldown_role(role_id)
    flash("🗑️ Role removed from Remove Cooldown access.", "info")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/set-log-channel", methods=["POST"])
@admin_required
def access_set_log_channel():
    channel_id = request.form.get("log_channel_id", "").strip()
    set_log_channel(channel_id or None)
    flash("✅ Ticket activity log channel updated.", "success")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/set-blacklist-log-channel", methods=["POST"])
@admin_required
def access_set_blacklist_log_channel():
    channel_id = request.form.get("blacklist_log_channel_id", "").strip()
    set_blacklist_log_channel(channel_id or None)
    flash("✅ Blacklist log channel updated.", "success")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/set-warning-log-channel", methods=["POST"])
@admin_required
def access_set_warning_log_channel():
    channel_id = request.form.get("warning_log_channel_id", "").strip()
    set_warning_log_channel(channel_id or None)
    flash("✅ Warning log channel updated.", "success")
    return redirect(url_for("home", page="access-control"))


@app.route("/dashboard/access/save-offline-schedule", methods=["POST"])
@admin_required
def access_save_offline_schedule():
    """Save the per-day Discord offline schedule from Access Control."""
    raw = request.form.get("offline_schedule_json", "").strip()
    try:
        schedule = json.loads(raw) if raw else {}
        if not isinstance(schedule, dict):
            raise ValueError("Schedule must be an object.")
        timezone = str(schedule.get("timezone", "America/New_York")).strip() or "America/New_York"
        ZoneInfo(timezone)
        schedule["timezone"] = timezone
        schedule["enabled"] = bool(schedule.get("enabled", False))
        days = schedule.setdefault("days", {})
        for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
            entries = days.get(day, [])
            if not isinstance(entries, list):
                raise ValueError(f"{day.title()} schedule is invalid.")
            clean = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                start = str(entry.get("start", "")).strip()
                end = str(entry.get("end", "")).strip()
                if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", end):
                    raise ValueError(f"{day.title()} contains an invalid time.")
                clean.append({"enabled": bool(entry.get("enabled", True)), "start": start, "end": end})
            days[day] = clean
        save_offline_schedule(schedule)
        flash("✅ Bot offline schedule saved.", "success")
    except Exception as exc:
        app.logger.exception("Failed to save bot offline schedule")
        flash(f"❌ Could not save the offline schedule: {exc}", "danger")
    return redirect(url_for("home", page="access-control"))


# --- BOT EVENT HANDLERS & RUNNER ---
@bot.event
async def setup_hook():
    """Load Discord cogs relative to this file, regardless of Render's cwd."""
    print("🔧 setup_hook started.", flush=True)
    cogs_dir = Path(__file__).resolve().parent / "cogs"
    print(f"🔧 Cog directory: {cogs_dir}", flush=True)
    if not cogs_dir.is_dir():
        print(f"❌ Cog directory not found: {cogs_dir}", flush=True)
        return

    cog_paths = sorted(cogs_dir.glob("*.py"))
    print(f"🔧 Found {len(cog_paths)} cog files.", flush=True)
    loaded = 0
    failed = 0
    for cog_path in cog_paths:
        name = cog_path.stem
        print(f"🔧 Loading cog: {name}", flush=True)
        try:
            await bot.load_extension(f"cogs.{name}")
            loaded += 1
            print(f"✅ Loaded cog: {name}", flush=True)
        except Exception as exc:
            failed += 1
            print(
                f"❌ Failed to load cog {name}: {type(exc).__name__}: {exc}",
                flush=True,
            )

    print(f"📦 Cog loading complete: {loaded} loaded, {failed} failed.", flush=True)

    # Sync application commands once per process/login, rather than on every
    # gateway reconnect. Repeated on_ready() syncs add unnecessary Discord API
    # traffic and can amplify rate-limit pressure during reconnects.
    print("🔧 Starting slash-command sync.", flush=True)
    try:
        synced = await bot.tree.sync()
        print(f"✅ Slash-command sync complete: {len(synced)} commands.", flush=True)
    except Exception as exc:
        print(f"❌ Slash-command sync failed: {type(exc).__name__}: {exc}", flush=True)

@bot.event
async def on_ready():
    global bot_ready_since
    print("🔧 on_ready started.", flush=True)
    bot_ready_since = time.time()
    # If this process was started by the cooldown/watchdog restart, tell the
    # owner the bot is back (once), then reset the retry counter.
    if int(os.getenv("BOT_START_ATTEMPT", "0") or 0) > 0:
        print("🔧 Processing restart recovery alert.", flush=True)
        os.environ["BOT_START_ATTEMPT"] = "0"
        import threading as _t
        _t.Thread(target=_send_alert, args=("✅ Carry Ticket Bot is back online.",), daemon=True).start()
    print(f"✅ Bot logged in as {bot.user}", flush=True)


def run_flask():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


_offline_schedule_active = False


def _offline_schedule_is_active(now=None, schedule=None):
    """Return True when the current local time falls inside a configured window."""
    if schedule is None:
        schedule = get_offline_schedule()
    if not schedule.get("enabled"):
        return False
    try:
        tz = ZoneInfo(schedule.get("timezone") or "America/New_York")
    except Exception:
        tz = datetime.timezone.utc
    now = now.astimezone(tz) if now else datetime.datetime.now(tz)
    day_names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    current_day = now.weekday()
    current_minutes = now.hour * 60 + now.minute

    def in_entry(entry):
        if not entry.get("enabled", True):
            return False
        try:
            sh, sm = map(int, str(entry.get("start", "00:00")).split(":"))
            eh, em = map(int, str(entry.get("end", "00:00")).split(":"))
        except Exception:
            return False
        start = sh * 60 + sm
        end = eh * 60 + em
        if start == end:
            return True
        if end > start:
            return start <= current_minutes < end
        return current_minutes >= start

    days = schedule.get("days", {})
    for idx in (current_day, (current_day - 1) % 7):
        for entry in days.get(day_names[idx], []) or []:
            if not entry.get("enabled", True):
                continue
            try:
                sh, sm = map(int, str(entry.get("start", "00:00")).split(":"))
                eh, em = map(int, str(entry.get("end", "00:00")).split(":"))
            except Exception:
                continue
            start = sh * 60 + sm
            end = eh * 60 + em
            if idx == current_day and in_entry(entry):
                return True
            if idx != current_day and end < start and current_minutes < end:
                return True
    return False


def run_offline_schedule_watcher():
    """Disconnect Discord during scheduled windows while leaving Flask/status alive."""
    global bot_sleeping, _offline_schedule_active
    while True:
        time.sleep(15)
        try:
            active = _offline_schedule_is_active()
            if active and not _offline_schedule_active and not bot_sleeping:
                _offline_schedule_active = True
                bot_sleeping = True
                print("🕒 Offline schedule active — disconnecting Discord bot; web/status remain online.")
                try:
                    if bot.loop and not bot.loop.is_closed():
                        asyncio.run_coroutine_threadsafe(bot.close(), bot.loop).result(timeout=30)
                except Exception as exc:
                    print(f"Offline schedule disconnect failed: {exc}")
                    bot_sleeping = False
                    _offline_schedule_active = False
            elif not active and _offline_schedule_active:
                print("🕒 Offline schedule ended — restarting process to reconnect Discord bot.")
                os.environ["BOT_START_ATTEMPT"] = "0"
                import sys
                os.execv(sys.executable, [sys.executable] + sys.argv)
        except SystemExit:
            raise
        except Exception as exc:
            print(f"Offline schedule watcher error: {exc}")


def run_status_checker():
    """Runs forever in its own daemon thread, sampling bot connectivity
    roughly once a minute so the /status page has real uptime history and
    auto-detected incidents instead of only a live snapshot."""
    from utils.status_history import record_check
    while True:
        try:
            if bot_sleeping:
                # Deliberate idle sleep, not an outage: don't record a check,
                # so uptime % and incidents aren't polluted.
                time.sleep(60)
                continue
            online = bool(bot.is_ready()) and not bot.is_closed()
            record_check(online)
        except Exception as e:
            print(f"Status checker error: {e}")
        time.sleep(60)


import threading

_cooldown_lock = threading.Lock()
_cooldown_active = False
last_online_at = None  # updated by the watchdog whenever the bot is connected


def _retry_after_from(exc):
    """Seconds Discord asked us to wait (Retry-After header), if any."""
    try:
        value = exc.response.headers.get("Retry-After")
        return int(float(value)) if value else 0
    except Exception:
        return 0


def _send_alert(text):
    """Optional owner alert through a service that does NOT depend on Discord
    (a Discord message would likely be blocked by the same IP block we're
    reporting). Does nothing unless ALERT_WEBHOOK_URL is set. Works with
    ntfy.sh topics (plain text body) or any webhook taking JSON with a
    "content"/"text" field (Discord/Slack style)."""
    url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return
    try:
        import requests
        if "ntfy" in url:
            requests.post(url, data=text.encode("utf-8"), headers={"Title": "Carry Ticket Bot"}, timeout=10)
        else:
            requests.post(url, json={"content": text, "text": text}, timeout=10)
    except Exception as e:
        print(f"Alert failed to send: {e}")


def _current_attempt():
    # If this process has ever been connected, a fresh failure starts the
    # backoff over; otherwise carry on from the count passed through restarts.
    if last_online_at or bot_ready_since:
        return 0
    return int(os.getenv("BOT_START_ATTEMPT", "0") or 0)


def _cooldown_and_restart(reason, exc=None, can_recover=False):
    """Switch the dashboard off (status pages stay up), alert the owner, wait
    with growing backoff, then restart the process to retry the Discord login.

    Shared by the startup-failure path and the watchdog; only one caller can
    run it at a time (returns False if another is already handling it).
    With can_recover=True, if the bot reconnects by itself during the wait the
    cooldown is cancelled instead of restarting. Returns True in that case."""
    import sys
    global web_lockdown_until, last_start_error, _cooldown_active
    with _cooldown_lock:
        if _cooldown_active:
            return False
        _cooldown_active = True
    attempt = _current_attempt()
    base = int(os.getenv("BOT_RETRY_BASE_SECONDS", "120"))
    cap = int(os.getenv("BOT_RETRY_MAX_SECONDS", "3600"))
    # discord.py already queues ordinary route/global limits internally.
    # This outer guard is for prolonged REST restrictions and failed startup:
    # always honor Discord's Retry-After and add a small safety margin before
    # making a fresh login attempt. Never shorten a server-requested wait.
    retry_after = _retry_after_from(exc) if exc else 0
    known_api_wait = max(0, api_block_until - time.time())
    server_wait = max(retry_after, known_api_wait)
    delay = max(min(base * (2 ** attempt), cap), server_wait + (5 if server_wait > 0 else 0))
    last_start_error = reason
    web_lockdown_until = time.time() + delay
    print(f"❌ {reason}")
    print(f"⏸️ Dashboard disabled, status stays up. Retrying Discord login in {delay}s (attempt {attempt + 1}).")
    _send_alert(f"⚠️ Carry Ticket Bot is down: {reason}. Dashboard is off; retrying in about {max(1, round(delay / 60))} min.")
    deadline = time.time() + delay
    while time.time() < deadline:
        time.sleep(min(15, max(0, deadline - time.time())))
        if can_recover and bot.is_ready() and not bot.is_closed():
            web_lockdown_until = 0.0
            with _cooldown_lock:
                _cooldown_active = False
            print("✅ Bot reconnected on its own; cooldown cancelled, dashboard back on.")
            _send_alert("✅ Carry Ticket Bot reconnected on its own.")
            return True
    os.environ["BOT_START_ATTEMPT"] = str(attempt + 1)
    print("🔄 Restarting process to retry Discord login...")
    os.execv(sys.executable, [sys.executable] + sys.argv)


def run_bot_with_cooldown():
    """Run the bot. If startup/login fails, keep the process (and status
    pages) alive and retry after a backoff instead of exiting, so the host
    isn't tempted into an instant restart loop that keeps the API block alive."""
    try:
        bot.run(TOKEN)
    except discord.HTTPException as exc:
        if getattr(exc, "status", None) == 429:
            retry_after = _retry_after_from(exc)
            reason = (
                f"Discord login rate-limited (HTTP 429; "
                f"Retry-After={retry_after}s)"
            )
            handled = _cooldown_and_restart(reason, exc=exc)
        else:
            handled = _cooldown_and_restart(
                f"Bot stopped: {type(exc).__name__}: {exc}", exc=exc
            )
    except Exception as exc:
        handled = _cooldown_and_restart(f"Bot stopped: {type(exc).__name__}: {exc}", exc=exc)
        if handled is False:
            # The watchdog is already running the cooldown; just stay alive
            # (keeping the web/status threads running) until it restarts us.
            while True:
                time.sleep(3600)
    if bot_sleeping:
        # bot.run() returned because the idle sleeper closed the bot. Keep the
        # process (and the web/status threads) alive until /api/wake restarts it.
        print("💤 Bot is asleep; web and status stay up. Waiting for a wake request.")
        while True:
            time.sleep(3600)
    # otherwise bot.run() returned normally (e.g. Ctrl+C locally): a deliberate stop.


def run_watchdog():
    """Background check for a bot that is running but stuck offline (e.g. a
    Discord block that starts mid-run, where discord.py just keeps retrying
    forever). If the bot stays offline past a grace period, run the same
    cooldown + restart as a failed startup. Short drops are ignored, and if
    the bot reconnects by itself the cooldown is cancelled."""
    global last_online_at
    grace = int(os.getenv("WATCHDOG_OFFLINE_SECONDS", "300"))
    offline_since = time.time()  # process start counts as offline until ready
    while True:
        time.sleep(30)
        try:
            if bot_sleeping:
                offline_since = None  # deliberate idle sleep, not a failure
                continue
            now = time.time()
            if bool(bot.is_ready()) and not bot.is_closed():
                last_online_at = now
                offline_since = None
                continue
            if offline_since is None:
                offline_since = now
            # Do not recycle the process while Discord's own 429 response
            # says the REST API is blocked. A restart cannot clear an IP-level
            # restriction and can create another immediate login attempt.
            if now < api_block_until:
                remaining = max(1, int(api_block_until - now))
                print(
                    f"🛡️ API Guard: watchdog holding restart for {remaining}s "
                    "until the Discord Retry-After window expires.",
                    flush=True,
                )
                continue
            if now - offline_since >= grace and not _cooldown_active:
                minutes = max(1, round((now - offline_since) / 60))
                if _cooldown_and_restart(f"Bot offline for {minutes} min", can_recover=True):
                    offline_since = None  # recovered without a restart
        except SystemExit:
            raise
        except Exception as e:
            print(f"Watchdog error: {e}")


async def _note_activity(interaction):
    """Any interaction with the bot (panel dropdown/buttons, ticket buttons,
    modals, slash commands) counts as activity for the idle-sleep timer."""
    global last_panel_activity
    last_panel_activity = time.time()


bot.add_listener(_note_activity, "on_interaction")


def _idle_sleep_hours():
    try:
        return float(os.getenv("IDLE_SLEEP_HOURS", "0") or 0)
    except ValueError:
        return 0.0


_last_ticket_read_error_log = 0.0


def _open_ticket_count():
    """Number of open tickets, or None if it can't be determined."""
    global _last_ticket_read_error_log
    try:
        from utils.storage import get_tickets_data
        return len(get_tickets_data().get("active_tickets", []))
    except Exception as e:
        # Called every minute once the bot is idle long enough, so during a
        # database outage only log this once an hour instead of every minute.
        if time.time() - _last_ticket_read_error_log >= 3600:
            _last_ticket_read_error_log = time.time()
            print(f"Idle sleeper: couldn't read open tickets, staying awake: {e}")
        return None


def _go_to_sleep(idle_seconds):
    global bot_sleeping
    hours = round(idle_seconds / 3600, 1)
    print(f"💤 No bot activity for {hours}h and no open tickets - going to sleep.")
    bot_sleeping = True
    try:
        asyncio.run_coroutine_threadsafe(bot.close(), bot.loop).result(timeout=30)
    except Exception as e:
        print(f"Idle sleep failed, staying awake: {e}")
        bot_sleeping = False
        return
    _send_alert(f"💤 Carry Ticket Bot went to sleep after {hours}h without activity. Wake it from the status page when needed.")


def run_idle_sleeper():
    """Disconnect the bot after IDLE_SLEEP_HOURS of no interactions, as long
    as no tickets are open (close/claim buttons need the bot). Off unless the
    IDLE_SLEEP_HOURS env var is set to a positive number."""
    hours = _idle_sleep_hours()
    if hours <= 0:
        return
    print(f"💤 Idle sleep enabled: bot will disconnect after {hours}h without activity (and no open tickets).")
    while True:
        time.sleep(60)
        try:
            if bot_sleeping or _cooldown_active:
                continue
            if not (bot.is_ready() and not bot.is_closed()):
                continue
            starts = [t for t in (last_panel_activity, bot_ready_since) if t]
            if not starts:
                continue
            idle = time.time() - max(starts)
            if idle < hours * 3600:
                continue
            open_tickets = _open_ticket_count()
            if open_tickets != 0:  # open tickets, or unknown: stay awake
                continue
            _go_to_sleep(idle)
        except Exception as e:
            print(f"Idle sleeper error: {e}")


if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    status_thread = threading.Thread(target=run_status_checker, daemon=True)
    status_thread.start()

    watchdog_thread = threading.Thread(target=run_watchdog, daemon=True)
    watchdog_thread.start()

    idle_thread = threading.Thread(target=run_idle_sleeper, daemon=True)
    idle_thread.start()
    offline_schedule_thread = threading.Thread(target=run_offline_schedule_watcher, daemon=True)
    offline_schedule_thread.start()

    if TOKEN:
        # Give Render and the web/status threads a short head start after every
        # deploy before opening a Discord connection. This keeps the bot
        # visibly offline for a minute while the deployment settles and avoids
        # an immediate burst of Discord API traffic on process startup.
        try:
            startup_delay = max(0, int(os.getenv("BOT_DEPLOY_START_DELAY_SECONDS", "60") or 60))
        except ValueError:
            startup_delay = 60

        if startup_delay:
            print(
                f"⏸️ Deployment startup delay: Discord bot will stay offline "
                f"for {startup_delay}s before connecting.",
                flush=True,
            )
            deadline = time.monotonic() + startup_delay
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(1, remaining))
            print("▶️ Deployment startup delay finished; starting Discord bot.", flush=True)

        run_bot_with_cooldown()
    else:
        print("❌ Error: DISCORD_BOT_TOKEN environment variable is missing.")



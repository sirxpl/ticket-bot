import os
import glob
import json
import asyncio
import time
from functools import wraps
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
    send_from_directory
)
from requests_oauthlib import OAuth2Session

# Import storage helpers
from utils.storage import (
    get_tickets_data, 
    get_blacklist_data, 
    remove_from_blacklist, 
    TRANSCRIPTS_DIR,
    get_settings,
    set_tickets_enabled,
    get_ticket_logs,
    get_logs_for_ticket,
    get_transcript_info,
    get_transcript_html,
    list_transcript_filenames,
    get_ticket_categories,
    save_ticket_categories,
    get_ticket_panel_draft,
    save_ticket_panel_draft,
    get_redirect_message,
    save_redirect_message,
    get_welcome_message,
    save_welcome_message,
)

# Import access-control helpers
from utils.access import (
    get_access_settings,
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
    add_basic_command_role,
    remove_basic_command_role,
    add_basic_command_user,
    remove_basic_command_user,
    set_log_channel,
    set_blacklist_log_channel,
    has_dashboard_access,
    has_carry_manager_access,
    is_admin,
)

# Environment & OAuth Setup
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("OAUTH2_REDIRECT_URI", "https://ticket-bot-f184.onrender.com/callback")

AUTHORIZATION_BASE_URL = 'https://discord.com/api/oauth2/authorize'
TOKEN_URL = 'https://discord.com/api/oauth2/token'

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

# Flask App
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey123")

# Discord Bot
intents = discord.Intents.default()
# NOTE: message_content and members are privileged intents. This app is now
# past Discord's "high user count" threshold and can't self-toggle them until
# the Privileged Intents review is approved - temporarily disabled so the bot
# can start. Ticket blacklist-role checks still work fine without these (they
# read interaction.user.roles from the interaction payload, not the member
# cache). What's degraded: transcript message text will save blank, and the
# dashboard's "members blocked by role" preview list will be empty. Re-enable
# both the moment the intents review is approved.
intents.message_content = False
intents.members = False
bot = commands.Bot(command_prefix="!", intents=intents)

# Tracks when the bot last became ready, used by the public /status page and
# /api/status JSON endpoint below. None means it hasn't connected yet since
# this process started.
bot_ready_since = None


def make_oauth_session(state=None):
    return OAuth2Session(
        client_id=CLIENT_ID,
        state=state,
        scope=['identify', 'guilds', 'guilds.members.read'],
        redirect_uri=REDIRECT_URI
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


def access_required(f):
    """Like login_required, but also enforces the Access Control allow-list."""
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


def admin_required(f):
    """Restricts a route to admins only (see utils.access.is_admin) — used
    for the Access Control page and its management routes."""
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
        if not is_admin(user_data["id"]):
            flash("⛔ Access Control is restricted to admins only.", "danger")
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return decorated_function


# --- FLASK ROUTES ---
@app.route("/privacy")
def privacy_policy():
    return render_template("privacy.html")


@app.route("/docs")
def docs():
    return render_template("docs.html")


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
    return jsonify({
        "online": online,
        "latency_ms": latency_ms,
        "uptime_seconds": uptime_seconds,
        "guild_count": guild_count,
    })


@app.route("/api/status-history")
def api_status_history():
    from utils.status_history import get_daily_uptime, get_overall_uptime_pct, get_incidents_by_month
    return jsonify({
        "daily": get_daily_uptime(90),
        "overall_uptime_pct": get_overall_uptime_pct(90),
        "months": get_incidents_by_month(3),
    })


@app.route("/status")
def status_page():
    return render_template("status.html")


@app.route("/")
def home():
    user_data = session.get("user", None)
    if not user_data:
        return render_template("dashboard.html", user=None, panel_draft={}, redirect_message={"content": ""}, welcome_message={})

    role_ids = get_member_role_ids(user_data["id"])
    if not has_dashboard_access(user_data["id"], role_ids):
        flash("⛔ You don't have permission to view this dashboard. Ask an admin to add your user ID or role in Access Control.", "danger")
        session.pop("user", None)
        return redirect(url_for("home"))

    guild = bot.guilds[0] if bot.guilds else None
    channels = guild.text_channels if guild else []
    categories = guild.categories if guild else []
    roles = guild.roles if guild else []
    
    tickets_info = get_tickets_data()
    blacklist_info = get_blacklist_data()
    settings = get_settings()
    access_settings = get_access_settings()

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

    is_admin_user = is_admin(user_data["id"])
    can_access_carry_settings = has_carry_manager_access(user_data["id"], role_ids)

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

    basic_command_roles = []
    for rid in access_settings.get("basic_command_roles", []):
        role = guild.get_role(int(rid)) if guild else None
        basic_command_roles.append({"id": rid, "name": role.name if role else None})
    basic_command_users = access_settings.get("basic_command_users", [])

    # members who are blocked from creating tickets via a Ticket Blacklist Role
    # (in addition to the individually-blacklisted user IDs above)
    role_blacklisted_members = []
    if guild:
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

    transcripts = [
        get_transcript_info(fn) for fn in list_transcript_filenames()
    ]

    return render_template(
        "dashboard.html",
        user=user_data,
        channels=channels,
        categories=categories,
        roles=roles,
        tickets_enabled=settings.get("tickets_enabled", True),
        total_tickets=tickets_info.get("ticket_counter", 0),
        active_tickets=tickets_info.get("active_tickets", []),
        transcripts=transcripts,
        cooldowns=tickets_info.get("cooldowns", []),
        blacklisted_users=blacklist_info.get("blacklisted_users", []),
        role_blacklisted_members=role_blacklisted_members,
        allowed_users=allowed_users,
        allowed_roles=allowed_roles,
        blacklist_roles=blacklist_roles,
        ticket_viewer_roles=ticket_viewer_roles,
        powerful_command_roles=powerful_command_roles,
        powerful_command_users=powerful_command_users,
        basic_command_roles=basic_command_roles,
        basic_command_users=basic_command_users,
        carry_manager_roles=carry_manager_roles,
        is_admin_user=is_admin_user,
        can_access_carry_settings=can_access_carry_settings,
        ticket_categories=get_ticket_categories(),
        panel_draft=get_ticket_panel_draft(),
        redirect_message=get_redirect_message(),
        welcome_message=get_welcome_message(),
        log_channel_id=access_settings.get("log_channel_id"),
        blacklist_log_channel_id=access_settings.get("blacklist_log_channel_id")
    )


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
        authorization_response=request.url
    )
    session['oauth2_token'] = token
    
    user_data = discord_sess.get('https://discord.com/api/users/@me').json()
    user_id = user_data.get('id')
    avatar_hash = user_data.get('avatar')
    
    avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png" if avatar_hash else "https://cdn.discordapp.com/embed/avatars/0.png"

    session['user'] = {
        'username': user_data.get('username'),
        'id': user_id,
        'avatar_url': avatar_url
    }
    
    flash(f"👋 Welcome back, {user_data.get('username')}!", "success")
    return redirect(url_for('home'))


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for('home'))


@app.route("/dashboard/toggle-tickets", methods=["POST"])
@carry_manager_required
def toggle_tickets():
    is_enabled = request.form.get("tickets_enabled") in ["on", "true", "True"]
    set_tickets_enabled(is_enabled)
    
    status_text = "enabled" if is_enabled else "disabled"
    flash(f"⚙️ Ticket creation has been {status_text}.", "success" if is_enabled else "warning")
    return redirect("/")


@app.route("/transcripts/<path:filename>")
def get_transcript(filename):
    # Allow access with a valid short-lived token (for DMed links); otherwise require login
    from flask import request, abort, Response

    def _serve():
        html = get_transcript_html(filename)
        if html is None:
            abort(404)
        return Response(html, mimetype="text/html")

    token = request.args.get('token')
    if token:
        from utils.storage import verify_transcript_token
        info = verify_transcript_token(token)
        if not info or info.get('filename') != filename:
            abort(403)
        return _serve()

    # no token, require logged-in session
    if not session.get('user'):
        flash("🔒 Please log in with Discord to access the transcript.", "warning")
        return redirect(url_for('login'))
    return _serve()


@app.route("/tickets/logs")
@access_required
def view_ticket_logs():
    logs = get_ticket_logs()
    # sort descending
    logs = sorted(logs, key=lambda l: l.get("timestamp", ""), reverse=True)
    return render_template("ticket_logs.html", logs=logs)


# Temporary unauthenticated debug endpoint to inspect ticket logs quickly
@app.route("/debug/ticket-logs")
def debug_ticket_logs():
    try:
        logs = get_ticket_logs()
        # return last 100 entries
        last = sorted(logs, key=lambda l: l.get("timestamp", ""), reverse=True)[:100]
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
@access_required
def api_unblacklist(user_id):
    remove_from_blacklist(user_id)
    return jsonify({"success": True})


@app.route("/api/remove-cooldown/<user_id>", methods=["POST"])
@access_required
def api_remove_cooldown(user_id):
    from utils.storage import remove_cooldown
    ok = remove_cooldown(user_id)
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
    return redirect("/")


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
        return redirect("/")

    title = draft["title"]
    description = draft["description"]
    embed_color = draft["embed_color"]
    image_url = draft["image_url"]
    thumbnail_url = draft["thumbnail_url"]
    footer_text = draft["footer_text"]
    fields = draft["fields"]
    components = draft.get("components") or []

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
                components=components,
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

    return redirect("/")


@app.route("/dashboard/access/add-user", methods=["POST"])
@admin_required
def access_add_user():
    user_id = request.form.get("user_id", "").strip()
    if user_id.isdigit():
        add_allowed_user(user_id)
        flash("✅ User added to the dashboard allow-list.", "success")
    else:
        flash("❌ Please enter a valid numeric Discord user ID.", "danger")
    return redirect(url_for("home"))


@app.route("/dashboard/access/remove-user/<user_id>", methods=["POST"])
@admin_required
def access_remove_user(user_id):
    remove_allowed_user(user_id)
    flash("🗑️ User removed from the allow-list.", "info")
    return redirect(url_for("home"))


@app.route("/dashboard/access/add-role", methods=["POST"])
@admin_required
def access_add_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_allowed_role(role_id)
        flash("✅ Role added to the dashboard allow-list.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home"))


@app.route("/dashboard/access/remove-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_role(role_id):
    remove_allowed_role(role_id)
    flash("🗑️ Role removed from the allow-list.", "info")
    return redirect(url_for("home"))


@app.route("/dashboard/access/add-blacklist-role", methods=["POST"])
@admin_required
def access_add_blacklist_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_blacklist_role(role_id)
        flash("✅ Role added to the Ticket Blacklist.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home"))


@app.route("/dashboard/access/remove-blacklist-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_blacklist_role(role_id):
    remove_blacklist_role(role_id)
    flash("🗑️ Role removed from the Ticket Blacklist.", "info")
    return redirect(url_for("home"))


@app.route("/dashboard/access/add-viewer-role", methods=["POST"])
@admin_required
def access_add_viewer_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_ticket_viewer_role(role_id)
        flash("✅ Role added as a Ticket Viewer Role.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home"))


@app.route("/dashboard/access/remove-viewer-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_viewer_role(role_id):
    remove_ticket_viewer_role(role_id)
    flash("🗑️ Role removed from Ticket Viewer Roles.", "info")
    return redirect(url_for("home"))


@app.route("/dashboard/access/add-powerful-command-role", methods=["POST"])
@admin_required
def access_add_powerful_command_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_powerful_command_role(role_id)
        flash("✅ Role added to Powerful Command Access.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home"))


@app.route("/dashboard/access/remove-powerful-command-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_powerful_command_role(role_id):
    remove_powerful_command_role(role_id)
    flash("🗑️ Role removed from Powerful Command Access.", "info")
    return redirect(url_for("home"))


@app.route("/dashboard/access/add-powerful-command-user", methods=["POST"])
@admin_required
def access_add_powerful_command_user():
    user_id = request.form.get("user_id", "").strip()
    if user_id.isdigit():
        add_powerful_command_user(user_id)
        flash("✅ User added to Powerful Command Access.", "success")
    else:
        flash("❌ Please enter a valid user ID.", "danger")
    return redirect(url_for("home"))


@app.route("/dashboard/access/remove-powerful-command-user/<user_id>", methods=["POST"])
@admin_required
def access_remove_powerful_command_user(user_id):
    remove_powerful_command_user(user_id)
    flash("🗑️ User removed from Powerful Command Access.", "info")
    return redirect(url_for("home"))


@app.route("/dashboard/access/add-basic-command-role", methods=["POST"])
@admin_required
def access_add_basic_command_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_basic_command_role(role_id)
        flash("✅ Role added to Basic Command Access.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home"))


@app.route("/dashboard/access/remove-basic-command-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_basic_command_role(role_id):
    remove_basic_command_role(role_id)
    flash("🗑️ Role removed from Basic Command Access.", "info")
    return redirect(url_for("home"))


@app.route("/dashboard/access/add-basic-command-user", methods=["POST"])
@admin_required
def access_add_basic_command_user():
    user_id = request.form.get("user_id", "").strip()
    if user_id.isdigit():
        add_basic_command_user(user_id)
        flash("✅ User added to Basic Command Access.", "success")
    else:
        flash("❌ Please enter a valid user ID.", "danger")
    return redirect(url_for("home"))


@app.route("/dashboard/access/remove-basic-command-user/<user_id>", methods=["POST"])
@admin_required
def access_remove_basic_command_user(user_id):
    remove_basic_command_user(user_id)
    flash("🗑️ User removed from Basic Command Access.", "info")
    return redirect(url_for("home"))


@app.route("/dashboard/ticket-categories/save", methods=["POST"])
@carry_manager_required
def save_ticket_categories_route():
    labels = request.form.getlist("cat_label")
    descriptions = request.form.getlist("cat_description")
    emojis = request.form.getlist("cat_emoji")
    blacklist_roles_raw = request.form.getlist("cat_blacklist_roles")
    name_prefixes = request.form.getlist("cat_name_prefix")
    open_notes = request.form.getlist("cat_open_note")
    discord_category_ids = request.form.getlist("cat_discord_category_id")

    # these lists aren't guaranteed to line up 1:1 with the other lists
    # (older cached pages, etc.) so pad them out defensively
    for lst in (blacklist_roles_raw, name_prefixes, open_notes, discord_category_ids):
        while len(lst) < len(labels):
            lst.append("")

    from utils.storage import slugify

    categories = []
    for label, desc, emoji, bl_raw, prefix_raw, note_raw, disc_cat_raw in zip(
        labels, descriptions, emojis, blacklist_roles_raw,
        name_prefixes, open_notes, discord_category_ids,
    ):
        label = label.strip()
        if not label:
            continue
        blacklist_roles = [r.strip() for r in bl_raw.split(",") if r.strip()]
        prefix = slugify(prefix_raw.strip() or label)
        categories.append({
            "label": label[:100],
            "description": desc.strip()[:100],
            "emoji": emoji.strip() or None,
            "blacklist_roles": blacklist_roles,
            "name_prefix": prefix,
            "open_note": note_raw.strip()[:200],
            "discord_category_id": disc_cat_raw.strip() or None,
        })

    if not categories:
        flash("❌ Add at least one category with a label.", "danger")
        return redirect(url_for("home"))

    if len(categories) > 25:
        categories = categories[:25]
        flash("⚠️ Only the first 25 categories were saved (Discord's dropdown limit).", "warning")

    save_ticket_categories(categories)
    flash("✅ Ticket dropdown categories saved. New panels you deploy will use them; existing panels update after the bot restarts.", "success")
    return redirect(url_for("home"))


@app.route("/dashboard/ticket-messages/save-redirect", methods=["POST"])
@carry_manager_required
def save_redirect_message_route():
    content = request.form.get("redirect_content", "").strip()
    if not content:
        flash("❌ Redirect message can't be empty.", "danger")
        return redirect(url_for("home"))
    save_redirect_message(content[:1000])
    flash("✅ 'Ticket created' redirect message saved.", "success")
    return redirect(url_for("home"))


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
    }
    save_welcome_message(data)
    flash("✅ Ticket-channel welcome message saved.", "success")
    return redirect(url_for("home"))


@app.route("/dashboard/access/add-carry-manager-role", methods=["POST"])
@admin_required
def access_add_carry_manager_role():
    role_id = request.form.get("role_id", "").strip()
    if role_id.isdigit():
        add_carry_manager_role(role_id)
        flash("✅ Role added to Carry Manager Settings access.", "success")
    else:
        flash("❌ Please select a valid role.", "danger")
    return redirect(url_for("home"))


@app.route("/dashboard/access/remove-carry-manager-role/<role_id>", methods=["POST"])
@admin_required
def access_remove_carry_manager_role(role_id):
    remove_carry_manager_role(role_id)
    flash("🗑️ Role removed from Carry Manager Settings access.", "info")
    return redirect(url_for("home"))


@app.route("/dashboard/access/set-log-channel", methods=["POST"])
@admin_required
def access_set_log_channel():
    channel_id = request.form.get("log_channel_id", "").strip()
    set_log_channel(channel_id or None)
    flash("✅ Ticket activity log channel updated.", "success")
    return redirect(url_for("home"))


@app.route("/dashboard/access/set-blacklist-log-channel", methods=["POST"])
@admin_required
def access_set_blacklist_log_channel():
    channel_id = request.form.get("blacklist_log_channel_id", "").strip()
    set_blacklist_log_channel(channel_id or None)
    flash("✅ Blacklist log channel updated.", "success")
    return redirect(url_for("home"))


# --- BOT EVENT HANDLERS & RUNNER ---
@bot.event
async def setup_hook():
    if os.path.exists("cogs"):
        for filename in os.listdir("cogs"):
            if filename.endswith(".py"):
                await bot.load_extension(f"cogs.{filename[:-3]}")

@bot.event
async def on_ready():
    global bot_ready_since
    bot_ready_since = time.time()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    print(f"✅ Bot logged in as {bot.user}")


def run_flask():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


def run_status_checker():
    """Runs forever in its own daemon thread, sampling bot connectivity
    roughly once a minute so the /status page has real uptime history and
    auto-detected incidents instead of only a live snapshot."""
    from utils.status_history import record_check
    while True:
        try:
            online = bool(bot.is_ready()) and not bot.is_closed()
            record_check(online)
        except Exception as e:
            print(f"Status checker error: {e}")
        time.sleep(60)


if __name__ == "__main__":
    import threading
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    status_thread = threading.Thread(target=run_status_checker, daemon=True)
    status_thread.start()

    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ Error: DISCORD_BOT_TOKEN environment variable is missing.")

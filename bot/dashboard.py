# dashboard.py
import os
import glob
from flask import Flask, redirect, url_for, session, render_template_string, request, jsonify, send_from_directory
from requests_oauthlib import OAuth2Session
from utils.storage import get_tickets_data, get_blacklist_data, remove_from_blacklist, TRANSCRIPTS_DIR

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey123")

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("OAUTH2_REDIRECT_URI", "https://ticket-bot-f184.onrender.com/callback")
AUTHORIZATION_BASE_URL = 'https://discord.com/api/oauth2/authorize'
TOKEN_URL = 'https://discord.com/api/oauth2/token'

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

# Global variable to pass active Discord ticket channels from main.py
active_tickets_cache = []

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Carry Bot Control Panel</title>
    <style>
        * { box-sizing: border-box; }
        body { background-color: #0d1321; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 35px 20px; }
        .container { max-width: 950px; margin: 0 auto; }
        
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px; }
        .title { font-size: 26px; font-weight: 800; display: flex; align-items: center; gap: 10px; }
        .status-badge { background-color: #10b981; color: #022c22; padding: 5px 14px; border-radius: 20px; font-weight: 700; font-size: 14px; display: inline-flex; align-items: center; gap: 6px; }
        
        .user-nav { display: flex; align-items: center; gap: 12px; font-weight: 600; font-size: 15px; }
        .btn { padding: 8px 16px; border-radius: 6px; text-decoration: none; font-weight: 700; border: none; cursor: pointer; transition: 0.2s; font-size: 14px; }
        .btn-login { background-color: #5865f2; color: white; display: inline-flex; align-items: center; gap: 8px; }
        .btn-login:hover { background-color: #4752c4; }
        .btn-logout { background-color: #ef4444; color: white; }
        .btn-logout:hover { background-color: #dc2626; }
        .btn-discord { background-color: #5865f2; color: white; padding: 6px 14px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 13px; }
        .btn-unblacklist { background-color: #10b981; color: #022c22; padding: 6px 14px; border-radius: 6px; border: none; font-weight: 700; cursor: pointer; }

        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 25px; }
        .card { background-color: #1a2333; border-radius: 12px; padding: 22px; }
        .card-title { font-size: 12px; color: #94a3b8; font-weight: 800; letter-spacing: 0.5px; margin-bottom: 8px; text-transform: uppercase; }
        .card-value { font-size: 38px; font-weight: 900; color: #38bdf8; }

        .panel { background-color: #1a2333; border-radius: 12px; padding: 22px; margin-bottom: 22px; }
        .panel-header { font-size: 18px; font-weight: 700; margin-top: 0; margin-bottom: 16px; display: flex; align-items: center; gap: 10px; }
        
        .item-box { background-color: #101726; border-radius: 8px; padding: 14px 18px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .item-text { font-weight: 600; font-size: 15px; color: #e2e8f0; }
        .empty-text { color: #64748b; font-size: 14px; margin: 0; }

        .gate-box { text-align: center; padding: 60px 20px; background-color: #1a2333; border-radius: 12px; margin-top: 30px; }
        .gate-box h2 { font-size: 24px; margin-bottom: 10px; }
        .gate-box p { color: #94a3b8; max-width: 500px; margin: 0 auto 25px auto; line-height: 1.5; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <div class="title">🎫 Carry Bot Control Panel</div>
                <div style="margin-top: 8px;">
                    <span class="status-badge">🟢 Bot Online</span>
                </div>
            </div>
            <div class="user-nav">
                {% if user %}
                    <span>Welcome, <strong>{{ user.username }}</strong>!</span>
                    <a href="/logout" class="btn btn-logout">Logout</a>
                {% else %}
                    <a href="/login" class="btn btn-login">Login with Discord</a>
                {% endif %}
            </div>
        </div>

        {% if user %}
            <div class="grid-2">
                <div class="card">
                    <div class="card-title">TOTAL TICKETS CREATED</div>
                    <div class="card-value">#{{ "%04d" % total_tickets }}</div>
                </div>
                <div class="card">
                    <div class="card-title">ACTIVE USER COOLDOWNS</div>
                    <div class="card-value">0</div>
                </div>
            </div>

            <div class="panel">
                <div class="panel-header">📡 Live Active Ticket Channels</div>
                {% if active_tickets %}
                    {% for ticket in active_tickets %}
                        <div class="item-box">
                            <span class="item-text">#{{ ticket.name }}</span>
                            <a href="https://discord.com/channels/{{ ticket.guild_id }}/{{ ticket.id }}" target="_blank" class="btn-discord">↗ Open in Discord</a>
                        </div>
                    {% endfor %}
                {% else %}
                    <p class="empty-text">No active ticket channels right now.</p>
                {% endif %}
            </div>

            <div class="panel">
                <div class="panel-header">📁 Past Closed Tickets & Message Logs</div>
                {% if transcripts %}
                    {% for file in transcripts %}
                        <div class="item-box">
                            <span class="item-text">📜 {{ file }}</span>
                            <a href="/transcripts/{{ file }}" target="_blank" class="btn-discord">View Log</a>
                        </div>
                    {% endfor %}
                {% else %}
                    <p class="empty-text">No saved ticket transcripts yet.</p>
                {% endif %}
            </div>

            <div class="panel">
                <div class="panel-header">⏳ Users Currently on Cooldown</div>
                <p class="empty-text">No users on cooldown</p>
            </div>

            <div class="panel">
                <div class="panel-header">🚫 Blacklisted Users</div>
                {% if blacklisted_users %}
                    {% for uid in blacklisted_users %}
                        <div class="item-box" id="user-{{ uid }}">
                            <span class="item-text">User ID: {{ uid }}</span>
                            <button class="btn-unblacklist" onclick="unblacklist('{{ uid }}')">✓ Un-blacklist</button>
                        </div>
                    {% endfor %}
                {% else %}
                    <p class="empty-text">No users currently blacklisted.</p>
                {% endif %}
            </div>

        {% else %}
            <div class="gate-box">
                <h2>🔒 Restricted Control Panel</h2>
                <p>Please authorize with Discord to view active ticket channels, past closed logs, and manage user blacklists.</p>
                <a href="/login" class="btn btn-login" style="padding: 12px 24px; font-size: 16px;">🔑 Login with Discord</a>
            </div>
        {% endif %}
    </div>

    <script>
        async function unblacklist(userId) {
            const res = await fetch(`/api/unblacklist/${userId}`, { method: 'POST' });
            if (res.ok) {
                document.getElementById(`user-${userId}`).remove();
            } else {
                alert("Failed to unblacklist user.");
            }
        }
    </script>
</body>
</html>
"""

def make_session(state=None):
    return OAuth2Session(
        client_id=CLIENT_ID,
        state=state,
        scope=['identify', 'guilds.members.read'],
        redirect_uri=REDIRECT_URI
    )

@app.route("/")
def index():
    user = session.get("user")
    tickets_info = get_tickets_data()
    blacklist_info = get_blacklist_data()
    
    # Fetch local HTML transcript logs
    transcripts = [os.path.basename(f) for f in glob.glob(f"{TRANSCRIPTS_DIR}/*.html")]

    return render_template_string(
        HTML_TEMPLATE,
        user=user,
        total_tickets=tickets_info.get("ticket_counter", 0),
        blacklisted_users=blacklist_info.get("blacklisted_users", []),
        active_tickets=active_tickets_cache,
        transcripts=transcripts
    )

@app.route("/login")
def login():
    discord_sess = make_session()
    authorization_url, state = discord_sess.authorization_url(AUTHORIZATION_BASE_URL)
    session['oauth2_state'] = state
    return redirect(authorization_url)

@app.route("/callback")
def callback():
    if request.args.get('error'):
        return request.args['error']
    discord_sess = make_session(state=session.get('oauth2_state'))
    token = discord_sess.fetch_token(
        TOKEN_URL,
        client_secret=CLIENT_SECRET,
        authorization_response=request.url
    )
    session['oauth2_token'] = token
    user_data = discord_sess.get('https://discord.com/api/users/@me').json()
    session['user'] = {'username': user_data.get('username'), 'id': user_data.get('id')}
    return redirect(url_for('index'))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route("/transcripts/<path:filename>")
def get_transcript(filename):
    if not session.get("user"):
        return "Unauthorized", 401
    return send_from_directory(TRANSCRIPTS_DIR, filename)

@app.route("/api/unblacklist/<user_id>", methods=["POST"])
def api_unblacklist(user_id):
    if not session.get("user"):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    remove_from_blacklist(user_id)
    return jsonify({"success": True})

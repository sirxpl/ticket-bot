# dashboard.py
import os
from flask import Flask, redirect, url_for, session, render_template_string, request, jsonify
from requests_oauthlib import OAuth2Session
from utils.storage import get_tickets_data, get_blacklist_data, remove_from_blacklist

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey123")

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("OAUTH2_REDIRECT_URI", "https://ticket-bot-f184.onrender.com/callback")
AUTHORIZATION_BASE_URL = 'https://discord.com/api/oauth2/authorize'
TOKEN_URL = 'https://discord.com/api/oauth2/token'

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Carry Bot Control Panel</title>
    <style>
        body { background-color: #0f172a; color: #f8fafc; font-family: sans-serif; margin: 0; padding: 30px; }
        .container { max-width: 1000px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px; }
        .title { font-size: 28px; font-weight: bold; }
        .badge { background-color: #10b981; color: #022c22; padding: 6px 14px; border-radius: 20px; font-weight: 600; }
        .btn { padding: 8px 16px; border-radius: 6px; text-decoration: none; font-weight: 600; border: none; cursor: pointer; }
        .btn-login { background-color: #5865f2; color: white; display: inline-flex; align-items: center; gap: 8px; font-size: 15px; }
        .btn-logout { background-color: #ef4444; color: white; }
        .btn-action { background-color: #10b981; color: white; padding: 6px 12px; }
        .btn-link { background-color: #3b82f6; color: white; padding: 6px 12px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 25px; }
        .card { background-color: #1e293b; border-radius: 10px; padding: 20px; }
        .card-title { font-size: 12px; color: #94a3b8; font-weight: 700; margin-bottom: 8px; }
        .card-value { font-size: 32px; font-weight: 800; color: #38bdf8; }
        .section { background-color: #1e293b; border-radius: 10px; padding: 20px; margin-bottom: 20px; }
        .list-box { background-color: #0f172a; border-radius: 6px; padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
        .login-gate { text-align: center; padding: 50px 20px; background-color: #1e293b; border-radius: 12px; margin-top: 40px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <span class="title">🎫 Carry Bot Control Panel</span>
                <span class="badge">🟢 Bot Online</span>
            </div>
            <div>
                {% if user %}
                    <span>Logged in as <strong>{{ user.username }}</strong></span>
                    <a href="/logout" class="btn btn-logout" style="margin-left: 10px;">Logout</a>
                {% else %}
                    <a href="/login" class="btn btn-login">Login with Discord</a>
                {% endif %}
            </div>
        </div>

        {% if user %}
            <div class="grid">
                <div class="card">
                    <div class="card-title">TOTAL TICKETS CREATED</div>
                    <div class="card-value">#{{ "%04d" % total_tickets }}</div>
                </div>
                <div class="card">
                    <div class="card-title">BLACKLISTED USERS</div>
                    <div class="card-value" style="color: #f43f5e;">{{ blacklisted_users|length }}</div>
                </div>
            </div>

            <div class="section">
                <h3>🚫 Blacklisted Users</h3>
                {% if blacklisted_users %}
                    {% for uid in blacklisted_users %}
                        <div class="list-box" id="user-{{ uid }}">
                            <span>User ID: <strong>{{ uid }}</strong></span>
                            <button class="btn btn-action" onclick="unblacklist('{{ uid }}')">✓ Un-blacklist</button>
                        </div>
                    {% endfor %}
                {% else %}
                    <p style="color: #64748b;">No blacklisted users found.</p>
                {% endif %}
            </div>
        {% else %}
            <div class="login-gate">
                <h2>🔒 Restricted Area</h2>
                <p style="color: #94a3b8; margin-bottom: 25px;">You must sign in with your authorized Discord account to view active tickets, closed transcripts, and manage blacklisted users.</p>
                <a href="/login" class="btn btn-login" style="padding: 12px 24px;">🔑 Login with Discord</a>
            </div>
        {% endif %}
    </div>

    <script>
        async function unblacklist(userId) {
            const res = await fetch(`/api/unblacklist/${userId}`, { method: 'POST' });
            if (res.ok) { document.getElementById(`user-${userId}`).remove(); } 
            else { alert("Failed to unblacklist user."); }
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
    return render_template_string(
        HTML_TEMPLATE,
        user=user,
        total_tickets=tickets_info.get("ticket_counter", 0),
        blacklisted_users=blacklist_info.get("blacklisted_users", [])
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

@app.route("/api/unblacklist/<user_id>", methods=["POST"])
def api_unblacklist(user_id):
    if not session.get("user"):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    remove_from_blacklist(user_id)
    return jsonify({"success": True})

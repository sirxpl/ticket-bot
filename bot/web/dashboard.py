# web/dashboard.py
import os
from threading import Thread
from flask import Flask, render_template_string, jsonify
from utils.storage import get_tickets_data, get_blacklist_data

app = Flask(__name__)

# Basic HTML Dashboard Template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bot Status Dashboard</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 40px; }
        .card { background-color: #1e293b; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); }
        h1 { color: #38bdf8; margin-top: 0; }
        .stat { font-size: 2rem; font-weight: bold; color: #22c55e; }
        .stat-label { color: #94a3b8; font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.05em; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; }
    </style>
</head>
<body>
    <h1>🤖 Bot Operational Dashboard</h1>
    <div class="grid">
        <div class="card">
            <div class="stat-label">Total Tickets Created</div>
            <div class="stat">{{ ticket_count }}</div>
        </div>
        <div class="card">
            <div class="stat-label">Blacklisted Users</div>
            <div class="stat" style="color: #ef4444;">{{ blacklist_count }}</div>
        </div>
    </div>
    <div class="card">
        <h3>System Status</h3>
        <p>✅ Flask Web Server & Discord Bot are currently online.</p>
    </div>
</body>
</html>
"""

@app.route("/")
def home():
    ticket_data = get_tickets_data()
    blacklist_data = get_blacklist_data()
    
    return render_template_string(
        HTML_TEMPLATE,
        ticket_count=ticket_data.get("ticket_counter", 0),
        blacklist_count=len(blacklist_data.get("blacklisted_users", []))
    )

@app.route("/health")
def health_check():
    return jsonify({"status": "healthy", "service": "discord-bot"}), 200

def run_web_server():
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def start_dashboard():
    server_thread = Thread(target=run_web_server)
    server_thread.daemon = True
    server_thread.start()

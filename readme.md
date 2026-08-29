# 🎫 Carry Ticket Bot

A full ticket & carry-request platform for Discord — category-based tickets, staff commands, transcripts, blacklists, autoclose on inactivity, Linked Roles verification, and a web dashboard for configuring all of it without touching code.

**Repository:** [github.com/sirxpl/ticket-bot](https://github.com/sirxpl/ticket-bot)

---

## 🍴 Fork & Clone

To run your own copy of this bot, fork the repository first rather than cloning directly — that gives you your own copy on GitHub to deploy from and customize.

1. Click **Fork** at the top of [github.com/sirxpl/ticket-bot](https://github.com/sirxpl/ticket-bot) to create your own copy under your GitHub account
2. Clone your fork locally (optional — only needed if you want to edit before deploying):
   ```bash
   git clone https://github.com/YOUR-USERNAME/ticket-bot.git
   cd ticket-bot
   ```
3. When deploying on Render (see [Setup](#-setup) below), connect **your fork**, not the original repo — that way your environment variables, config edits, and future commits all stay separate from the upstream project
4. To pull in updates later without losing your own changes:
   ```bash
   git remote add upstream https://github.com/sirxpl/ticket-bot.git
   git fetch upstream
   git merge upstream/main
   ```

---

## ✨ Features

- **Category-based tickets** — members pick a type from a dropdown or button panel, answer a short form, and get a private channel auto-named for that category
- **Components V2 panel builder** — build multi-embed panels with buttons, dropdowns, and dividers directly from the dashboard; buttons/dropdowns can filter by category tags or show every ticket type
- **Editable ticket messages** — the "ticket created" redirect and the in-channel welcome message both support placeholders (`{user}`, `{category}`, `{timezone}`, etc.)
- **Autoclose** — tickets warn at 12h of inactivity and close at 24h, or close immediately if the opener leaves the server; staff can override per-ticket with `/disableautoclose` / `/enableautoclose`
- **Blacklist system** — block specific users or entire roles from opening tickets, with a separate "Voidcore" role-based blacklist type that restricts specific ticket categories instead of everything
- **Tiered Access Control** — three independent permission tiers (Basic, Powerful, Dangerous commands) each with their own role/user allow-list, on top of Discord's own Manage Channels permission
- **Web dashboard** — manage categories, panels, blacklists, transcripts, and access control from a browser; login is gated by Discord OAuth2
- **Linked Roles verification** — optional Discord Connections integration so members can verify they've agreed to your rules without the bot needing any privileged intents
- **MongoDB-backed persistence** — falls back to local JSON automatically if no database is configured, so it still works out of the box

---

## 📋 Prerequisites

- A [Discord Developer Portal](https://discord.com/developers/applications) application with a bot user
- A [Render](https://render.com) account (or any host that can run a long-lived Python web service)
- A free [MongoDB Atlas](https://www.mongodb.com/cloud/atlas) cluster (optional, but strongly recommended — see [Persistence](#-persistence) below)
- [UptimeRobot](https://uptimerobot.com) (optional) — pings your Render URL periodically so a free-tier service doesn't spin down from inactivity

---

## 🚀 Setup

### 1. Create your Discord application

1. Go to the [Developer Portal](https://discord.com/developers/applications) → **New Application**
2. **Bot** tab → reset/copy your bot token → this becomes `DISCORD_BOT_TOKEN`
3. **OAuth2 → General** → copy **Client ID** and **Client Secret** → these become `DISCORD_CLIENT_ID` and `DISCORD_CLIENT_SECRET`
4. **OAuth2 → General → Redirects** → add:
   - `https://YOUR-RENDER-URL.onrender.com/callback`
   - `https://YOUR-RENDER-URL.onrender.com/linked-role/callback` *(only if using Linked Roles)*

> **Privileged intents:** this bot runs with **Message Content** and **Server Members** intents both *disabled* by default, and the core ticket/dashboard system doesn't need them. You only need to enable **Message Content** if you want `chat-exporter` to include real message text in saved transcripts — without it, transcripts still generate, just with limited content.

### 2. Deploy on Render

1. New **Web Service** → connect **your fork** of this repo (see [Fork & Clone](#-fork--clone) above) → Environment: **Python 3**
2. Build command: `pip install -r requirements.txt`
3. Start command: `python main.py`
4. Add the environment variables listed below
5. Deploy, then copy your live `https://your-service.onrender.com` URL and go back to fill in the redirect URIs in step 1

### 3. Set your admin user ID

Open `utils/access.py` and replace the fallback ID with your own Discord user ID, so you can never get locked out of the dashboard:

```python
SUPER_ADMIN_FALLBACK_IDS = {"YOUR_DISCORD_USER_ID"}
```

You can also grant admin access via the `ADMIN_USER_IDS` environment variable instead (comma-separated user IDs), without editing code.

### 4. (Optional) Keep it alive on Render's free tier

Point [UptimeRobot](https://uptimerobot.com) at `https://your-service.onrender.com` on a 5-minute interval. Free Render web services spin down after inactivity — a periodic ping keeps the bot and dashboard responsive.

### 5. (Optional) Set up Linked Roles

Linked Roles lets members verify through Discord's own Connections screen instead of a bot command. It needs some one-time setup on Discord's side that lives outside this repo entirely:

1. Deploy first, then in your dashboard's **Access Control** page, click **Register Metadata**
2. Developer Portal → **General Information** → set **Linked Roles Verification URL** to `https://your-service.onrender.com/linked-role`
3. In your server: **Server Settings → Roles → (your role) → Links → Add Requirement** → select this app → choose the "Agreed to Rules" field

If you don't want this feature, it's safe to leave unconfigured — nothing else in the bot depends on it.

---

## 🔑 Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DISCORD_BOT_TOKEN` | ✅ | Your bot's token from the Developer Portal |
| `DISCORD_CLIENT_ID` | ✅ | Your application's Client ID (used for OAuth2 and Linked Roles) |
| `DISCORD_CLIENT_SECRET` | ✅ | Your application's Client Secret |
| `OAUTH2_REDIRECT_URI` | ✅ | `https://your-service.onrender.com/callback` |
| `SECRET_KEY` | ✅ | Random string used to sign dashboard session cookies — **set your own**, don't rely on the code's fallback default |
| `MONGODB_URI` | Recommended | MongoDB Atlas connection string — without this, all settings fall back to local files that **do not survive a redeploy** on most hosts |
| `ADMIN_USER_IDS` | Optional | Comma-separated Discord user IDs granted admin dashboard access |
| `LOG_CHANNEL_ID` | Optional | Channel ID for general ticket activity logs |
| `CARRY_RULES_ROLE_ID` | Optional | Role granted after a member accepts your rules agreement |
| `LINKED_ROLE_REDIRECT_URI` | Optional | `https://your-service.onrender.com/linked-role/callback` — only needed if using Linked Roles |
| `VIRUSTOTAL_API_KEY` | Optional | Enables the `/scan_url` utility command |
| `PORT` | Optional | Defaults to `5000`; Render sets this automatically |

---

## 💾 Persistence

This bot checks for `MONGODB_URI` on every read and write. If it's set, MongoDB is the source of truth and your settings survive redeploys and restarts. If it isn't set, everything falls back to local JSON files under `data/` — which **most hosts, including a typical Render web service without a persistent disk, wipe on every redeploy.** For anything beyond quick testing, connecting a free MongoDB Atlas cluster is strongly recommended.

---

## ⚠️ Privacy & Data

This bot stores Discord user IDs and role IDs (for blacklists, access control, and ticket records) — in MongoDB if configured, otherwise in local JSON files. Keep in mind:

- Never commit real `data/*.json` files, `.env` files, or tokens to a public repository — `.gitignore` already excludes the common ones, but double-check before pushing
- If you fork this for your own server, review `utils/access.py` and swap out any example IDs left in the code
- Treat your MongoDB connection string like a password — anyone with it has full read/write access to your stored data

---

## 🛠 Tech Stack

- [discord.py](https://github.com/Rapptz/discord.py) 2.6+ (Components V2 support)
- Flask (web dashboard)
- MongoDB (optional persistent storage)
- [chat-exporter](https://github.com/mahtoid/DiscordChatExporterPy) (ticket transcripts)

---

## 📄 License

Add your preferred license here (MIT, GPL, etc.) — none is currently specified.

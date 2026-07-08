# Deployment Guide (for beginners)

This walks you through deploying the bot on a Linux server, step by step.
Two options — pick ONE:

- **Option A: Docker** (recommended — one command, auto-restart, clean)
- **Option B: No Docker** (systemd — simpler to debug, fewer moving parts)

---

# Before you start (both options)

You need:
1. A Linux server (Ubuntu 22.04+ is easiest) with a public IP or domain.
2. SSH access to it (`ssh youruser@your-server-ip`).
3. Your Google OAuth client secrets file (`inactiveness_client.json`).
4. Your cached Google token (`google_token.json`) — created the first time the
   bot ran on your PC and you approved the browser consent. The server has no
   browser, so this file MUST be copied from your PC.
5. Your filled-in `.env` file (copy from `.env.example`).
6. HTTPS — Lark requires it for webhooks. Easiest is Caddy (auto-HTTPS, shown below).

Get the code onto the server (clone from GitHub):

```bash
ssh youruser@your-server-ip
git clone https://github.com/Cody0002/Inactive-Group-Chat.git
cd Inactive-Group-Chat
```

Secrets are NOT in git (on purpose). Copy them from your PC:

```bash
# Run these on YOUR computer, not the server:
scp .env inactiveness_client.json google_token.json youruser@your-server-ip:~/Inactive-Group-Chat/
```

(or create `.env` on the server with `cp .env.example .env` and `nano .env`).

---

# OPTION A — Docker

## What is Docker, in one sentence?
Docker packages your app + Python + all libraries into one "container" that
runs the same everywhere, and restarts itself if it crashes.

## A.1 — Install Docker (once per server)

```bash
# Install Docker using the official script
curl -fsSL https://get.docker.com | sudo sh

# Let your user run docker without sudo (log out + back in after this)
sudo usermod -aG docker $USER

# Verify it works (after logging back in)
docker --version
docker compose version
```

## A.2 — Build and start the bot

From inside the `newgroup_bot` folder:

```bash
docker compose up -d --build
```

That's it. Breakdown of what that command does:
- `docker compose` — reads `docker-compose.yml`
- `up` — create and start the container
- `-d` — "detached" = runs in the background
- `--build` — build the image from the Dockerfile first

## A.3 — Everyday Docker commands

```bash
# See if it's running
docker compose ps

# Watch the live logs (Ctrl+C to stop watching — bot keeps running)
docker compose logs -f

# Restart the bot
docker compose restart

# Stop the bot
docker compose down

# After you change the code: rebuild and restart
docker compose up -d --build

# Check memory/CPU usage
docker stats newgroup-bot
```

## A.4 — That's the whole Docker flow

The container auto-restarts on crash or server reboot (because of
`restart: unless-stopped` in the compose file). You don't need to do
anything to keep it alive.

---

# OPTION B — No Docker (systemd)

Use this if you'd rather not learn Docker. systemd is built into Linux and
keeps the bot running.

## B.1 — Install Python and create a virtual environment

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

cd ~/newgroup_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## B.2 — Test it runs

```bash
python main.py
# You should see "Starting New Group Monitor Bot…"
# Press Ctrl+C to stop.
```

## B.3 — Install the systemd service

Edit `newgroup-bot.service` — change `youruser` to your actual username
and fix the paths if needed:

```bash
nano newgroup-bot.service
```

Then install and start it:

```bash
sudo cp newgroup-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable newgroup-bot     # start on boot
sudo systemctl start newgroup-bot      # start now
```

## B.4 — Everyday systemd commands

```bash
sudo systemctl status newgroup-bot     # is it running?
sudo journalctl -u newgroup-bot -f     # watch live logs
sudo systemctl restart newgroup-bot    # restart
sudo systemctl stop newgroup-bot       # stop
```

---

# HTTPS (required for both options)

Lark only sends webhooks to HTTPS URLs. The easiest way is **Caddy**, which
gets a free certificate automatically.

## Install Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

## Configure Caddy

You need a domain (e.g. `bot.yourcompany.com`) pointing to your server's IP.

```bash
sudo nano /etc/caddy/Caddyfile
```

Replace the contents with:

```
bot.yourcompany.com {
    reverse_proxy localhost:8000
}
```

Then:

```bash
sudo systemctl reload caddy
```

Caddy now handles HTTPS and forwards traffic to your bot on port 8000.
Your `SERVER_BASE_URL` in `.env` becomes `https://bot.yourcompany.com`.

---

# Final step — wire up Lark

Once the bot is running and reachable over HTTPS, go to the Lark Developer
Console and set:

- Event subscription Request URL: `https://bot.yourcompany.com/webhook`
  (Lark sends a verification challenge — the bot answers it automatically)
- Subscribe to events: `im.message.receive_v1`, `im.chat.created_v1`
- Permissions: send messages (`im:message`), read basic user info
  (`contact:user.base:readonly`), and **admin audit log read access**
  (needed for activity tracking — under Admin/Audit permissions)

Then in your central Lark group, type `/help` to confirm the bot replies.

---

# Troubleshooting

**Bot won't start / crashes immediately**
Check the logs (`docker compose logs -f` or `journalctl -u newgroup-bot -f`).
Usually a missing value in `.env` or a wrong path to `google_credentials.json`.

**Webhook won't verify in Lark**
- Is the bot running? (`docker compose ps`)
- Is HTTPS working? Visit `https://bot.yourcompany.com/health` in a browser —
  you should see a JSON status.
- Does `LARK_VERIFICATION_TOKEN` in `.env` match the Lark console exactly?

**Bot doesn't reply to /help**
- Is `CENTRAL_GROUP_CHAT_ID` correct?
- Is `im.message.receive_v1` subscribed in the console?

**Google Sheets errors**
- Did you copy BOTH `inactiveness_client.json` and `google_token.json` to the
  server? (Without the token the bot tries to open a browser and hangs.)
- Is `SPREADSHEET_ID` correct (the long string from the sheet URL)?
- If the token expired/revoked: run the bot once on your PC to re-consent,
  then copy the refreshed `google_token.json` to the server again.

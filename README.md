# New Group Monitor Bot

Monitors **newly created** Lark groups from day one. Old groups are left for
manual cleanup. Built to run comfortably on a small server.

**Notification-only.** The bot never asks anyone to authorize, never joins or
posts in monitored groups, and never reads message content. It judges activity
**purely from the admin audit log** using its own app (tenant) token.

## What it does

1. **Detects** every new group via the `im.chat.created_v1` event.
2. **Notifies** the central Lark admin group whenever a new group is created.
3. **Checks** activity daily for **every** group, from qualifying audit events.
4. **Alerts** — tags the creator in the central group when a group goes
   inactive past the threshold (default 180 days).
5. **Summarizes** every Friday: how many groups are active / warning / inactive.

The bot is read-only and does not delete groups. It simply reports new groups,
monitors activity, and escalates inactivity into the central monitoring group.

## Audit query example

A helper script is included at `request.py` to query Lark behavior audit logs.
Use it to inspect today’s group creation activity, for example:

```bash
python request.py --days 1 --event-name im_create_chat --pretty
```

If your local environment uses `python3`, run:

```bash
python3 request.py --days 1 --event-name im_create_chat --pretty
```

This query asks the Lark admin audit API for today’s chat-group creation events.
If the app is not authorized for audit logs, you will need to enable the corresponding scope first.

## Commands (in the central group)

| Command | Action |
|---------|--------|
| `/status` | See tracking stats (active / warning / inactive) |
| `/summary` | Run the activity report now |
| `/help` | Show commands |

## Small-server optimizations

- **3-day log rotation** — logs auto-delete after 3 days (`./logs/`, configurable via `LOG_DIR`).
- **Cached Sheets reads** (60s TTL) — repeated reads in one job don't re-hit the API.
- **Batched writes** — multi-field updates use one API call, not one per cell.
- **Single tenant-token cache** — one app-token refresh, reused across the job.
- **Shared HTTP client** with connection pooling + retry on 429/5xx.
- **Single worker** — low memory, one scheduler, no duplication.
- **Alert-log auto-prune** — rows older than 30 days removed each Friday.

## Setup

1. `conda create -y -n inactive-group-bot python=3.11`
2. `conda activate inactive-group-bot`
3. `pip install -r requirements.txt`
4. Lark app scope: `contact:user.base:readonly` (for creator names) + admin
   **audit log** read access (`audit_infos`)
5. Subscribe to events: `im.message.receive_v1`, `im.chat.created_v1`
6. Webhook URL: `https://your-server.com/webhook`
7. Create a Google Sheet; create a Google **OAuth 2.0 Desktop client** and
   download its JSON (set `GOOGLE_CREDENTIALS_PATH` to it)
8. Copy `.env.example` to `.env`, fill in values
9. `python main.py` — first run opens a browser once to authorize Google Sheets,
   then caches the token at `GOOGLE_TOKEN_PATH`
10. Create the central admin group, add the bot, type `/help`

> Recommended local environment: use the conda environment named `inactive-group-bot` for all project commands.

## Google Sheet tabs (auto-created)

- **groups** — every group: name, creator, created_at, last activity, state
- **alert_log** — sent alerts (auto-pruned to 30 days)

## Resource footprint

Idle: ~60-80 MB RAM. Handles hundreds of groups and dozens of admins on a
1 vCPU / 512 MB-1 GB server. Logs capped at 3 days. Sheets are the only
persistent store — no database to maintain.

# Group Monitor Bot — Quick Guide

A simple bot that lives in the **central admin group**. It quietly keeps an eye
on every Lark group in the workspace and tells you here when something needs
attention. It **never joins your groups, never posts in them, and never reads
message content** — it only looks at activity dates.

You don't need to authorize anything. It just works.

---

## What it sends you

### 📋 Daily report — every day at 17:00 (Bangkok time)

No daily report on Friday — the weekly report goes out then instead.

One message with up to **two short tables**:

1. **🆕 New groups** — groups created today so far (name, creator, time).
2. **⏳ Approaching / inactive** — groups going quiet, with how many days they've
   been silent. 🔴 means already past the limit, 🟠 means getting close.

If there's nothing new and nothing going quiet, the bot stays silent — no noise.

### ⚠️ Inactive alert — the moment a group crosses the line

When a group has been silent past the limit (default **180 days**), the bot posts
a one-time alert to the central group. It won't repeat that alert for **7 days**.

### 📊 Weekly report — every Friday, 17:00 (Bangkok time)

A wider summary: how many groups are active, getting quiet, or inactive, plus
the cleanup watch list of groups quiet for more than **60 days**.

---

## Commands you can type (in the central group)

| Command | What it does |
|---------|--------------|
| `/daily` | Run the daily report right now |
| `/week` | Run the weekly report right now |
| `/detail` | Open the full Google Sheet with every group's details |
| `/help` | Show the list of commands |

---

## When a new group is created — what details you'll see

The bot picks up every newly created group automatically. The details it can
report are:

- **Group name**
- **Creator** (who made it)
- **Members at creation** (how many people were added)
- **External users?** (Yes / No)
- **Created time**
- **Chat ID**

By default these new groups simply appear in the **next daily report**, so you're
not pinged for every single one. (An admin can switch on an instant per-group
notice if the team prefers real-time.)

---

## Where the full data lives

Everything the bot tracks — creator, created date, last activity, days inactive,
and current state — is written to a **Google Sheet**. Use `/detail` to open it
any time.

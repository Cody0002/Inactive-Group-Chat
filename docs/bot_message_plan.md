# Bot Message Plan

Last reviewed: 2026-07-02

## What the Bot Sends Today

The bot posts only into `CENTRAL_GROUP_CHAT_ID`.

### 1. New Group Coverage Gap

Triggered by webhook event `im.chat.created_v1`.

Current behavior:

- The group is written to the `groups` Google Sheet.
- The bot checks whether any authorized admin is already a member.
- If an authorized admin is found, the group is marked `covered`.
- If no authorized admin is found, the group is marked `gap` and the bot sends an interactive card tagging the creator.

Important note: the current code does not send a central-group notification when a new group is already covered. It only sends a message for coverage gaps.

Card source:

- `lark/cards.py::new_group_gap_card`
- Sent from `lark/webhook.py::_handle_new_group`

### 2. Inactive Group Alert

Triggered by scheduled daily check.

Current behavior:

- Runs once daily at `DAILY_CHECK_HOUR_UTC`.
- Checks only groups where `coverage_status == "covered"`.
- Refreshes the covering admin OAuth token.
- Reads the latest human message timestamp from the group.
- Updates `last_activity_at`, `days_inactive`, and `state`.
- Sends an alert only when the group is `inactive` and has not been alerted recently.

Card source:

- `lark/cards.py::inactive_alert_card`
- Sent from `checker.py::_alert`

### 3. Weekly Summary

Triggered every Friday at 17:00 UTC, or manually with `/summary`.

Current behavior:

- Re-checks pending/gap groups against all active authorized admins.
- Marks groups as `covered` or `gap`.
- Sends a weekly card only if there are newly covered groups or gaps.

Card source:

- `lark/cards.py::weekly_summary_card`
- Sent from `weekly_summary.py::run_weekly_summary`

### 4. Slash Command Replies

Only works inside the central group.

- `/checkgroups`: replies with authorization card.
- `/status`: replies with counts for total, covered, gaps, pending, inactive, admins.
- `/summary`: starts weekly summary.
- `/help`: replies with command help.

### 5. Reauthorization Ping

If an admin OAuth refresh token fails during daily check, the bot sends a plain text message tagging that admin and asks them to run `/checkgroups` again.

Source:

- `checker.py::_ping_reauth`

## Recommended Notification Schedule

Yes, two daily scheduled messages is a good operating model, but they should be digests, not repeated noisy alerts.

### Morning Digest: New Groups Created

Recommended time:

- 09:00 local business time, or after overnight group creation activity has settled.

Contents:

- Groups created in the last 24 hours.
- Creator or operator.
- Chat ID.
- Coverage status: `covered`, `gap`, or `pending`.
- Action needed: who should authorize if the group is a gap.

Why:

- The current webhook only warns on gaps.
- A morning digest gives full visibility into all new groups, including groups that were auto-covered.

### Afternoon Digest: Inactivity Review

Recommended time:

- 16:00-17:00 local business time.

Contents:

- Groups that became inactive today.
- Groups in warning state, for example over `WARN_THRESHOLD_DAYS`.
- Groups whose admin authorization expired.
- Groups that could not be checked.

Why:

- Inactivity is operational work, not usually urgent second-by-second.
- A digest reduces alert fatigue.
- Individual urgent alerts can still be sent when a group first crosses the inactive threshold.

## Recommended Message Policy

Use this policy to avoid spam:

- Real-time: only critical setup gaps after group creation.
- Daily morning: all newly created groups from the last 24 hours.
- Daily afternoon: newly inactive groups and check failures.
- Weekly: full coverage/gap summary.
- Manual: `/status` and `/summary` for on-demand checks.

## Current Inactivity Logic

The bot now identifies inactivity from both message history and qualifying Messenger audit events:

- Primary API: `im/v1/messages`
- Primary signal: latest human message timestamp
- Secondary API: Admin Audit `audit_infos`, module `2` Messenger
- Secondary signal: latest qualifying chat activity event tied to the group chat ID
- Bot/app messages are ignored in message history

The final activity timestamp is the newest of:

```text
human message timestamp
qualifying Messenger audit activity timestamp
group created_at fallback
```

The Google Sheet stores the source in:

- `last_activity_source`
- `last_activity_event_name`

## Audit Events That Reset Inactivity

Configured by `AUDIT_ACTIVITY_EVENTS`.

Default reset events:

- `im_chat_uploadfile`
- `im_send_link`
- `im_chat_editimage`
- `im_chat_withdraw`
- `im_addtochat`
- `im_deletefromchat`
- `im_add_chatadmin`
- `im_delete_chatadmin`
- `im_forward_chatadmin`
- `im_admin_no_restrict_ctrl`
- `im_join_chat`
- `im_quit_chat`
- `im_chat_pin_create`
- `im_chat_pin_update`
- `im_chat_pin_update_permission`
- `im_chat_pin_delete`
- `im_chat_pin_stick`
- `im_chat_pin_unstick`
- `im_export_chat_chatter`
- `im_forward_file`
- `im_savetospace`

The checker only applies an audit event when it can map the event back to an `oc_...` group chat ID.

Passive events like file preview, open link, download, copy image, screenshot, and OCR are not in the default reset list. Add them to `AUDIT_ACTIVITY_EVENTS` only if passive consumption should also make a group active.

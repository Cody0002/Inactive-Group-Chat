# Graph Report - D:\Projects\KZG\Inactive-Group-Bot  (2026-07-17)

## Corpus Check
- Corpus is ~13,167 words - fits in a single context window. You may not need a graph.

## Summary
- 259 nodes · 531 edges · 14 communities
- Extraction: 95% EXTRACTED · 4% INFERRED · 1% AMBIGUOUS · INFERRED: 23 edges (avg confidence: 0.84)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Scheduling and Configuration
- Google Sheets Storage
- Lark Card UI
- Notification Policy
- Activity Checker
- Deployment and Runtime
- Audit Request CLI
- Audit Data Model
- Lark API and Webhooks
- New Group Detection
- Token Utility

## God Nodes (most connected - your core abstractions)
1. `weekly_summary_card()` - 20 edges
2. `daily_digest_card()` - 16 edges
3. `SheetsClient` - 16 edges
4. `get_sheets()` - 15 edges
5. `_CachedTab` - 13 edges
6. `get_logger()` - 11 edges
7. `Python Dependency Manifest` - 11 edges
8. `request_with_retry()` - 10 edges
9. `run_weekly_summary()` - 10 edges
10. `_refresh_data_locked()` - 9 edges

## Surprising Connections (you probably didn't know these)
- `Inactive Group Alert` --references--> `inactive_alert_card()`  [EXTRACTED]
  docs/bot_message_plan.md → lark/cards.py
- `Weekly Summary` --references--> `weekly_summary_card()`  [EXTRACTED]
  docs/bot_message_plan.md → lark/cards.py
- `New Group Coverage Gap` --references--> `_handle_new_group()`  [EXTRACTED]
  docs/bot_message_plan.md → lark/webhook.py
- `Weekly Summary` --references--> `run_weekly_summary()`  [EXTRACTED]
  docs/bot_message_plan.md → weekly_summary.py
- `Central Group Notifications` --conceptually_related_to--> `New Group Coverage Gap`  [AMBIGUOUS]
  README.md → docs/bot_message_plan.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Inactivity Evaluation Flow** — docs_bot_message_plan_combined_inactivity_logic, docs_lark_messenger_audit_data_dictionary_message_history_primary, docs_lark_messenger_audit_data_dictionary_audit_log_secondary, docs_lark_messenger_audit_data_dictionary_final_activity_timestamp, docs_bot_overview_inactive_alert [INFERRED 0.95]
- **Central Notification Strategy** — readme_central_group_notifications, docs_bot_message_plan_notification_policy, docs_bot_message_plan_morning_digest, docs_bot_message_plan_afternoon_digest, docs_bot_overview_daily_report, docs_bot_overview_weekly_report [INFERRED 0.85]
- **Small-Server Runtime Stack** — deploy_docker_deployment, docker_compose_bot_service, docker_compose_runtime_constraints, environment_conda_environment, requirements_dependency_manifest, readme_small_server_optimizations [INFERRED 0.85]

## Communities (14 total, 0 thin omitted)

### Community 0 - "Scheduling and Configuration"
Cohesion: 0.09
Nodes (40): BaseSettings, First run: build the 30-day base (every group created in the window —     the au, Render the daily digest from the sheet. No API scans — the hourly     refresh ke, Block until the currently running refresh (if any) finishes., refresh_in_progress(), run_daily_check(), run_initial_backfill(), wait_for_refresh() (+32 more)

### Community 1 - "Google Sheets Storage"
Cohesion: 0.10
Nodes (13): _CachedTab, Update cells across MANY rows in ONE batch API call., Record a group. Returns False if it was already logged.          created_at de, Record many groups in ONE append call (used by the audit scan /         base bu, Fill in member_count / external for an existing group, but only for         fie, Write activity fields for many groups in ONE batch call.         changes: chat_, Apply member add/remove deltas (from audit events) to member_count         in O, Delete alert_log rows older than keep_days to keep the sheet small. (+5 more)

### Community 2 - "Lark Card UI"
Cohesion: 0.16
Nodes (29): _actions(), _button(), _card(), _col(), _creator_md(), daily_digest_card(), detail_card(), _div() (+21 more)

### Community 3 - "Notification Policy"
Cohesion: 0.09
Nodes (28): _alert(), checker._ping_reauth, Post the inactive alert to the central group — no @mention., Afternoon Inactivity Digest, Bot Message Plan, Digest Notification Rationale, Inactive Group Alert, Morning New-Group Digest (+20 more)

### Community 4 - "Activity Checker"
Cohesion: 0.15
Nodes (24): _audit_activity_by_chat(), _audit_activity_events(), _audit_event_time(), _chat_ids_from_audit_item(), _days(), _is_chat_id(), _leak_risk(), _member_event_count() (+16 more)

### Community 5 - "Deployment and Runtime"
Cohesion: 0.14
Nodes (19): Caddy HTTPS Reverse Proxy, Deployment Guide, Docker Deployment, Google OAuth Server Token Provisioning, Lark Webhook Setup, systemd Deployment, Docker Compose Bot Service, Google OAuth Credential and Token Mounts (+11 more)

### Community 6 - "Audit Request CLI"
Cohesion: 0.25
Nodes (16): debug(), _env(), _fetch_audit_logs(), _fmt_ts(), _get_behavior_audit_logs(), _is_rate_limit(), _is_token_error(), main() (+8 more)

### Community 7 - "Audit Data Model"
Cohesion: 0.15
Nodes (17): AUDIT_ACTIVITY_EVENTS, Combined Message and Audit Inactivity Logic, Passive Audit Event Exclusion, Audit Activity Signal Classification, Lark Admin Audit API, Configured Audit Events Secondary Signal, Audit Object-to-Chat Mapping, Audit Top-Level Fields (+9 more)

### Community 8 - "Lark API and Webhooks"
Cohesion: 0.25
Nodes (13): get_behavior_audit_logs(), get_tenant_access_token(), get_user_info(), patch_message(), Lark Open Platform API calls for the QC bot.  Notification-only: the bot never j, Query the Lark admin behavior audit log.      See: https://open.larksuite.com/op, Fetch (and cache) the bot's tenant access token., send_message() (+5 more)

### Community 9 - "New Group Detection"
Cohesion: 0.22
Nodes (12): _chat_from_objects(), _creator_name(), _drawer_map(), _im_fields(), parse_created_group(), New-group detection from the admin audit log (`im_create_chat`).  This is the, Detect newly created groups from the audit log, log them, and (optionally), Flatten common_drawers.common_draw_info_list to {info_key: info_val}. (+4 more)

### Community 10 - "Token Utility"
Cohesion: 0.53
Nodes (5): env(), main(), mask(), parse_args(), Namespace

## Ambiguous Edges - Review These
- `Notification-Only Monitoring` → `Admin Reauthorization Ping`  [AMBIGUOUS]
  README.md · relation: conceptually_related_to
- `Admin Audit Activity Tracking` → `Combined Message and Audit Inactivity Logic`  [AMBIGUOUS]
  README.md · relation: conceptually_related_to
- `Central Group Notifications` → `New Group Coverage Gap`  [AMBIGUOUS]
  README.md · relation: conceptually_related_to
- `Slash Command Replies` → `Central Group Commands`  [AMBIGUOUS]
  docs/bot_overview.md · relation: conceptually_related_to
- `Admin Reauthorization Ping` → `No Authorization Required`  [AMBIGUOUS]
  docs/bot_overview.md · relation: conceptually_related_to

## Knowledge Gaps
- **10 isolated node(s):** `new_group_gap_card`, `checker._ping_reauth`, `Lark Admin Audit API`, `Messenger Audit Module 2`, `Singular event_name Query Parameter` (+5 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Notification-Only Monitoring` and `Admin Reauthorization Ping`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Admin Audit Activity Tracking` and `Combined Message and Audit Inactivity Logic`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Central Group Notifications` and `New Group Coverage Gap`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Slash Command Replies` and `Central Group Commands`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Admin Reauthorization Ping` and `No Authorization Required`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `Weekly Summary` connect `Notification Policy` to `Scheduling and Configuration`, `Lark Card UI`?**
  _High betweenness centrality (0.237) - this node is a cross-community bridge._
- **Why does `run_weekly_summary()` connect `Scheduling and Configuration` to `Lark API and Webhooks`, `Lark Card UI`, `Notification Policy`?**
  _High betweenness centrality (0.152) - this node is a cross-community bridge._
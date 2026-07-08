# Lark Messenger Audit Data Dictionary

Last reviewed: 2026-07-02

Source:

- Lark Admin Audit API: `GET /open-apis/admin/v1/audit_infos`
- Event module: `2` Messenger
- Local sample file: `messenger_audit_module2_sample.json`
- Local create-chat file: `im_create_chat_last_1_day.json`

## Query Rules

Use `event_name`, singular.

Correct:

```text
event_name=im_create_chat
```

Incorrect:

```text
event_names=im_create_chat
```

If the wrong parameter is used, Lark ignores the filter and returns many event types.

Useful commands:

```powershell
python request.py --days 1 --event-name im_create_chat --event-module 2 --page-size 200 --max-pages 5 --output .\im_create_chat_last_1_day.json
python request.py --days 1 --all-events --event-module 2 --page-size 200 --max-pages 5 --output .\messenger_audit_module2_sample.json
```

## Common Top-Level Fields

| Field | Meaning | Notes |
|---|---|---|
| `event_id` | Audit event identifier | Useful for dedupe if present. |
| `unique_id` | Unique audit row identifier | Useful for dedupe if present. |
| `event_time` | Event timestamp | Second-level Unix timestamp. |
| `event_name` | Audit event name | Example: `im_create_chat`. |
| `event_module` | Audit module | Messenger is `2`. |
| `operator_type` | Actor type | Observed value `1` for user. |
| `operator_value` | Actor user identifier | Depends on `user_id_type`. |
| `operator_detail` | Actor metadata | May contain display name and tenant name. |
| `operator_tenant` | Tenant identifier | Useful for multi-tenant audits. |
| `ip` | Public IP | Often also appears inside platform context. |
| `audit_context` | Client/platform context | Android, PC, web context and terminal type. |
| `audit_detail` | Device/location details | City, OS, device model. |
| `objects` | Objects affected by the event | Usually where chat/file/object IDs live. |
| `recipients` | Recipient data | Often empty in observed Messenger samples. |

## Object Fields

Each audit item may have `objects`, usually a list.

| Field | Meaning | Notes |
|---|---|---|
| `object_type` | Type of affected object | Chat objects were observed as `4`; uploaded image/file objects were often `121`. |
| `object_value` | Object ID/value | For chat object type `4`, this is usually the `oc_...` chat ID. |
| `object_name` | Object display name | May be chat name, file type, or empty. |
| `object_owner` | Owner/actor ID | Often blank for chat object, populated for uploaded file/image objects. |
| `object_detail` | Extra object fields | Empty in many observed samples. |

## Observed Sample Distribution

From `messenger_audit_module2_sample.json`, first 5 pages, 1000 total rows:

| Event | Count |
|---|---:|
| `im_chat_previewfile` | 379 |
| `im_chat_uploadfile` | 305 |
| `im_copy_image` | 81 |
| `im_send_link` | 61 |
| `im_chat_pin_sidebar_search` | 57 |
| `im_open_link` | 53 |
| `im_download` | 23 |
| `im_chat_withdraw` | 18 |
| `im_snaphot` | 7 |
| `im_download_file` | 5 |
| `im_download_image` | 4 |
| `im_screencap` | 3 |
| `im_forward_file` | 2 |
| `im_download_video` | 1 |
| `im_ocr` | 1 |

From corrected `im_create_chat_last_1_day.json`:

| Event | Count |
|---|---:|
| `im_create_chat` | 8 |

## Activity Classification

Use this classification when deciding whether a group is inactive.

| Event | Description | Activity Signal | Recommended Use |
|---|---|---|---|
| `im_create_chat` | Creates group chat | Lifecycle | Track new group creation, not activity after creation. |
| `im_delete_chat` | Disbands group chat | Lifecycle | Mark group closed/deleted. |
| `im_join_chat` | Joins group chat | Medium | Shows membership activity, not conversation. |
| `im_quit_chat` | Quits group chat | Medium | Shows membership activity, not conversation. |
| `im_addtochat` | Added group member | Medium | Count as administrative/group activity. |
| `im_deletefromchat` | Deleted group member | Medium | Count as administrative/group activity. |
| `im_add_chatadmin` | Added group administrator | Medium | Count as administrative activity. |
| `im_delete_chatadmin` | Deleted group administrator | Medium | Count as administrative activity. |
| `im_admin_no_restrict_ctrl` | Do not apply restricted mode to a group chat | Medium | Count as administrative/security activity. |
| `im_forward_chatadmin` | Set group owner | Medium | Count as administrative activity. |
| `im_chat_editimage` | Edited image | Strong | Count as content creation/editing activity. |
| `im_chat_uploadfile` | Uploaded file | Strong | Count as group activity. |
| `im_send_link` | Sent URL in chat | Strong | Count as group activity. |
| `im_chat_withdraw` | Recalled message/file | Strong | Count as group activity. |
| `im_chat_pin_create` | Pin an item | Strong | Count as group activity. |
| `im_chat_pin_stick` | Prioritize a pinned item | Medium | Count as group maintenance activity. |
| `im_chat_pin_unstick` | Cancel prioritize a pinned item | Medium | Count as group maintenance activity. |
| `im_chat_pin_update` | Edit pinned link name | Medium | Count as group maintenance activity. |
| `im_chat_pin_update_permission` | Change pin permissions | Medium | Count as group maintenance activity. |
| `im_chat_pin_delete` | Remove pinned item | Medium | Count as group maintenance activity. |
| `im_chat_pin_reorder` | Reorder pinned area | Weak | Optional signal. |
| `im_chat_pin_sidebar_add_link` | Click Add Pinned Link in toolbar | Weak | Intent signal only until a create/update event appears. |
| `im_chat_pin_sidebar_announce` | Click Add/View Group Announcement | Weak | Navigation signal; do not reset inactivity alone. |
| `im_chat_pin_sidebar_file` | Click Files in toolbar | Weak | Navigation signal; do not reset inactivity alone. |
| `im_chat_pin_sidebar_search` | Click Search in toolbar | Weak | Navigation/search signal; do not reset inactivity alone. |
| `im_chat_pin_show_pin_list` | Expand pinned area | Weak | Passive navigation; do not reset inactivity alone. |
| `im_chat_pin_show_in_chat` | View pinned items | Weak | Passive navigation; do not reset inactivity alone. |
| `im_chat_pin_hover_show` | Hover pinned card to preview | Weak | Passive preview; do not reset inactivity alone. |
| `im_chat_pin_click_open_browser` | Click Open in Browser on pinned link | Weak | Passive/open action; do not reset inactivity alone. |
| `im_chat_pin_click_open_url` | Open pinned link | Weak | Passive/open action; do not reset inactivity alone. |
| `im_chat_pin_click_copy_url` | Copy pinned link URL | Weak | Passive/copy action; do not reset inactivity alone. |
| `im_chat_pin_click_back_to_chat` | View pinned item in chat | Weak | Navigation signal; do not reset inactivity alone. |
| `im_chat_previewfile` | Previewed file online | Weak | Passive consumption; do not reset inactivity alone. |
| `im_open_link` | Opens links in chats | Weak | Passive consumption; do not reset inactivity alone. |
| `im_download` | Downloaded file | Weak | Passive consumption; do not reset inactivity alone. |
| `im_download_file` | Downloaded file | Weak | Observed in sample; treat same as `im_download`. |
| `im_download_image` | Downloads images | Weak | Passive consumption; do not reset inactivity alone. |
| `im_download_video` | Downloads videos | Weak | Passive consumption; do not reset inactivity alone. |
| `im_copy_image` | Copies image | Weak | Passive consumption; do not reset inactivity alone. |
| `im_screencap` | Record screen | Weak | Security signal, not group activity. |
| `im_snaphot` | Take screenshots | Weak | Security signal, not group activity. |
| `im_ocr` | Extract text from screenshots | Weak | Security signal, not group activity. |
| `im_read_doc` | Reads Docs in chat window | Weak | Passive consumption. |
| `im_download_doc` | Downloads Docs in chat window | Weak | Passive consumption. |
| `im_open_with_3rdApp` | Opens preview files with third-party app | Weak | Passive consumption. |
| `im_savetospace` | Saved to My Space | Medium | Indicates useful interaction but not conversation. |
| `im_forward_file` | Forwards files | Medium | Count as content movement, not necessarily chat activity. |
| `im_load_file_to_local` | Caches server-side file locally | Weak | Passive/access signal. |
| `im_start_external_chat` | Starts private chat with stranger | Not group activity | Not useful for group inactivity unless object maps to group. |
| `im_export_chat_chatter` | Export group member data | Medium | Admin/security activity. |

## Implemented Inactivity Definition

Primary:

- Use latest human message timestamp from the group message API.

Secondary:

- Use Messenger audit events configured in `AUDIT_ACTIVITY_EVENTS`.
- Prefer `objects[].object_type == "4"` and `objects[].object_value` as the group chat ID.
- If that is missing, recursively search the audit row for an `oc_...` chat ID.
- Reset activity for the configured events when the audit row can be mapped to a known group chat ID.
- Do not reset activity for passive weak events like preview, open link, download, screenshot, OCR, or copy image.

## Implementation Notes

The daily checker stores:

| Field | Purpose |
|---|---|
| `chat_id` | Join key to `groups.chat_id`. |
| `last_activity_at` | Final activity timestamp after comparing message and audit data. |
| `days_inactive` | Number of full days since `last_activity_at`. |
| `state` | `active`, `warning`, or `inactive`. |
| `last_activity_source` | `message_history`, `audit_log`, or `created_at_fallback`. |
| `last_activity_event_name` | Audit event name when source is `audit_log`. |

Final group activity timestamp should be:

```text
max(last_human_message_at, last_configured_audit_activity_at, created_at_fallback)
```

Weak/passive audit events should appear in reports but should not reset inactivity by themselves.

"""
Card UI builders — modern, clean, consistent Lark interactive cards.

Notification-only bot: the only buttons are URL links (e.g. open the sheet).
No OAuth, no authorization callbacks.

Design principles:
  - Color-coded headers (blue=info, green=success, orange=warning, red=alert)
  - Clear visual hierarchy with dividers and notes
  - Minimal text, scannable layout
  - Consistent iconography via emoji prefixes
"""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import settings


# Header color templates Lark supports: blue, wathet, turquoise, green,
# yellow, orange, red, carmine, violet, purple, indigo, grey
def _card(title: str, template: str, elements: list, subtitle: str = "") -> str:
    header = {
        "title": {"tag": "plain_text", "content": title},
        "template": template,
    }
    if subtitle:
        header["subtitle"] = {"tag": "plain_text", "content": subtitle}
    return json.dumps({
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": header,
        "elements": elements,
    })


def _div(content: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _note(content: str) -> dict:
    return {"tag": "note", "elements": [{"tag": "lark_md", "content": content}]}


def _hr() -> dict:
    return {"tag": "hr"}


def _button(text: str, url: str = "", btn_type: str = "primary") -> dict:
    btn = {"tag": "button", "text": {"tag": "plain_text", "content": text},
           "type": btn_type}
    if url:
        btn["url"] = url
    return btn


def _actions(buttons: list) -> dict:
    return {"tag": "action", "actions": buttons}


def _short_dt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso or "?"


def _local_dt(iso: str, fmt: str = "%b %d, %H:%M") -> str:
    """Render a stored UTC ISO timestamp in the report's local timezone."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo(settings.DAILY_REPORT_TZ)).strftime(fmt)
    except Exception:
        return iso or "?"


# ---- native Lark table component: light borders, clean header, paging ----
def _col(name: str, display: str, dtype: str = "text",
         align: str = "left", width: str = "") -> dict:
    col = {"name": name, "display_name": display, "data_type": dtype,
           "horizontal_align": align}
    if width:
        col["width"] = width
    return col


def _table(columns: list[dict], rows: list[dict], page_size: int = 10) -> dict:
    return {
        "tag": "table",
        "page_size": page_size,
        "row_height": "low",
        "header_style": {
            "text_align": "left",
            "text_size": "normal",
            "background_style": "grey",
            "text_color": "grey",
            "bold": True,
            "lines": 1,
        },
        "columns": columns,
        "rows": rows,
    }


def _opt(text: str, color: str) -> list[dict]:
    """A colored tag cell for an 'options' column."""
    return [{"text": text, "color": color}]


def _sheet_url() -> str:
    return f"https://docs.google.com/spreadsheets/d/{settings.SPREADSHEET_ID}/edit"


def _creator_md(name: str) -> str:
    """Bold the display name before the first '|' (e.g. '**Danny** | OA | …')."""
    name = (name or "unknown").strip()
    if "|" in name:
        first, rest = name.split("|", 1)
        return f"**{first.strip()}** | {rest.strip()}"
    return f"**{name}**"


def _members(group: dict) -> int | None:
    try:
        return int(group.get("member_count") or 0) or None
    except (TypeError, ValueError):
        return None


def _is_team(group: dict, personal_max: int) -> bool:
    n = _members(group)
    return n is not None and n > personal_max


def _is_external(group: dict) -> bool:
    return str(group.get("external", "")).lower() in ("yes", "true")


# ----------------------------------------------------------------
# New group detected — informational only (opt-in; off by default)
# ----------------------------------------------------------------
def new_group_created_card(group_name: str, creator_id: str = "",
                           creator_name: str = "", chat_id: str = "",
                           created: str = "", members: str = "",
                           external: str = "") -> str:
    if creator_id:
        creator = f'<at user_id="{creator_id}">{creator_name or creator_id}</at>'
    else:
        creator = creator_name or "unknown"

    lines = [f"👤  Creator:  {creator}"]
    if members != "":
        lines.append(f"👥  Members at creation:  {members}")
    if external != "":
        lines.append(f"🌐  External users:  {external}")
    if created:
        lines.append(f"🕐  Created:  {created}")
    if chat_id:
        lines.append(f"🆔  `{chat_id}`")

    return _card(
        title="🆕  New group created",
        template="blue",
        elements=[
            _div(f"**{group_name}** was just created — now being monitored."),
            _div("\n".join(lines)),
            _note("I'll watch its activity from the audit log and flag it here "
                  "if it goes quiet."),
        ],
    )


# ----------------------------------------------------------------
# Daily digest — two tables in one message (new groups + approaching/inactive)
# ----------------------------------------------------------------
def daily_digest_card(new_groups: list, near_groups: list, threshold: int,
                      window_label: str = "", personal_max: int = 3) -> str:
    """Two tables: new groups (no member counts — creation-time size is
    misleading) and approaching/inactive groups."""
    elements = []
    if window_label:
        elements.append(_note(window_label))

    if new_groups:
        elements.append(_div(f"**🆕  New groups — {len(new_groups)}**"))
        rows = []
        for g in new_groups[:50]:
            name = g.get("group_name") or "Unnamed"
            if _is_external(g):
                name += " 🌐"
            rows.append({
                "group": name,
                "creator": _creator_md(g.get("creator_name")),
                "created": _local_dt(g.get("created_at", "")),
            })
        elements.append(_table(
            [_col("group", "Group", width="55%"),
             _col("creator", "Creator", dtype="lark_md", width="27%"),
             _col("created", "Created", width="18%")],
            rows))

    if near_groups:
        if new_groups:
            elements.append(_hr())
        elements.append(_div(
            f"**⏳  Approaching / inactive — {len(near_groups)}**  "
            f"_(threshold {threshold}d)_"))
        rows = []
        for g in near_groups[:50]:
            try:
                days = int(g.get("days_inactive") or 0)
            except (TypeError, ValueError):
                days = 0
            name = g.get("group_name") or "Unnamed"
            if _is_external(g):
                name += " 🌐"
            rows.append({
                "group": name,
                "days": str(days),
                "status": (_opt("Inactive", "red") if days >= threshold
                           else _opt("Warning", "orange")),
            })
        elements.append(_table(
            [_col("group", "Group", width="58%"),
             _col("days", "Days Inactive", align="right", width="20%"),
             _col("status", "Status", dtype="options", width="22%")],
            rows))

    elements.append(_actions([_button("📄  Open detailed sheet",
                                      url=_sheet_url(), btn_type="default")]))
    return _card(
        title="📋  Daily group report",
        template="blue",
        elements=elements,
    )


# ----------------------------------------------------------------
# Inactive group alert — posted to the central group, no @mention
# ----------------------------------------------------------------
def inactive_alert_card(group_name: str, days: int, last_active: str) -> str:
    return _card(
        title="⚠️  Group inactive",
        template="red",
        elements=[
            _div(f"**{group_name}** has gone quiet."),
            _div(f"🕐  **{days} days** with no activity\n"
                 f"📅  Last active: {last_active}"),
            _note("Please review and clean up if it's no longer needed. "
                  "You're getting this once — I won't repeat it for a week."),
        ],
    )


# ----------------------------------------------------------------
# Weekly summary — activity digest
# ----------------------------------------------------------------
def weekly_summary_card(groups: list, threshold: int, personal_max: int = 3) -> str:
    """Two sections: A) groups created this week, B) groups quiet for more
    than WEEKLY_INACTIVE_DAYS. Member counts here are ACTUAL (kept current
    from add/join/remove/quit audit events)."""
    now = datetime.now(timezone.utc)

    def _days(g):
        try:
            return int(g.get("days_inactive") or 0)
        except (TypeError, ValueError):
            return 0

    def _created(g):
        try:
            return datetime.fromisoformat(
                str(g.get("created_at", "")).replace("Z", "+00:00"))
        except Exception:
            return None

    week_ago = now - timedelta(days=7)
    created_week = [g for g in groups
                    if (c := _created(g)) and c >= week_ago]
    created_week.sort(key=lambda g: g.get("created_at", ""), reverse=True)

    quiet_floor = settings.WEEKLY_INACTIVE_DAYS
    inactive = [g for g in groups if _days(g) > quiet_floor]
    inactive.sort(key=lambda g: (_days(g), 2 if _is_external(g) else 0),
                  reverse=True)

    tz = ZoneInfo(settings.DAILY_REPORT_TZ)
    range_label = (f"{week_ago.astimezone(tz).strftime('%b %d')} → "
                   f"{now.astimezone(tz).strftime('%b %d')}")

    elements = [
        _div(f"**{len(groups)}** group(s) monitored   ·   "
             f"🆕 **{len(created_week)}** created this week   ·   "
             f"😴 **{len(inactive)}** quiet >{quiet_floor}d"),
        _hr(),
        _div(f"**A  ·  🆕  Groups created this week "
             f"({range_label}) — {len(created_week)}**"),
    ]

    if created_week:
        rows = []
        for g in created_week[:50]:
            name = g.get("group_name") or "Unnamed"
            if _is_external(g):
                name += " 🌐"
            n = _members(g)
            rows.append({
                "group": name,
                "creator": _creator_md(g.get("creator_name")),
                "members": str(n) if n is not None else "—",
                "created": _local_dt(g.get("created_at", ""), "%b %d"),
            })
        elements.append(_table(
            [_col("group", "Group", width="46%"),
             _col("creator", "Creator", dtype="lark_md", width="24%"),
             _col("members", "Members", align="right", width="14%"),
             _col("created", "Created", width="16%")],
            rows))
    else:
        elements.append(_note("No new groups this week."))

    elements.append(_hr())
    elements.append(_div(
        f"**B  ·  😴  Inactive groups (>{quiet_floor} days) — {len(inactive)}**"))

    if inactive:
        rows = []
        for g in inactive[:50]:
            name = g.get("group_name") or "Unnamed"
            if _is_external(g):
                name += " 🌐"
            n = _members(g)
            days = _days(g)
            rows.append({
                "group": name,
                "members": str(n) if n is not None else "—",
                # colored tag so the severity pops: red = quiet 90d+, orange = 30d+
                "days": _opt(f"{days}d", "red" if days >= 90 else "orange"),
                "last_active": _local_dt(g.get("last_activity_at", ""), "%b %d"),
            })
        elements.append(_table(
            [_col("group", "Group", width="48%"),
             _col("members", "Members", align="right", width="14%"),
             _col("days", "Days Inactive", dtype="options", width="18%"),
             _col("last_active", "Last Active", width="20%")],
            rows))
    else:
        elements.append(_note(f"None — every group was active in the last "
                              f"{quiet_floor} days 🎉"))

    elements.append(_actions([_button("📄  Open detailed sheet",
                                      url=_sheet_url(), btn_type="primary")]))
    return _card(
        title="📊  Weekly group report",
        template="blue",
        elements=elements,
    )


# ----------------------------------------------------------------
# Detail — link users to the Google Sheet
# ----------------------------------------------------------------
def detail_card(sheet_url: str) -> str:
    return _card(
        title="📄  Full group details",
        template="wathet",
        elements=[
            _div("Every monitored group — creator, created date, last activity, "
                 "days inactive, and state — lives in the Google Sheet:"),
            _actions([_button("Open Google Sheet", url=sheet_url, btn_type="primary")]),
            _note("Read-only view of the same data the bot writes each day."),
        ],
    )


# ----------------------------------------------------------------
# Help card
# ----------------------------------------------------------------
def help_card() -> str:
    commands = [
        ("/daily", "today's report — groups created today & groups going quiet"),
        ("/week", "weekly report — created this week & inactive groups"),
        ("/detail", "open the full Google Sheet"),
        ("/help", "show this message"),
    ]
    return _card(
        title="🤖  Group Monitor",
        template="wathet",
        elements=[
            _div("I check every group's activity once an hour, so any report "
                 "you ask for is ready instantly."),
            _hr(),
            _div("\n".join(f"- **{cmd}** : {desc}" for cmd, desc in commands)),
            _hr(),
            _div("**Automatic**\n"
                 f"· 📋  Daily report at {settings.DAILY_REPORT_HOUR}:00 (GMT+7)\n"
                 "· 📊  Weekly report on Friday at 17:00 (GMT+7)\n"
                 f"· ⚠️  Alert when a group is quiet for over "
                 f"{settings.INACTIVITY_THRESHOLD_DAYS} days"),
            _note("Nothing to authorize — it just works."),
        ],
    )


# ----------------------------------------------------------------
# Simple loading placeholder
# ----------------------------------------------------------------
def loading_card(text: str = "Working on it…") -> str:
    return json.dumps({
        "config": {"wide_screen_mode": True, "update_multi": True},
        "elements": [_div(f"⏳  {text}")],
    })

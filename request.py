import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
import time

import httpx
from dotenv import load_dotenv


LARK_DOMAIN = "https://open.larksuite.com"
TOKEN_REFRESH_BUFFER_SECONDS = 60

QUIET = False


def debug(message: str) -> None:
    # stderr so stdout stays clean JSON (safe to pipe into jq or another script)
    if not QUIET:
        print(f"[debug] {message}", file=sys.stderr, flush=True)


def _parse_time(value: str) -> int:
    """Accept epoch seconds or ISO date/datetime (2026-07-01 or 2026-07-01T09:00)."""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid time {value!r}: use epoch seconds or ISO format "
            "(e.g. 2026-07-01 or 2026-07-01T09:00)"
        )
    if dt.tzinfo is None:
        dt = dt.astimezone()  # interpret naive input as local time
    return int(dt.timestamp())


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _mask(value: str, keep: int = 4) -> str:
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query Lark behavior audit logs using the app tenant access token."
    )
    parser.add_argument(
        "--days",
        type=float,
        default=1,
        help="Query the last N days (default: 1, max: 30). Accepts fractions, e.g. 0.5.",
    )
    parser.add_argument(
        "--oldest",
        type=_parse_time,
        help="Start time as epoch seconds or ISO date/datetime "
        "(e.g. 2026-07-01 or 2026-07-01T09:00). Overrides --days.",
    )
    parser.add_argument(
        "--latest",
        type=_parse_time,
        help="End time as epoch seconds or ISO date/datetime. Defaults to now.",
    )
    event_group = parser.add_mutually_exclusive_group()
    event_group.add_argument(
        "--event-name",
        dest="event_name",
        default="im_create_chat",
        help="Behavioral audit event name (default: im_create_chat).",
    )
    event_group.add_argument(
        "--all-events",
        action="store_true",
        help="Do not filter by event_name. Useful with --event-module for sampling.",
    )
    parser.add_argument(
        "--operator-type",
        choices=["user", "bot"],
        help="Operator type when filtering by operator_value",
    )
    parser.add_argument("--operator-value", help="Operator ID or value")
    parser.add_argument("--event-module", type=int, help="Behavioral audit event module")
    parser.add_argument(
        "--page-size",
        type=int,
        default=20,
        help="Page size (1-200).",
    )
    parser.add_argument(
        "--user-id-type",
        default="user_id",
        choices=["user_id", "open_id", "union_id"],
        help="User ID type used for the query.",
    )
    parser.add_argument(
        "--user-type",
        type=int,
        choices=[0, 1, 2],
        help="User type filter: 0=any, 1=internal, 2=external.",
    )
    parser.add_argument("--object-type", type=int, help="Action object type")
    parser.add_argument("--object-value", help="Action object value")
    parser.add_argument("--page-token", help="Paging token for the next page")
    pages_group = parser.add_mutually_exclusive_group()
    pages_group.add_argument(
        "--all-pages",
        action="store_true",
        default=True,
        help="Fetch every page in the time range (default).",
    )
    pages_group.add_argument(
        "--one-page",
        action="store_false",
        dest="all_pages",
        help="Fetch only one page.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Stop after this many pages, even when more pages are available.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON response.",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress debug output (the JSON result is still printed).",
    )
    parser.add_argument(
        "--output",
        help="Optional JSON output path. Defaults to printing the response.",
    )
    parser.add_argument(
        "--domain",
        default=os.getenv("LARK_DOMAIN", LARK_DOMAIN),
        help="Lark Open Platform domain. Use https://open.feishu.cn for Feishu tenants.",
    )
    return parser.parse_args()


def _env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip().strip("\"'")


def _print_credential_debug(domain: str) -> None:
    app_id = _env("LARK_APP_ID")
    app_secret = _env("LARK_APP_SECRET")
    debug(f"LARK_DOMAIN={domain}")
    debug(f"LARK_APP_ID={_mask(app_id)} len={len(app_id)}")
    debug(f"LARK_APP_SECRET={_mask(app_secret)} len={len(app_secret)}")
    if not app_id.startswith("cli_"):
        debug("Warning: LARK_APP_ID usually starts with cli_")


class TenantTokenManager:
    def __init__(self, client: httpx.AsyncClient, domain: str):
        self.client = client
        self.domain = domain
        self.token: str | None = None
        self.expires_at = 0.0

    async def get(self, *, force_refresh: bool = False) -> str:
        now = time.time()
        if (
            not force_refresh
            and self.token
            and now < self.expires_at - TOKEN_REFRESH_BUFFER_SECONDS
        ):
            return self.token

        debug("Requesting tenant access token")
        resp = await self.client.post(
            f"{self.domain}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": _env("LARK_APP_ID"), "app_secret": _env("LARK_APP_SECRET")},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(
                "Failed to get tenant token: "
                f"{data.get('msg', 'unknown')} (code={data.get('code')}). "
                "Check that LARK_APP_SECRET is the app secret, not the verification token, "
                "and that --domain matches your tenant: "
                "https://open.larksuite.com for Lark or https://open.feishu.cn for Feishu."
            )

        self.token = data["tenant_access_token"]
        expire = int(data.get("expire") or 7200)
        self.expires_at = now + expire
        debug(f"Tenant access token received; expires in {expire} second(s)")
        return self.token


def _is_token_error(data: dict) -> bool:
    msg = str(data.get("msg", "")).lower()
    code = str(data.get("code", ""))
    return (
        "token" in msg
        or "expired" in msg
        or "unauthorized" in msg
        or code in {"99991663", "99991664", "99991668", "99991671"}
    )


def _is_rate_limit(data: dict) -> bool:
    msg = str(data.get("msg", "")).lower()
    return data.get("code") == 99991400 or "frequency" in msg or "rate limit" in msg


async def _get_behavior_audit_logs(
    client: httpx.AsyncClient,
    token_manager: TenantTokenManager,
    *,
    oldest: int,
    latest: int,
    user_id_type: str,
    event_name: str | None,
    operator_type: str | None,
    operator_value: str | None,
    event_module: int | None,
    page_token: str | None,
    page_size: int,
    user_type: int | None,
    object_type: int | None,
    object_value: str | None,
    domain: str,
) -> dict:
    params: dict[str, str | int] = {
        "oldest": oldest,
        "latest": latest,
        "user_id_type": user_id_type,
        "page_size": page_size,
    }
    optional = {
        "event_name": event_name,
        "operator_type": operator_type,
        "operator_value": operator_value,
        "event_module": event_module,
        "page_token": page_token,
        "user_type": user_type,
        "object_type": object_type,
        "object_value": object_value,
    }
    params.update({key: value for key, value in optional.items() if value is not None})

    data: dict | None = None
    token_refreshed = False  # only force-refresh the tenant token once
    force_refresh = False
    max_attempts = 4
    for attempt in range(max_attempts):
        last_attempt = attempt == max_attempts - 1
        token = await token_manager.get(force_refresh=force_refresh)
        force_refresh = False
        resp = await client.get(
            f"{domain}/open-apis/admin/v1/audit_infos",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        if resp.status_code == 429 and not last_attempt:
            wait = float(resp.headers.get("Retry-After") or 2**attempt)
            debug(f"Rate limited (HTTP 429); retrying in {wait:.0f}s")
            await asyncio.sleep(wait)
            continue
        if resp.status_code in {401, 403} and not token_refreshed:
            debug(f"HTTP {resp.status_code}; refreshing tenant token and retrying once")
            token_refreshed = True
            force_refresh = True
            continue
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") == 0:
            break
        if _is_rate_limit(data) and not last_attempt:
            wait = float(2**attempt)
            debug(f"Audit API reported rate limiting; retrying in {wait:.0f}s")
            await asyncio.sleep(wait)
            continue
        if _is_token_error(data) and not token_refreshed:
            debug(
                "Audit API reported a token problem; refreshing tenant token "
                "and retrying once"
            )
            token_refreshed = True
            force_refresh = True
            continue
        break

    if data is None:
        raise RuntimeError("Audit log query failed before receiving a JSON response")
    if data.get("code") != 0:
        raise RuntimeError(
            f"Audit log query failed: {data.get('msg', 'unknown')} (code={data.get('code')})"
        )
    return data.get("data", {})


async def _fetch_audit_logs(args: argparse.Namespace, oldest: int, latest: int) -> dict:
    if not 1 <= args.page_size <= 200:
        raise ValueError("page_size must be between 1 and 200")
    if args.max_pages is not None and args.max_pages < 1:
        raise ValueError("max_pages must be at least 1")

    async with httpx.AsyncClient(timeout=30.0) as client:
        token_manager = TenantTokenManager(client, args.domain)
        items: list[dict] = []
        page_count = 0
        page_token = args.page_token
        has_more = False

        while True:
            debug(f"Fetching audit page {page_count + 1}")
            page = await _get_behavior_audit_logs(
                client,
                token_manager,
                oldest=oldest,
                latest=latest,
                user_id_type=args.user_id_type,
                event_name=args.event_name,
                operator_type=args.operator_type,
                operator_value=args.operator_value,
                event_module=args.event_module,
                page_size=args.page_size,
                user_type=args.user_type,
                object_type=args.object_type,
                object_value=args.object_value,
                page_token=page_token,
                domain=args.domain,
            )
            page_count += 1
            page_items = page.get("items", [])
            items.extend(page_items)
            has_more = bool(page.get("has_more"))
            page_token = page.get("page_token")
            debug(
                f"Page {page_count} received: {len(page_items)} item(s), "
                f"has_more={has_more}"
            )
            if not args.all_pages or not has_more or not page_token:
                break
            if args.max_pages is not None and page_count >= args.max_pages:
                debug(f"Reached max_pages={args.max_pages}; stopping pagination")
                break

    raw_total = len(items)
    filtered_out = 0
    if args.event_name:
        items = [item for item in items if item.get("event_name") == args.event_name]
        filtered_out = raw_total - len(items)
        if filtered_out:
            debug(
                f"Filtered out {filtered_out} item(s) whose event_name "
                f"was not {args.event_name}"
            )

    return {
        "oldest": oldest,
        "latest": latest,
        "event_name": args.event_name,
        "raw_total": raw_total,
        "filtered_out": filtered_out,
        "total": len(items),
        "items": items,
        "pages": page_count,
        "max_pages": args.max_pages,
        "has_more": has_more,
        "next_page_token": page_token if has_more else None,
    }


async def main() -> None:
    global QUIET
    load_dotenv()
    args = parse_args()
    QUIET = args.quiet
    if args.all_events:
        args.event_name = None
    if args.days <= 0:
        raise ValueError("--days must be positive")
    latest = args.latest if args.latest is not None else int(time.time())
    oldest = (
        args.oldest if args.oldest is not None else int(latest - args.days * 86400)
    )
    if oldest >= latest:
        raise ValueError("oldest must be earlier than latest")
    if latest - oldest > 30 * 86400:
        raise ValueError("Time range cannot exceed 30 days")

    debug(f"Querying event_name={args.event_name}")
    debug(f"Time window: oldest={oldest}, latest={latest}, seconds={latest - oldest}")
    debug(f"Page size: {args.page_size}, all_pages={args.all_pages}")
    debug(f"Max pages: {args.max_pages if args.max_pages is not None else 'unlimited'}")
    _print_credential_debug(args.domain)
    data = await _fetch_audit_logs(args, oldest, latest)

    # Human-readable summary on stderr; stdout carries only JSON.
    label = args.event_name or "all events"
    print(
        f"[done] {data['total']} event(s) ({label}) across {data['pages']} page(s) "
        f"| {_fmt_ts(oldest)} -> {_fmt_ts(latest)}",
        file=sys.stderr,
        flush=True,
    )

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        debug(f"Saved output to {output_path.resolve()}")
    elif args.pretty:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

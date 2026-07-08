"""
Lark Open Platform API calls for the QC bot.

Notification-only: the bot never joins groups and never asks anyone to
authorize. It reads the admin audit log and sends alerts to the central
group using only its own tenant (app) token.
"""
from __future__ import annotations
import time
from typing import Optional

from config import settings
from utils.http import request_with_retry
from utils.logger import get_logger

logger = get_logger(__name__)

_tenant_token_cache: dict = {"token": None, "expires_at": 0}


async def get_tenant_access_token() -> str:
    """Fetch (and cache) the bot's tenant access token."""
    if _tenant_token_cache["token"] and time.time() < _tenant_token_cache["expires_at"]:
        return _tenant_token_cache["token"]
    resp = await request_with_retry(
        "POST",
        f"{settings.LARK_DOMAIN}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": settings.LARK_APP_ID, "app_secret": settings.LARK_APP_SECRET},
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Failed to get tenant token: {data.get('msg')}")
    _tenant_token_cache["token"] = data["tenant_access_token"]
    _tenant_token_cache["expires_at"] = time.time() + data["expire"] - 60
    return data["tenant_access_token"]


# ----------------------------------------------------------------
# Messaging (bot's own token)
# ----------------------------------------------------------------
async def send_message(receive_id: str, content: str, msg_type: str = "text",
                       receive_id_type: str = "chat_id") -> Optional[str]:
    token = await get_tenant_access_token()
    resp = await request_with_retry(
        "POST",
        f"{settings.LARK_DOMAIN}/open-apis/im/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={"receive_id_type": receive_id_type},
        json={"receive_id": receive_id, "msg_type": msg_type, "content": content},
    )
    data = resp.json()
    if data.get("code") != 0:
        logger.warning(f"send_message failed: code={data.get('code')} {data.get('msg')}")
    return data.get("data", {}).get("message_id")


async def reply_in_thread(parent_message_id: str, content: str,
                          msg_type: str = "interactive") -> Optional[str]:
    token = await get_tenant_access_token()
    resp = await request_with_retry(
        "POST",
        f"{settings.LARK_DOMAIN}/open-apis/im/v1/messages/{parent_message_id}/reply",
        headers={"Authorization": f"Bearer {token}"},
        json={"msg_type": msg_type, "content": content, "reply_in_thread": True},
    )
    return resp.json().get("data", {}).get("message_id")


async def patch_message(message_id: str, content: str) -> bool:
    token = await get_tenant_access_token()
    resp = await request_with_retry(
        "PATCH",
        f"{settings.LARK_DOMAIN}/open-apis/im/v1/messages/{message_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"content": content},
    )
    return resp.json().get("code") == 0


async def get_user_info(open_id: str) -> dict:
    if not open_id:
        return {}
    token = await get_tenant_access_token()
    resp = await request_with_retry(
        "GET",
        f"{settings.LARK_DOMAIN}/open-apis/contact/v3/users/{open_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={"user_id_type": "open_id"},
    )
    return resp.json().get("data", {}).get("user", {})


async def get_behavior_audit_logs(
    oldest: int | None = None,
    latest: int | None = None,
    user_id_type: str = "user_id",
    event_name: str | None = None,
    event_names: str | None = None,
    operator_type: str | None = None,
    operator_value: str | None = None,
    event_module: int | None = None,
    page_token: str | None = None,
    page_size: int | None = None,
    user_type: int | None = None,
    object_type: int | None = None,
    object_value: str | None = None,
) -> dict:
    """Query the Lark admin behavior audit log.

    See: https://open.larksuite.com/open-apis/admin/v1/audit_infos
    """
    params: dict[str, str | int] = {"user_id_type": user_id_type}
    if oldest is not None:
        params["oldest"] = oldest
    if latest is not None:
        params["latest"] = latest
    event_filter = event_name or event_names
    if event_filter:
        params["event_name"] = event_filter
    if operator_type:
        params["operator_type"] = operator_type
    if operator_value:
        params["operator_value"] = operator_value
    if event_module is not None:
        params["event_module"] = event_module
    if page_token is not None:
        params["page_token"] = page_token
    if page_size is not None:
        if not 1 <= page_size <= 200:
            raise ValueError("page_size must be between 1 and 200")
        params["page_size"] = page_size
    if user_type is not None:
        params["user_type"] = user_type
    if object_type is not None:
        params["object_type"] = object_type
    if object_value is not None:
        params["object_value"] = object_value

    token = await get_tenant_access_token()
    resp = await request_with_retry(
        "GET",
        f"{settings.LARK_DOMAIN}/open-apis/admin/v1/audit_infos",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(
            f"Audit log query failed: {data.get('msg', 'unknown')} (code={data.get('code')})"
        )
    return data.get("data", {})

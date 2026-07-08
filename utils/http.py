"""Resilient HTTP helper: retry on 429/5xx with exponential backoff."""
from __future__ import annotations
import asyncio
import httpx
from utils.logger import get_logger

logger = get_logger(__name__)

_client: httpx.AsyncClient | None = None
MAX_RETRIES = 3
BASE_DELAY = 1.0


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def close_http_client():
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()


async def request_with_retry(method: str, url: str, **kwargs) -> httpx.Response:
    client = get_http_client()
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", BASE_DELAY * (2 ** attempt)))
                logger.warning(f"Rate limited on {url}, retrying in {retry_after}s")
                await asyncio.sleep(retry_after)
                continue
            if resp.status_code >= 500:
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning(f"Server error {resp.status_code} on {url}, retry in {delay}s")
                await asyncio.sleep(delay)
                continue
            return resp
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            last_exc = e
            delay = BASE_DELAY * (2 ** attempt)
            logger.warning(f"Connection error on {url} (attempt {attempt + 1}): {e}")
            await asyncio.sleep(delay)
    if last_exc:
        raise last_exc
    raise httpx.HTTPError(f"Failed after {MAX_RETRIES} retries: {url}")

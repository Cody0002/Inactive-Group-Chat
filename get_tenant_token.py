import argparse
import os

import httpx
from dotenv import load_dotenv


DEFAULT_DOMAIN = "https://open.larksuite.com"


def mask(value: str, keep: int = 4) -> str:
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"


def env(name: str) -> str:
    value = os.getenv(name, "").strip().strip("\"'")
    if not value:
        raise RuntimeError(f"Missing required .env value: {name}")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Get a Lark custom-app tenant_access_token from .env credentials."
    )
    parser.add_argument(
        "--domain",
        default=os.getenv("LARK_DOMAIN", DEFAULT_DOMAIN),
        help=(
            "Open Platform domain. Use https://open.larksuite.com for Lark, "
            "or https://open.feishu.cn for Feishu."
        ),
    )
    parser.add_argument(
        "--show-token",
        action="store_true",
        help="Print the full tenant_access_token. Otherwise only a masked token is printed.",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    app_id = env("LARK_APP_ID")
    app_secret = env("LARK_APP_SECRET")
    domain = args.domain.rstrip("/")
    url = f"{domain}/open-apis/auth/v3/tenant_access_token/internal"

    print("[debug] Loading credentials from .env", flush=True)
    print(f"[debug] LARK_DOMAIN={domain}", flush=True)
    print(f"[debug] LARK_APP_ID={mask(app_id)} len={len(app_id)}", flush=True)
    print(f"[debug] LARK_APP_SECRET={mask(app_secret)} len={len(app_secret)}", flush=True)
    print(f"[debug] POST {url}", flush=True)

    if not app_id.startswith("cli_"):
        print("[debug] Warning: custom app IDs usually start with cli_", flush=True)

    response = httpx.post(
        url,
        headers={"Content-Type": "application/json; charset=utf-8"},
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=30.0,
    )
    print(f"[debug] HTTP status={response.status_code}", flush=True)
    response.raise_for_status()

    data = response.json()
    print(f"[debug] Lark code={data.get('code')} msg={data.get('msg')}", flush=True)
    if data.get("code") != 0:
        print("[debug] Tenant token request failed.", flush=True)
        print("[debug] Check that LARK_APP_SECRET is App Secret, not Verification Token.", flush=True)
        print("[debug] Also check domain: Lark uses open.larksuite.com, Feishu uses open.feishu.cn.", flush=True)
        raise SystemExit(1)

    token = data["tenant_access_token"]
    expire = data.get("expire")
    if args.show_token:
        print(f"tenant_access_token={token}")
    else:
        print(f"tenant_access_token={mask(token, keep=8)}")
    print(f"expire={expire}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Technical smoke test for the NAV pam-stilling-feed API.

The scheduled workflow uses ``NAV_FEED_TOKEN``. NAV's rotating public token
is available only for an explicitly requested manual experiment.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

DEFAULT_BASE_URL = "https://pam-stilling-feed.nav.no"
FEED_PATH = "/api/v1/feed"
ENTRY_PATH_TEMPLATE = "/api/v1/feedentry/{entry_id}"
PUBLIC_TOKEN_PATH = "/api/publicToken"

EXIT_OK = 0
EXIT_DATA_ERROR = 1
EXIT_INFRASTRUCTURE = 78
REQUEST_TIMEOUT_SECONDS = (10, 60)
RETRY_DELAYS_SECONDS = (2, 4, 8)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
INFRASTRUCTURE_STATUS_CODES = {401, 403, *RETRYABLE_STATUS_CODES}

RELEVANT_HEADER_NAMES = (
    "etag",
    "last-modified",
    "content-type",
    "link",
    "cache-control",
    "x-request-id",
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_env() -> None:
    if load_dotenv:
        load_dotenv(_project_root() / ".env")


def _base_url() -> str:
    raw = os.environ.get("NAV_FEED_BASE_URL", "").strip()
    return raw or DEFAULT_BASE_URL


def _truthy_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _extract_jwt(value: str) -> str | None:
    match = re.search(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", value)
    return match.group(0) if match else None


def _retry_after_seconds(response: requests.Response, fallback: int) -> int:
    value = response.headers.get("Retry-After", "").strip()
    if value.isdigit():
        return min(int(value), 60)
    return fallback


def _get_with_retry(
    session: requests.Session, url: str
) -> tuple[requests.Response | None, str | None]:
    for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            if attempt == len(RETRY_DELAYS_SECONDS):
                return None, str(exc)
            time.sleep(RETRY_DELAYS_SECONDS[attempt])
            continue

        if response.status_code not in RETRYABLE_STATUS_CODES:
            return response, None
        if attempt == len(RETRY_DELAYS_SECONDS):
            return response, None

        time.sleep(_retry_after_seconds(response, RETRY_DELAYS_SECONDS[attempt]))

    return None, "request retry loop ended unexpectedly"


def _fetch_public_token(base: str) -> tuple[str | None, str | None]:
    response, error = _get_with_retry(session=requests.Session(), url=base.rstrip("/") + PUBLIC_TOKEN_PATH)
    if error:
        return None, f"NAV public token request failed: {error}"
    assert response is not None
    if response.status_code != 200:
        return None, f"NAV public token endpoint returned HTTP {response.status_code}"
    token = _extract_jwt(response.text)
    if not token:
        return None, "NAV public token response did not contain a JWT-looking token"
    return token, None


def _optional_bearer_token(base: str) -> tuple[str | None, str, str | None]:
    token = os.environ.get("NAV_FEED_TOKEN", "").strip()
    if token:
        return token, "configured_secret", None

    if _truthy_env("NAV_FEED_USE_PUBLIC_TOKEN"):
        token, error = _fetch_public_token(base)
        return token, "manual_public_token", error

    return None, "none", "NAV_FEED_TOKEN is not configured"


def _session_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _pick_relevant_headers(response: requests.Response) -> dict[str, str]:
    return {
        key: value
        for key, value in response.headers.items()
        if key.lower() in RELEVANT_HEADER_NAMES
    }


def _safe_json(response: requests.Response) -> tuple[Any | None, str | None]:
    try:
        return response.json(), None
    except ValueError as exc:
        return None, f"JSON parse error: {exc}"


def _error_title(response: requests.Response) -> str | None:
    data, _ = _safe_json(response)
    if isinstance(data, dict) and isinstance(data.get("title"), str):
        return data["title"]
    return None


def _extract_feed_items(data: Any) -> list[Any] | None:
    if not isinstance(data, dict):
        return None
    items = data.get("items")
    return items if isinstance(items, list) else None


def _entry_path_from_item(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    url = item.get("url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    for key in ("id", "uuid", "entryId", "entry_id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return ENTRY_PATH_TEMPLATE.format(entry_id=value.strip())
    return None


def _request_json(session: requests.Session, url: str) -> tuple[int, Any | None]:
    print(f"GET {url}")
    response, request_error = _get_with_retry(session, url)
    if request_error:
        print(f"Infrastructure error: {request_error}")
        return EXIT_INFRASTRUCTURE, None
    assert response is not None

    print(f"HTTP {response.status_code}")
    for key, value in sorted(_pick_relevant_headers(response).items()):
        print(f"  {key}: {value}")

    if response.status_code != 200:
        title = _error_title(response)
        suffix = f": {title}" if title else ""
        print(f"NAV returned HTTP {response.status_code}{suffix}")
        status = (
            EXIT_INFRASTRUCTURE
            if response.status_code in INFRASTRUCTURE_STATUS_CODES
            else EXIT_DATA_ERROR
        )
        return status, None

    data, error = _safe_json(response)
    if error:
        print(f"Data error: expected JSON from HTTP 200 response ({error})")
        return EXIT_DATA_ERROR, None
    return EXIT_OK, data


def main() -> int:
    _load_env()
    base = _base_url().rstrip("/")
    token, token_source, token_error = _optional_bearer_token(base)

    print(f"Effective base URL: {base}")
    print(f"Token source: {token_source}")
    print(f"Authorization header will be sent: {bool(token)}")
    if token_error:
        print(f"Infrastructure/auth error: {token_error}")
        return EXIT_INFRASTRUCTURE

    session = requests.Session()
    session.headers.update(_session_headers(token))

    status, feed = _request_json(session, base + FEED_PATH)
    if status != EXIT_OK:
        return status

    items = _extract_feed_items(feed)
    if items is None:
        print("Data error: HTTP 200 feed response did not contain an 'items' list")
        return EXIT_DATA_ERROR
    print(f"Feed contains {len(items)} items")

    entry_path = next((path for item in items if (path := _entry_path_from_item(item))), None)
    if not entry_path:
        print("No entry URL was available; feed contract is valid, skipping entry probe")
        return EXIT_OK

    entry_url = entry_path if entry_path.startswith("http") else base + entry_path
    status, _ = _request_json(session, entry_url)
    return status


if __name__ == "__main__":
    sys.exit(main())

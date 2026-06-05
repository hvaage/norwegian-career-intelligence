#!/usr/bin/env python3
"""
Technical smoke test for NAV pam-stilling-feed.

References:
  - Swagger: https://pam-stilling-feed.ekstern.dev.nav.no/swagger
  - OpenAPI: https://pam-stilling-feed.ekstern.dev.nav.no/api/openapi.json
  - Docs: https://navikt.github.io/pam-stilling-feed/
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

DEFAULT_BASE_URL = "https://pam-stilling-feed.nav.no"
FEED_PATH = "/api/v1/feed"
ENTRY_PATH_TEMPLATE = "/api/v1/feedentry/{entry_id}"
PUBLIC_TOKEN_PATH = "/api/publicToken"

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


def _truthy_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _extract_jwt(value: str) -> str | None:
    match = re.search(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", value)
    return match.group(0) if match else None


def _fetch_public_token(base: str) -> str | None:
    url = base.rstrip("/") + PUBLIC_TOKEN_PATH
    try:
        response = requests.get(url, timeout=30)
    except requests.RequestException as exc:
        print(f"Could not fetch NAV public token: {exc}")
        return None
    if not response.ok:
        print(f"NAV public token endpoint returned {response.status_code}.")
        return None
    token = _extract_jwt(response.text)
    if not token:
        print("NAV public token response did not contain a JWT-looking token.")
    return token


def _optional_bearer_token(base: str) -> tuple[str | None, str]:
    token = os.environ.get("NAV_FEED_TOKEN")
    if token is not None:
        token = token.strip()
        if token:
            return token, "NAV_FEED_TOKEN"

    use_public_token = _truthy_env(
        "NAV_FEED_USE_PUBLIC_TOKEN",
        default=base.rstrip("/") == DEFAULT_BASE_URL,
    )
    if use_public_token:
        public_token = _fetch_public_token(base)
        if public_token:
            return public_token, "NAV public token"

    return None, "none"


def _session_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _print_auth_debug(base: str, token: str | None, token_source: str) -> None:
    """Safe auth debug: never print the full token."""
    print("\n--- Auth / URL debug (token never printed in full) ---")
    print(f"Effective base URL: {base}")
    print(f"Token source: {token_source}")
    headers = _session_headers(token)
    auth_present = "Authorization" in headers
    print(f"Authorization header will be sent: {auth_present}")
    if "Authorization" in headers:
        scheme, _, cred = headers["Authorization"].partition(" ")
        print(f"Auth header scheme: {scheme.strip() or '(empty)'}")
        # Never print `cred` (bearer token).
        if cred:
            print(f"Bearer credential length: {len(cred)} characters")
            if len(cred) >= 12:
                print(f"Bearer credential prefix (6): {cred[:6]}...")
                print(f"Bearer credential suffix (6): ...{cred[-6:]}")
            elif len(cred) >= 6:
                print(
                    "Bearer credential prefix/suffix: (token 6–11 chars; "
                    "6+6 preview would overlap — showing length only)"
                )
            else:
                print(
                    "Bearer credential: (shorter than 6 chars; "
                    "not printing any fragment)"
                )
    else:
        print("Auth header scheme: (none)")
    print("--- end auth debug ---\n")


def _pick_relevant_headers(response: requests.Response) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in response.headers.items():
        if key.lower() in RELEVANT_HEADER_NAMES:
            out[key] = value
    return out


def _safe_json(response: requests.Response) -> tuple[Any | None, str | None]:
    """Returns (parsed, error_message)."""
    ctype = response.headers.get("Content-Type", "")
    if "json" not in ctype.lower() and response.text.strip()[:1] not in "{[":
        return None, f"non-JSON response (Content-Type: {ctype or 'missing'})"
    try:
        return response.json(), None
    except ValueError as exc:
        return None, f"JSON parse error: {exc}"


def _top_level_keys(data: Any) -> list[str] | None:
    if isinstance(data, dict):
        return list(data.keys())
    return None


def _extract_feed_items(data: Any) -> list[Any] | None:
    if not isinstance(data, dict):
        return None
    items = data.get("items")
    if isinstance(items, list):
        return items
    return None


def _entry_path_from_item(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    url = item.get("url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    for key in ("id", "uuid", "entryId", "entry_id"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return ENTRY_PATH_TEMPLATE.format(entry_id=val.strip())
    nested = item.get("_feed_entry")
    if isinstance(nested, dict):
        for key in ("id", "uuid"):
            val = nested.get(key)
            if isinstance(val, str) and val.strip():
                return ENTRY_PATH_TEMPLATE.format(entry_id=val.strip())
    return None


def _first_entry_path(items: list[Any] | None) -> str | None:
    if not items:
        return None
    for item in items:
        path = _entry_path_from_item(item)
        if path:
            return path
    return None


def _print_auth_hint(status_code: int, had_token: bool) -> None:
    if status_code in (401, 403):
        if not had_token:
            print(
                f"\nMissing NAV_FEED_TOKEN: server returned {status_code}. "
                "Set NAV_FEED_TOKEN in .env (see .env.example) and retry."
            )
        else:
            print(
                f"\nAuth failed ({status_code}) with a token present: "
                "verify NAV_FEED_TOKEN value, audience, and expiry."
            )
        print(
            "Dev/prod hosts may require Bearer auth per NAV's feed terms; "
            "see https://navikt.github.io/pam-stilling-feed/"
        )


def _write_raw(path: Path, response: requests.Response) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    print(f"Saved raw body to {path}")


def _run_feed(session: requests.Session, base: str, token: str | None) -> tuple[Any | None, str | None]:
    url = base.rstrip("/") + FEED_PATH
    print(f"\nGET {url}")
    had_token = bool(token)
    try:
        response = session.get(url, timeout=60)
    except requests.RequestException as exc:
        print(f"Request failed: {exc}")
        return None, None

    print(f"Status: {response.status_code}")
    for k, v in sorted(_pick_relevant_headers(response).items()):
        print(f"  {k}: {v}")

    data, err = _safe_json(response)
    if err:
        print(f"Body parse: {err}")
        snippet = response.text[:500]
        if snippet:
            print(f"Body snippet (first 500 chars):\n{snippet}")
        _print_auth_hint(response.status_code, had_token)
        return None, None

    keys = _top_level_keys(data)
    if keys is not None:
        print(f"Top-level JSON keys: {keys}")
    else:
        print("Unexpected JSON shape: top-level is not an object.")

    items = _extract_feed_items(data)
    if items is None:
        print(
            "Could not find a list at key 'items' (unexpected feed shape — "
            "often an auth or error JSON envelope instead of a Feed document)."
        )
    else:
        print(f"Feed item count (this page): {len(items)}")
        for i, entry in enumerate(items[:3], start=1):
            print(f"--- entry {i} ---")
            try:
                print(json.dumps(entry, ensure_ascii=False, indent=2)[:4000])
            except (TypeError, ValueError):
                print(repr(entry)[:4000])

    entry_path = _first_entry_path(items)
    if entry_path:
        print(f"\nDetected feed entry path: {entry_path}")
    else:
        print("\nNo feed entry path detected from first items.")

    _print_auth_hint(response.status_code, had_token)

    out_path = _project_root() / "data" / "raw" / "sample_feed.json"
    _write_raw(out_path, response)

    return data, entry_path


def _run_entry(
    session: requests.Session,
    base: str,
    entry_path: str,
    token: str | None,
) -> None:
    url = urljoin(base.rstrip("/") + "/", entry_path.lstrip("/"))
    print(f"\nGET {url}")
    had_token = bool(token)
    try:
        response = session.get(url, timeout=60)
    except requests.RequestException as exc:
        print(f"Request failed: {exc}")
        return

    print(f"Status: {response.status_code}")
    for k, v in sorted(_pick_relevant_headers(response).items()):
        print(f"  {k}: {v}")

    data, err = _safe_json(response)
    if err:
        print(f"Body parse: {err}")
        snippet = response.text[:500]
        if snippet:
            print(f"Body snippet (first 500 chars):\n{snippet}")
        _print_auth_hint(response.status_code, had_token)
    else:
        keys = _top_level_keys(data)
        if keys is not None:
            print(f"Top-level JSON keys: {keys}")

    _print_auth_hint(response.status_code, had_token)

    out_path = _project_root() / "data" / "raw" / "sample_entry.json"
    _write_raw(out_path, response)


def main() -> int:
    _load_env()
    base = _base_url()
    token, token_source = _optional_bearer_token(base)

    _print_auth_debug(base, token, token_source)

    if not token:
        print(
            "NAV_FEED_TOKEN is not set (optional). "
            "The request will be sent without Authorization."
        )

    session = requests.Session()
    session.headers.update(_session_headers(token))

    data, entry_path = _run_feed(session, base, token)
    items = _extract_feed_items(data)
    if items is None:
        print("\nSmoke test failed: feed response did not contain an 'items' list.")
        return 1

    if entry_path:
        _run_entry(session, base, entry_path, token)
    else:
        print("\nSkipping feedentry request (no entry path).")
        print("sample_entry.json was not written (no GET /api/v1/feedentry/... call).")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

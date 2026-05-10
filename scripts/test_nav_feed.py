#!/usr/bin/env python3
"""
Technical smoke test for NAV pam-stilling-feed (dev/swagger/test endpoints).

References:
  - Swagger: https://pam-stilling-feed.ekstern.dev.nav.no/swagger
  - OpenAPI: https://pam-stilling-feed.ekstern.dev.nav.no/api/openapi.json
  - Docs: https://navikt.github.io/pam-stilling-feed/
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

DEFAULT_BASE_URL = "https://pam-stilling-feed.ekstern.dev.nav.no"
FEED_PATH = "/api/v1/feed"
ENTRY_PATH_TEMPLATE = "/api/v1/feedentry/{entry_id}"

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


def _optional_bearer_token() -> str | None:
    token = os.environ.get("NAV_FEED_TOKEN")
    if token is None:
        return None
    token = token.strip()
    return token or None


def _session_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


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


def _entry_id_from_item(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    for key in ("id", "uuid", "entryId", "entry_id"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    nested = item.get("_feed_entry")
    if isinstance(nested, dict):
        for key in ("id", "uuid"):
            val = nested.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def _first_entry_id(items: list[Any] | None) -> str | None:
    if not items:
        return None
    for item in items:
        eid = _entry_id_from_item(item)
        if eid:
            return eid
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

    entry_id = _first_entry_id(items)
    if entry_id:
        print(f"\nDetected feed entry id: {entry_id}")
    else:
        print("\nNo feed entry id detected from first items.")

    _print_auth_hint(response.status_code, had_token)

    out_path = _project_root() / "data" / "raw" / "sample_feed.json"
    _write_raw(out_path, response)

    return data, entry_id


def _run_entry(
    session: requests.Session,
    base: str,
    entry_id: str,
    token: str | None,
) -> None:
    path = ENTRY_PATH_TEMPLATE.format(entry_id=entry_id)
    url = base.rstrip("/") + path
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
    token = _optional_bearer_token()

    if not token:
        print(
            "NAV_FEED_TOKEN is not set (optional). "
            "The request will be sent without Authorization."
        )

    session = requests.Session()
    session.headers.update(_session_headers(token))

    _data, entry_id = _run_feed(session, base, token)
    if entry_id:
        _run_entry(session, base, entry_id, token)
    else:
        print("\nSkipping feedentry request (no entry id).")
        print("sample_entry.json was not written (no GET /api/v1/feedentry/... call).")

    return 0


if __name__ == "__main__":
    sys.exit(main())

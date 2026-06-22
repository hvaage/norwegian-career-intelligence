#!/usr/bin/env python3
"""
Read-only NAV job ad status report via Supabase PostgREST.

This avoids relying on `supabase db query --linked`, which requires a separate
Supabase CLI access token in local automation environments.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_env() -> None:
    env_path = project_root() / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def parse_content_range(value: str | None) -> int:
    if not value or "/" not in value:
        raise RuntimeError(f"Missing count in Content-Range header: {value!r}")
    _, total = value.rsplit("/", 1)
    if total == "*":
        raise RuntimeError(f"Exact count unavailable in Content-Range: {value!r}")
    return int(total)


class SupabaseRest:
    def __init__(self, base_url: str, service_role_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_role_key = service_role_key

    def get_rows(self, relation: str, params: dict[str, str]) -> list[dict[str, Any]]:
        url = f"{self.base_url}/rest/v1/{relation}?{urlencode(params)}"
        headers = {
            "Accept": "application/json",
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
        }
        request = Request(url, headers=headers, method="GET")
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def count_rows(self, relation: str, filters: dict[str, str] | None = None) -> int:
        params = {"select": "id"}
        if filters:
            params.update(filters)
        url = f"{self.base_url}/rest/v1/{relation}?{urlencode(params)}"
        headers = {
            "Accept": "application/json",
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Prefer": "count=exact",
            "Range": "0-0",
            "Range-Unit": "items",
        }
        request = Request(url, headers=headers, method="GET")
        with urlopen(request, timeout=30) as response:
            return parse_content_range(response.headers.get("Content-Range"))


def latest_value(
    client: SupabaseRest,
    relation: str,
    column: str,
    filters: dict[str, str] | None = None,
) -> str | None:
    params = {
        "select": column,
        "order": f"{column}.desc.nullslast",
        "limit": "1",
    }
    if filters:
        params.update(filters)
    rows = client.get_rows(relation, params)
    if not rows:
        return None
    value = rows[0].get(column)
    return str(value) if value is not None else None


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_report() -> dict[str, Any]:
    load_env()
    client = SupabaseRest(
        require_env("SUPABASE_URL"),
        require_env("SUPABASE_SERVICE_ROLE_KEY"),
    )

    checked_at = datetime.now(timezone.utc)
    since_24h = checked_at - timedelta(hours=24)
    since_24h_iso = since_24h.isoformat().replace("+00:00", "Z")

    latest_run_rows = client.get_rows(
        "nav_sync_run_log",
        {
            "select": "started_at,mode,status,fetched_count,inserted_count,updated_count",
            "order": "started_at.desc.nullslast",
            "limit": "1",
        },
    )
    latest_run = latest_run_rows[0] if latest_run_rows else {}

    latest_imported_at = latest_value(
        client,
        "job_opportunities",
        "imported_at",
        {"source": "eq.nav"},
    )
    latest_published_at = latest_value(
        client,
        "job_opportunities",
        "published_at",
        {"source": "eq.nav"},
    )

    latest_imported_dt = parse_timestamp(latest_imported_at)
    latest_run_status = latest_run.get("status")
    status_ok = (
        latest_run_status == "success"
        and latest_imported_dt is not None
        and latest_imported_dt >= since_24h
    )

    return {
        "checked_at": checked_at.isoformat().replace("+00:00", "Z"),
        "total_nav_job_ads": client.count_rows(
            "job_opportunities",
            {"source": "eq.nav"},
        ),
        "downloaded_last_24h": client.count_rows(
            "job_opportunities",
            {"source": "eq.nav", "imported_at": f"gte.{since_24h_iso}"},
        ),
        "active_nav_rows": client.count_rows(
            "job_opportunities",
            {"source": "eq.nav", "status": "eq.ACTIVE"},
        ),
        "valid_nav_jobs": client.count_rows("valid_nav_jobs"),
        "stale_nav_jobs": client.count_rows("stale_nav_jobs"),
        "latest_imported_at": latest_imported_at,
        "latest_published_at": latest_published_at,
        "latest_run_started": latest_run.get("started_at"),
        "latest_run_mode": latest_run.get("mode"),
        "latest_run_status": latest_run_status,
        "latest_run_fetched": latest_run.get("fetched_count"),
        "latest_run_inserted": latest_run.get("inserted_count"),
        "latest_run_updated": latest_run.get("updated_count"),
        "status_ok": status_ok,
        "status_reason": (
            "OK: latest run is success and latest_imported_at is within 24 hours"
            if status_ok
            else "AVVIK: latest run is not success or latest_imported_at is older than 24 hours"
        ),
    }


def main() -> int:
    try:
        print(json.dumps(build_report(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(
            json.dumps(
                {
                    "error": "Supabase REST request failed",
                    "http_status": exc.code,
                    "body": body[:2000],
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1
    except (OSError, RuntimeError, URLError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

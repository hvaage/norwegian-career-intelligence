#!/usr/bin/env python3
"""
Technical exploration: SSB PxWebApi v2 labor-market structure tables.

Tables:
  - 08417
  - 09793

Scope: metadata + very small sample data only.
No Supabase writes, no normalization, no signal building yet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

BASE_URL = "https://data.ssb.no/api/pxwebapi/v2"
TABLE_IDS = ["08417", "09793"]
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "ssb"

TOP_LEVEL_HINTS = ("hoved", "hovedgruppe", "nivå 1", "niva 1", "level 1")


def _safe_json(response: requests.Response) -> tuple[Any | None, str | None]:
    ctype = (response.headers.get("content-type") or "").lower()
    if "json" not in ctype and not response.text.strip().startswith(("{", "[")):
        return None, f"Non-JSON response (content-type={ctype or 'missing'})"
    try:
        return response.json(), None
    except ValueError as exc:
        return None, f"JSON parse error: {exc}"


def _request_json(
    method: str,
    url: str,
    *,
    table_id: str,
    purpose: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, requests.Response | None]:
    try:
        resp = requests.request(method, url, params=params, json=json_body, timeout=60)
    except requests.RequestException as exc:
        print(f"[{table_id}] {purpose}: request failed: {exc}")
        return None, None

    if resp.status_code in (404, 429, 503):
        label = {404: "Not Found", 429: "Rate Limited", 503: "Service Unavailable"}[
            resp.status_code
        ]
        print(f"[{table_id}] {purpose}: HTTP {resp.status_code} ({label})")
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                print(f"[{table_id}] {purpose}: Retry-After={retry_after}")
        return None, resp

    data, err = _safe_json(resp)
    if err:
        print(f"[{table_id}] {purpose}: {err}")
        if not resp.ok:
            print(f"[{table_id}] {purpose}: HTTP {resp.status_code}")
        snippet = resp.text[:400].strip()
        if snippet:
            print(f"[{table_id}] {purpose}: body snippet: {snippet}")
        return None, resp

    if not isinstance(data, dict):
        print(f"[{table_id}] {purpose}: unexpected top-level JSON type: {type(data).__name__}")
        return None, resp

    if not resp.ok:
        print(f"[{table_id}] {purpose}: HTTP {resp.status_code}")
        # Catch too-large query responses and similar API validation errors.
        err_title = str(data.get("title") or data.get("type") or "").lower()
        err_body = str(data.get("errors") or data.get("detail") or data.get("message") or "").lower()
        if "too large" in err_title or "too large" in err_body or "max" in err_body:
            print(f"[{table_id}] {purpose}: too-large query style error detected.")
        elif "selection" in err_body:
            print(f"[{table_id}] {purpose}: selection/query validation error.")
        else:
            print(f"[{table_id}] {purpose}: error keys: {list(data.keys())}")
        return None, resp

    return data, resp


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _extract_output_formats(basic_meta: dict[str, Any]) -> list[str]:
    formats: set[str] = set()
    for link in basic_meta.get("links", []) or []:
        if not isinstance(link, dict):
            continue
        href = link.get("href")
        if not isinstance(href, str):
            continue
        query = parse_qs(urlparse(href).query)
        for key in ("outputFormat", "outputformat"):
            for v in query.get(key, []):
                if v:
                    formats.add(v)
    return sorted(formats)


def _dimensions(meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    dims = meta.get("dimension")
    if not isinstance(dims, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for code, raw in dims.items():
        if not isinstance(raw, dict):
            continue
        idx = ((raw.get("category") or {}).get("index") or {})
        labels = ((raw.get("category") or {}).get("label") or {})
        out[str(code)] = {
            "label": str(raw.get("label") or code),
            "count": len(idx) if isinstance(idx, dict) else 0,
            "index": idx if isinstance(idx, dict) else {},
            "labels": labels if isinstance(labels, dict) else {},
        }
    return out


def _print_dim_summary(table_id: str, dims: dict[str, dict[str, Any]]) -> None:
    if not dims:
        print(f"[{table_id}] Dimensions: unexpected metadata shape (missing/invalid 'dimension').")
        return
    print(f"[{table_id}] Dimensions:")
    for code, info in dims.items():
        print(f"  - {code} ({info['label']}): values={info['count']}")


def _time_variable(meta: dict[str, Any]) -> str | None:
    role = meta.get("role")
    if isinstance(role, dict):
        t = role.get("time")
        if isinstance(t, list) and t:
            return str(t[0])
    for c in ("Tid", "time", "Time", "År", "Aar"):
        if c in _dimensions(meta):
            return c
    return None


def _last_n_codes(idx: dict[str, int], n: int) -> list[str]:
    if not idx:
        return []
    items = sorted(idx.items(), key=lambda kv: kv[1])
    return [str(k) for k, _ in items[-n:]]


def _pick_top_level_codes(labels: dict[str, Any], fallback_codes: list[str]) -> list[str]:
    if not labels:
        return fallback_codes[:1]
    top = []
    for code, label in labels.items():
        text = str(label).lower()
        if any(h in text for h in TOP_LEVEL_HINTS):
            top.append(str(code))
    return top[:1] if top else fallback_codes[:1]


def _build_selection(meta: dict[str, Any], periods: int) -> list[dict[str, Any]]:
    dims = _dimensions(meta)
    if not dims:
        return []
    selection: list[dict[str, Any]] = []
    time_var = _time_variable(meta)

    for code, info in dims.items():
        idx: dict[str, int] = info["index"]
        labels: dict[str, Any] = info["labels"]
        if not idx:
            continue

        if time_var and code == time_var:
            values = _last_n_codes(idx, periods)
        else:
            ordered = _last_n_codes(idx, len(idx))
            # ordered currently oldest..newest because from _last_n_codes full list;
            # take first for small queries, but prefer top-level if label suggests so.
            values = _pick_top_level_codes(labels, ordered)

        if not values:
            continue
        selection.append({"VariableCode": code, "ValueCodes": values})

    return selection


def _try_sample_data(table_id: str, meta: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    url = f"{BASE_URL}/tables/{table_id}/data"
    attempts = [
        ("latest-1y", _build_selection(meta, periods=1)),
        ("latest-2y", _build_selection(meta, periods=2)),
    ]
    for attempt_name, selection in attempts:
        if not selection:
            continue
        data, _ = _request_json(
            "POST",
            url,
            params={"lang": "no"},
            json_body={"selection": selection, "outputFormat": "json-stat2"},
            table_id=table_id,
            purpose=f"sample data ({attempt_name})",
        )
        if data is not None:
            return data, f"post-{attempt_name}"

    # Fallback GET (may still be large; kept as final fallback only)
    data, _ = _request_json(
        "GET",
        url,
        params={"lang": "no", "outputFormat": "json-stat2"},
        table_id=table_id,
        purpose="sample data (fallback-get)",
    )
    return data, "fallback-get"


def _print_signal_note(table_id: str) -> None:
    print(f"[{table_id}] possible signal types: market_signal, trajectory_signal, risk_signal, role_family_signal, verified_statistical")


def run_table(table_id: str) -> None:
    basic_url = f"{BASE_URL}/tables/{table_id}"
    meta_url = f"{BASE_URL}/tables/{table_id}/metadata"

    basic, basic_resp = _request_json(
        "GET",
        basic_url,
        params={"lang": "no"},
        table_id=table_id,
        purpose="basic metadata",
    )
    basic_path = OUT_DIR / f"{table_id}_basic_metadata.json"
    if basic_resp is not None:
        _write_json(
            basic_path,
            basic
            if basic is not None
            else {"error": "basic_metadata_failed", "status_code": basic_resp.status_code, "body": basic_resp.text[:5000]},
        )
        print(f"[{table_id}] saved: {basic_path}")

    detailed, detailed_resp = _request_json(
        "GET",
        meta_url,
        params={"lang": "no"},
        table_id=table_id,
        purpose="detailed metadata",
    )
    detailed_path = OUT_DIR / f"{table_id}_metadata.json"
    if detailed_resp is not None:
        _write_json(
            detailed_path,
            detailed
            if detailed is not None
            else {
                "error": "detailed_metadata_failed",
                "status_code": detailed_resp.status_code,
                "body": detailed_resp.text[:5000],
            },
        )
        print(f"[{table_id}] saved: {detailed_path}")

    if basic is None or detailed is None:
        print(f"[{table_id}] skipping sample data (metadata missing).")
        return

    title = basic.get("label") or basic.get("title") or detailed.get("label") or "N/A"
    first_period = basic.get("firstPeriod") or detailed.get("firstPeriod")
    last_period = basic.get("lastPeriod") or detailed.get("lastPeriod")
    print(f"\n=== Table {table_id} ===")
    print(f"table_id: {table_id}")
    print(f"title/label: {title}")
    print(f"firstPeriod: {first_period if first_period is not None else 'N/A'}")
    print(f"lastPeriod: {last_period if last_period is not None else 'N/A'}")
    var_names = basic.get("variableNames")
    if isinstance(var_names, list) and var_names:
        print(f"variable names (basic): {var_names}")
    dims = _dimensions(detailed)
    _print_dim_summary(table_id, dims)
    formats = _extract_output_formats(basic)
    print(f"[{table_id}] available output formats: {formats if formats else 'N/A'}")

    sample_data, method = _try_sample_data(table_id, detailed)
    sample_path = OUT_DIR / f"{table_id}_sample_data.json"
    if sample_data is not None:
        _write_json(sample_path, {"request_method_used": method, "data": sample_data})
        print(f"[{table_id}] sample request method: {method}")
        size = sample_data.get("size")
        values = sample_data.get("value")
        if isinstance(size, list):
            print(f"[{table_id}] sample size: {size}")
        if isinstance(values, list):
            print(f"[{table_id}] sample values count: {len(values)}")
        print(f"[{table_id}] saved: {sample_path}")
    else:
        _write_json(sample_path, {"error": "sample_data_failed", "request_method_used": method})
        print(f"[{table_id}] sample data failed; wrote error file: {sample_path}")

    _print_signal_note(table_id)


def main() -> int:
    print("SSB PxWebApi v2 labor-market technical test")
    print(f"Tables: {TABLE_IDS}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for table_id in TABLE_IDS:
        run_table(table_id)
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


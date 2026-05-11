#!/usr/bin/env python3
"""
Technical test for SSB PxWebApi v2 table metadata and sample data.

Tables:
  - 11615
  - 12850
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

BASE_URL = "https://data.ssb.no/api/pxwebapi/v2"
TABLE_IDS = ["11615", "12850"]
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "ssb"


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

    if not resp.ok:
        print(f"[{table_id}] {purpose}: HTTP {resp.status_code}")
        data, err = _safe_json(resp)
        if err:
            print(f"[{table_id}] {purpose}: {err}")
            snippet = resp.text[:400].strip()
            if snippet:
                print(f"[{table_id}] {purpose}: body snippet: {snippet}")
            return None, resp
        if isinstance(data, dict):
            print(f"[{table_id}] {purpose}: error keys: {list(data.keys())}")
        return None, resp

    data, err = _safe_json(resp)
    if err:
        print(f"[{table_id}] {purpose}: {err}")
        return None, resp
    if not isinstance(data, dict):
        print(f"[{table_id}] {purpose}: unexpected top-level JSON type: {type(data).__name__}")
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
        of = query.get("outputFormat") or query.get("outputformat")
        if of:
            for v in of:
                if v:
                    formats.add(v)
    return sorted(formats)


def _dimension_info(metadata: dict[str, Any]) -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    dims = metadata.get("dimension")
    if not isinstance(dims, dict):
        return out
    for code, meta in dims.items():
        label = code
        count = 0
        if isinstance(meta, dict):
            label = str(meta.get("label") or code)
            cat = meta.get("category")
            if isinstance(cat, dict):
                index = cat.get("index")
                if isinstance(index, dict):
                    count = len(index)
        out.append((str(code), label, count))
    return out


def _latest_period_code(metadata: dict[str, Any]) -> tuple[str | None, str | None]:
    role = metadata.get("role")
    if isinstance(role, dict):
        t = role.get("time")
        if isinstance(t, list) and t:
            time_var = str(t[0])
            dim = (metadata.get("dimension") or {}).get(time_var, {})
            if isinstance(dim, dict):
                idx = ((dim.get("category") or {}).get("index") or {})
                if isinstance(idx, dict) and idx:
                    # Sorted by category index position if available.
                    items = sorted(idx.items(), key=lambda kv: kv[1])
                    return time_var, str(items[-1][0])
    # Fallback for common naming
    dims = metadata.get("dimension") or {}
    if isinstance(dims, dict):
        for candidate in ("Tid", "time", "Time"):
            dim = dims.get(candidate)
            if not isinstance(dim, dict):
                continue
            idx = ((dim.get("category") or {}).get("index") or {})
            if isinstance(idx, dict) and idx:
                items = sorted(idx.items(), key=lambda kv: kv[1])
                return candidate, str(items[-1][0])
    return None, None


def _build_small_selection(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    dims = metadata.get("dimension") or {}
    if not isinstance(dims, dict):
        return []
    time_var, latest_code = _latest_period_code(metadata)
    selection: list[dict[str, Any]] = []
    for var_code, var_meta in dims.items():
        if not isinstance(var_meta, dict):
            continue
        idx = ((var_meta.get("category") or {}).get("index") or {})
        if not isinstance(idx, dict) or not idx:
            continue
        if time_var and var_code == time_var and latest_code:
            chosen = latest_code
        else:
            # pick first available code
            chosen = next(iter(idx.keys()))
        selection.append({"VariableCode": str(var_code), "ValueCodes": [str(chosen)]})
    return selection


def _try_small_data_request(table_id: str, metadata: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    data_url = f"{BASE_URL}/tables/{table_id}/data"
    selection = _build_small_selection(metadata)
    if selection:
        attempts: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
            (
                "post-selection-lower",
                {"lang": "no"},
                {"selection": selection, "outputFormat": "json-stat2"},
            ),
            (
                "post-selection-pascal",
                {"lang": "no"},
                {"Selection": selection, "OutputFormat": "json-stat2"},
            ),
            (
                "post-selection-with-query-format",
                {"lang": "no", "outputFormat": "json-stat2"},
                {"selection": selection},
            ),
        ]
        for label, params, body in attempts:
            data, _ = _request_json(
                "POST",
                data_url,
                params=params,
                json_body=body,
                table_id=table_id,
                purpose=f"sample data ({label})",
            )
            if data is not None:
                return data, label

    # Fallback: default /data (often already current period for some tables)
    data, _ = _request_json(
        "GET",
        data_url,
        params={"lang": "no", "outputFormat": "json-stat2"},
        table_id=table_id,
        purpose="sample data (fallback GET)",
    )
    return data, "fallback-get"


def _print_table_summary(table_id: str, basic: dict[str, Any], detailed: dict[str, Any]) -> None:
    title = basic.get("label") or basic.get("title") or detailed.get("label") or "N/A"
    first_period = basic.get("firstPeriod") or detailed.get("firstPeriod")
    last_period = basic.get("lastPeriod") or detailed.get("lastPeriod")
    print(f"\n=== Table {table_id} ===")
    print(f"Title/label: {title}")
    print(f"firstPeriod: {first_period if first_period is not None else 'N/A'}")
    print(f"lastPeriod: {last_period if last_period is not None else 'N/A'}")
    var_names = basic.get("variableNames")
    if isinstance(var_names, list) and var_names:
        print(f"Variable names (basic): {var_names}")
    dims = _dimension_info(detailed)
    if dims:
        print("Dimensions (detailed):")
        for code, label, count in dims:
            print(f"  - {code} ({label}) values={count}")
    else:
        print("Dimensions (detailed): N/A")
    formats = _extract_output_formats(basic)
    if formats:
        print(f"Available output formats (from links): {formats}")
    else:
        print("Available output formats: N/A")


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
    if basic_resp is not None:
        basic_path = OUT_DIR / f"{table_id}_basic_metadata.json"
        if basic is not None:
            _write_json(basic_path, basic)
        else:
            _write_json(
                basic_path,
                {
                    "error": "non_json_or_request_error",
                    "status_code": basic_resp.status_code,
                    "body": basic_resp.text[:5000],
                },
            )
        print(f"[{table_id}] saved: {basic_path}")

    detailed, meta_resp = _request_json(
        "GET",
        meta_url,
        params={"lang": "no"},
        table_id=table_id,
        purpose="detailed metadata",
    )
    if meta_resp is not None:
        meta_path = OUT_DIR / f"{table_id}_metadata.json"
        if detailed is not None:
            _write_json(meta_path, detailed)
        else:
            _write_json(
                meta_path,
                {
                    "error": "non_json_or_request_error",
                    "status_code": meta_resp.status_code,
                    "body": meta_resp.text[:5000],
                },
            )
        print(f"[{table_id}] saved: {meta_path}")

    if basic is None or detailed is None:
        print(f"[{table_id}] skipping sample data request (metadata missing).")
        return

    _print_table_summary(table_id, basic, detailed)

    sample_data, method_used = _try_small_data_request(table_id, detailed)
    sample_path = OUT_DIR / f"{table_id}_sample_data.json"
    if sample_data is not None:
        payload = {"request_method_used": method_used, "data": sample_data}
        _write_json(sample_path, payload)
        print(f"[{table_id}] sample data request method: {method_used}")
        size = sample_data.get("size")
        values = sample_data.get("value")
        if isinstance(values, list):
            print(f"[{table_id}] sample data values count: {len(values)}")
        if size is not None:
            print(f"[{table_id}] sample data size: {size}")
        print(f"[{table_id}] saved: {sample_path}")
    else:
        _write_json(
            sample_path,
            {
                "error": "sample_data_request_failed",
                "request_method_used": method_used,
            },
        )
        print(f"[{table_id}] sample data failed; wrote error file: {sample_path}")


def main() -> int:
    print("SSB PxWebApi v2 technical test")
    print(f"Tables: {TABLE_IDS}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for table_id in TABLE_IDS:
        run_table(table_id)
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


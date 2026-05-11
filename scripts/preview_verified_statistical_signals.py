#!/usr/bin/env python3
"""
Preview verified_statistical-style signals from Supabase statistical_observations.

Preview only: writes CSV + JSON under data/processed/signal_preview/.
Does not insert into signals or any other table.

Change-style generators (employment_count_change, regional_education_employment_signal,
industry_education_employment_signal) compare two periods per dimension slice. With a
plain row cap, PostgREST may return many rows for a single period first, so pair counts
stay at zero. For those signals, use ``--balanced-periods`` so the script discovers the
two latest distinct periods and fetches up to ``--limit`` rows **per period**, then merges
them in memory. Snapshot generators keep the default sequential fetch.

Quality filters (``--min-baseline``, ``--exclude-unspecified`` / ``--include-unspecified``,
``--contents-code``) trim noisy preview rows before manual review; emitted rows include
``quality_flags`` with ``preview_not_product_ready`` until reviewed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from supabase import Client, create_client

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "processed" / "signal_preview"
SCRIPT_VERSION = "preview_verified_statistical_signals_v1.2"

SELECT_OBS_COLS = (
    "id, table_id, period, value, unit, contents_code, dimensions_json, dimension_labels_json, "
    "statistical_dataset_id, source_file, normalization_version, transformation_version, "
    "confidence_category, confidence_score"
)

GROWTH_PCT_THRESHOLD = 5.0
DECLINE_PCT_THRESHOLD = -5.0

TABLES_EMPLOYMENT_CHANGE = ("11615", "12850")
TABLE_REGIONAL = "11615"
TABLE_INDUSTRY = "12850"
TABLE_OCCUPATION = "09793"
TABLE_WORKFORCE = "08417"

TIME_KEYS = frozenset({"Tid", "tid", "TIME", "time"})

SIGNAL_TYPES = (
    "employment_count_change",
    "regional_education_employment_signal",
    "industry_education_employment_signal",
    "occupation_structure_signal",
    "education_level_workforce_signal",
    "all",
)

CSV_COLUMNS = [
    "signal_type",
    "signal_label",
    "table_id",
    "periods_compared",
    "value_start",
    "value_end",
    "absolute_change",
    "percent_change",
    "direction_label",
    "confidence_category",
    "confidence_score",
    "source_observation_ids",
    "source_table",
    "dimensions_json",
    "dimension_labels_json",
    "explainability_note",
    "lineage_json",
    "quality_flags",
    "min_baseline",
    "contents_code",
    "contents_code_label",
]

# Case-insensitive substring match against flattened dimension label text.
UNSPECIFIED_LABEL_SUBSTRINGS = (
    "uoppgitt",
    "unspecified",
    "unknown",
    "not stated",
    "ikke oppgitt",
)


@dataclass
class RunStats:
    rows_read: int = 0
    candidate_pairs: int = 0
    preview_signals_generated: int = 0
    skipped_missing_prior_period: int = 0
    skipped_zero_baseline: int = 0
    skipped_low_baseline: int = 0
    skipped_unspecified_category: int = 0

    def merge(self, other: RunStats) -> None:
        self.rows_read += other.rows_read
        self.candidate_pairs += other.candidate_pairs
        self.preview_signals_generated += other.preview_signals_generated
        self.skipped_missing_prior_period += other.skipped_missing_prior_period
        self.skipped_zero_baseline += other.skipped_zero_baseline
        self.skipped_low_baseline += other.skipped_low_baseline
        self.skipped_unspecified_category += other.skipped_unspecified_category


@dataclass
class QualityContext:
    min_baseline: float
    exclude_unspecified: bool
    contents_code_filter: str | None


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_client() -> Client:
    load_dotenv(ROOT / ".env")
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment/.env")
    return create_client(url, key)


def _period_sort_key(period: str | None) -> tuple[Any, ...]:
    if period is None:
        return (2, "")
    p = str(period).strip()
    if len(p) == 4 and p.isdigit():
        return (0, int(p))
    return (1, p)


def _slice_key(dims: dict[str, Any], exclude: frozenset[str] | None = None) -> str:
    ex = TIME_KEYS | (exclude or frozenset())
    items = sorted((str(k), str(v)) for k, v in dims.items() if k not in ex and v is not None)
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def _dims_subset(dims: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {k: dims[k] for k in keys if k in dims}


def _labels_for(
    dims: dict[str, Any],
    labels: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(labels, dict):
        return {}
    return {k: labels.get(k) for k in dims if k in labels}


def _direction_from_pct(pct: float | None) -> str:
    if pct is None:
        return "n/a"
    if pct >= GROWTH_PCT_THRESHOLD:
        return "growth"
    if pct <= DECLINE_PCT_THRESHOLD:
        return "decline"
    return "stable"


def _labels_flat_text(labels: Any) -> str:
    if not isinstance(labels, dict):
        return ""
    parts: list[str] = []
    for v in labels.values():
        if v is None:
            continue
        if isinstance(v, dict):
            parts.extend("" if x is None else str(x) for x in v.values())
        else:
            parts.append(str(v))
    return " ".join(parts).lower()


def _has_unspecified_category(labels: Any) -> bool:
    t = _labels_flat_text(labels)
    return any(s in t for s in UNSPECIFIED_LABEL_SUBSTRINGS)


def _resolve_contents_code_fields(row: dict[str, Any]) -> tuple[str, str]:
    """Return (contents_code, display_label). Label prefers dimension_labels_json['ContentsCode']."""
    raw = row.get("contents_code")
    code_s = "" if raw is None else str(raw).strip()
    lab = row.get("dimension_labels_json") or {}
    if isinstance(lab, dict):
        lc = lab.get("ContentsCode")
        if lc is None:
            lc = lab.get("contents_code")
        if lc is not None and str(lc).strip():
            return code_s, str(lc).strip()
    return code_s, (code_s if code_s else "")


def _lineage_json(
    row_a: dict[str, Any],
    row_b: dict[str, Any] | None,
    extra: dict[str, Any] | None = None,
) -> str:
    base = {
        "script": SCRIPT_VERSION,
        "generator": "preview_verified_statistical_signals.py",
        "statistical_dataset_id": row_a.get("statistical_dataset_id"),
        "source_file": row_a.get("source_file"),
        "normalization_version": row_a.get("normalization_version"),
        "transformation_version": row_a.get("transformation_version"),
    }
    if row_b:
        base["source_file_b"] = row_b.get("source_file")
    if extra:
        base.update(extra)
    return json.dumps(base, ensure_ascii=False)


def _fetch_observations_paged(
    client: Client,
    *,
    table_id: str,
    limit: int,
    period: str | None = None,
    contents_code: str | None = None,
) -> list[dict[str, Any]]:
    """Paginate PostgREST reads up to `limit` rows. If `period` is set, filter `.eq('period', period)`."""
    out: list[dict[str, Any]] = []
    offset = 0
    chunk = min(1000, max(1, limit))
    while len(out) < limit:
        take = min(chunk, limit - len(out))
        q = (
            client.table("statistical_observations")
            .select(SELECT_OBS_COLS)
            .eq("table_id", table_id)
            .order("period")
        )
        if contents_code is not None:
            q = q.eq("contents_code", contents_code)
        if period is not None:
            q = q.eq("period", period)
        res = q.range(offset, offset + take - 1).execute()
        batch = res.data or []
        if not batch:
            break
        out.extend(batch)
        if len(batch) < take:
            break
        offset += take
    return out[:limit]


def _fetch_observations(
    client: Client,
    table_id: str,
    limit: int,
    *,
    contents_code: str | None = None,
) -> list[dict[str, Any]]:
    return _fetch_observations_paged(client, table_id=table_id, limit=limit, period=None, contents_code=contents_code)


def _discover_two_latest_distinct_periods(
    client: Client,
    table_id: str,
    *,
    contents_code: str | None = None,
) -> tuple[list[str], int]:
    """
    Scan observations ordered by period descending until two distinct period strings are found.
    Returns ([p_older, p_newer] chronological), rows_scanned.
    """
    found_desc: list[str] = []
    seen: set[str] = set()
    offset = 0
    chunk = 1000
    scanned = 0
    max_scan = 200_000
    while len(found_desc) < 2 and scanned < max_scan:
        q = (
            client.table("statistical_observations")
            .select("period")
            .eq("table_id", table_id)
            .order("period", desc=True)
        )
        if contents_code is not None:
            q = q.eq("contents_code", contents_code)
        res = q.range(offset, offset + chunk - 1).execute()
        batch = res.data or []
        if not batch:
            break
        for r in batch:
            scanned += 1
            p = r.get("period")
            if p is None:
                continue
            ps = str(p)
            if ps not in seen:
                seen.add(ps)
                found_desc.append(ps)
                if len(found_desc) >= 2:
                    break
        if len(batch) < chunk:
            break
        offset += chunk
    if len(found_desc) < 2:
        return [], scanned
    # found_desc[0] is newest, found_desc[1] is second-newest (by scan order)
    p_newer = found_desc[0]
    p_older = found_desc[1]
    return sorted([p_older, p_newer], key=_period_sort_key), scanned


def _fetch_balanced_change_rows(
    client: Client,
    table_id: str,
    limit_per_period: int,
    *,
    contents_code: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Fetch up to `limit_per_period` rows for each of the two latest distinct periods, then concatenate.
    """
    periods, discovery_scanned = _discover_two_latest_distinct_periods(client, table_id, contents_code=contents_code)
    meta: dict[str, Any] = {
        "periods_selected": periods,
        "per_period_counts": {},
        "total_rows": 0,
        "discovery_rows_scanned": discovery_scanned,
    }
    if len(periods) < 2:
        return [], meta
    p0, p1 = periods[0], periods[1]
    rows0 = _fetch_observations_paged(
        client, table_id=table_id, limit=limit_per_period, period=p0, contents_code=contents_code
    )
    rows1 = _fetch_observations_paged(
        client, table_id=table_id, limit=limit_per_period, period=p1, contents_code=contents_code
    )
    merged = rows0 + rows1
    meta["per_period_counts"] = {p0: len(rows0), p1: len(rows1)}
    meta["total_rows"] = len(merged)
    return merged, meta


def _group_two_period_change(
    rows: list[dict[str, Any]],
    stats: RunStats,
) -> list[tuple[dict, dict, float | None, float, str, str]]:
    """
    Group rows by (table_id, contents_code, slice_key), pick two latest periods.
    Returns list of (row_start, row_end, pct, abs_chg, p_start, p_end).
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dims = row.get("dimensions_json") or {}
        if not isinstance(dims, dict):
            continue
        cc = row.get("contents_code")
        cc_s = str(cc) if cc is not None else ""
        gk = f"{row.get('table_id')}|{cc_s}|{_slice_key(dims)}"
        groups[gk].append(row)

    results: list[tuple[dict, dict, float | None, float, str, str]] = []
    for _gk, items in groups.items():
        by_period: dict[str, dict[str, Any]] = {}
        for it in items:
            p = it.get("period")
            if p is None:
                continue
            ps = str(p)
            by_period[ps] = it
        periods = sorted(by_period.keys(), key=_period_sort_key)
        if len(periods) < 2:
            stats.skipped_missing_prior_period += 1
            continue
        p_end = periods[-1]
        p_start = periods[-2]
        r_end = by_period[p_end]
        r_start = by_period[p_start]
        v_end = r_end.get("value")
        v_start = r_start.get("value")
        if v_end is None or v_start is None:
            stats.skipped_missing_prior_period += 1
            continue
        try:
            f_end = float(v_end)
            f_start = float(v_start)
        except (TypeError, ValueError):
            stats.skipped_missing_prior_period += 1
            continue
        abs_chg = f_end - f_start
        if f_start == 0:
            stats.skipped_zero_baseline += 1
            continue
        pct = (abs_chg / f_start) * 100.0
        results.append((r_start, r_end, pct, abs_chg, p_start, p_end))
    return results


def _emit_change_signals(
    signal_type: str,
    label_fn: Any,
    pairs: list[tuple[dict, dict, float | None, float, str, str]],
    stats: RunStats,
    ctx: QualityContext,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r_start, r_end, pct, abs_chg, p_start, p_end in pairs:
        f_start = float(r_start.get("value"))
        labels_s = r_start.get("dimension_labels_json")
        labels_e = r_end.get("dimension_labels_json")
        if ctx.exclude_unspecified and (
            _has_unspecified_category(labels_s) or _has_unspecified_category(labels_e)
        ):
            stats.skipped_unspecified_category += 1
            continue
        if f_start < ctx.min_baseline:
            stats.skipped_low_baseline += 1
            continue

        dims = r_end.get("dimensions_json") or {}
        labels = r_end.get("dimension_labels_json") or {}
        if not isinstance(dims, dict):
            dims = {}
        if not isinstance(labels, dict):
            labels = {}
        direction = _direction_from_pct(pct)
        cc, cc_lab = _resolve_contents_code_fields(r_end)
        base_label = label_fn(r_start, r_end, p_start, p_end, direction)
        if cc_lab:
            signal_label = f"{base_label} — ContentsCode: {cc_lab}" + (f" ({cc})" if cc and cc != cc_lab else "")
        else:
            signal_label = base_label
        note = (
            f"Compared periods {p_start}→{p_end} on the same dimension slice (excluding time key); "
            f"percent change {pct:.2f}% ({direction})."
        )
        if cc_lab:
            note += f" ContentsCode (label): {cc_lab}."
            if cc and cc != cc_lab:
                note += f" Code: {cc}."
        qf = [
            "preview_not_product_ready",
            "two_period_change",
            f"min_baseline_met(>={ctx.min_baseline})",
        ]
        if ctx.exclude_unspecified:
            qf.append("no_blocked_unspecified_phrase_in_labels")
        row_out = {
            "signal_type": signal_type,
            "signal_label": signal_label,
            "table_id": r_end.get("table_id"),
            "periods_compared": f"{p_start}→{p_end}",
            "value_start": float(r_start.get("value")),
            "value_end": float(r_end.get("value")),
            "absolute_change": abs_chg,
            "percent_change": round(pct, 6),
            "direction_label": direction,
            "confidence_category": "verified_statistical",
            "confidence_score": 0.9,
            "source_observation_ids": f"{r_start.get('id')},{r_end.get('id')}",
            "source_table": r_end.get("table_id"),
            "dimensions_json": json.dumps(dims, ensure_ascii=False),
            "dimension_labels_json": json.dumps(labels, ensure_ascii=False),
            "explainability_note": note,
            "lineage_json": _lineage_json(r_start, r_end),
            "quality_flags": json.dumps(qf, ensure_ascii=False),
            "min_baseline": ctx.min_baseline,
            "contents_code": cc,
            "contents_code_label": cc_lab,
        }
        out.append(row_out)
        stats.preview_signals_generated += 1
    return out


def _employment_label(_a: dict, _b: dict, ps: str, pe: str, direction: str) -> str:
    return f"Employment count change ({ps}→{pe}): {direction}"


def _regional_label(_a: dict, _b: dict, ps: str, pe: str, direction: str) -> str:
    return f"Regional + education/fagfelt employment ({ps}→{pe}): {direction}"


def _industry_label(_a: dict, _b: dict, ps: str, pe: str, direction: str) -> str:
    return f"Industry (NACE) + education employment ({ps}→{pe}): {direction}"


def _filter_rows(
    rows: list[dict[str, Any]],
    predicate: Any,
) -> list[dict[str, Any]]:
    return [r for r in rows if predicate(r)]


def _has_keys(dims: dict[str, Any], keys: set[str], any_of: set[str] | None = None) -> bool:
    if any_of:
        return keys.issubset(dims.keys()) and any(k in dims for k in any_of)
    return keys.issubset(dims.keys())


def run_employment_count_change(
    get_rows: Callable[[str], list[dict[str, Any]]],
    table_filter: str | None,
    stats: RunStats,
    ctx: QualityContext,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for tid in TABLES_EMPLOYMENT_CHANGE:
        if table_filter and tid != table_filter:
            continue
        rows = get_rows(tid)
        stats.rows_read += len(rows)
        pairs = _group_two_period_change(rows, stats)
        stats.candidate_pairs += len(pairs)
        signals.extend(
            _emit_change_signals("employment_count_change", _employment_label, pairs, stats, ctx)
        )
    return signals


def run_regional_education(
    get_rows: Callable[[str], list[dict[str, Any]]],
    table_filter: str | None,
    stats: RunStats,
    ctx: QualityContext,
) -> list[dict[str, Any]]:
    if table_filter and table_filter != TABLE_REGIONAL:
        return []
    rows = get_rows(TABLE_REGIONAL)
    stats.rows_read += len(rows)

    def pred(r: dict[str, Any]) -> bool:
        d = r.get("dimensions_json") or {}
        if not isinstance(d, dict):
            return False
        return _has_keys(d, {"Region"}, {"Fagfelt", "UtdNivaa"})

    filtered = _filter_rows(rows, pred)
    pairs = _group_two_period_change(filtered, stats)
    stats.candidate_pairs += len(pairs)
    return _emit_change_signals(
        "regional_education_employment_signal",
        _regional_label,
        pairs,
        stats,
        ctx,
    )


def run_industry_education(
    get_rows: Callable[[str], list[dict[str, Any]]],
    table_filter: str | None,
    stats: RunStats,
    ctx: QualityContext,
) -> list[dict[str, Any]]:
    if table_filter and table_filter != TABLE_INDUSTRY:
        return []
    rows = get_rows(TABLE_INDUSTRY)
    stats.rows_read += len(rows)

    def pred(r: dict[str, Any]) -> bool:
        d = r.get("dimensions_json") or {}
        if not isinstance(d, dict):
            return False
        return _has_keys(d, {"NACE2007"}, {"Fagfelt", "UtdNivaa"})

    filtered = _filter_rows(rows, pred)
    pairs = _group_two_period_change(filtered, stats)
    stats.candidate_pairs += len(pairs)
    return _emit_change_signals(
        "industry_education_employment_signal",
        _industry_label,
        pairs,
        stats,
        ctx,
    )


def run_occupation_structure(
    get_rows: Callable[[str], list[dict[str, Any]]],
    table_filter: str | None,
    stats: RunStats,
    ctx: QualityContext,
) -> list[dict[str, Any]]:
    if table_filter and table_filter != TABLE_OCCUPATION:
        return []
    rows = get_rows(TABLE_OCCUPATION)
    stats.rows_read += len(rows)
    periods = sorted({str(r["period"]) for r in rows if r.get("period") is not None}, key=_period_sort_key)
    if not periods:
        return []
    latest = periods[-1]
    distinct_periods = len(periods)

    # Aggregate by Yrke (occupation) for latest period
    agg: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if str(r.get("period")) != latest:
            continue
        if ctx.exclude_unspecified and _has_unspecified_category(r.get("dimension_labels_json")):
            stats.skipped_unspecified_category += 1
            continue
        dims = r.get("dimensions_json") or {}
        if not isinstance(dims, dict) or "Yrke" not in dims:
            continue
        yk = str(dims.get("Yrke"))
        agg[yk].append(r)

    out: list[dict[str, Any]] = []
    for _yk, items in agg.items():
        total = 0.0
        ids: list[str] = []
        dims_ref = (items[0].get("dimensions_json") or {}) if items else {}
        labels_ref = (items[0].get("dimension_labels_json") or {}) if items else {}
        for it in items:
            v = it.get("value")
            if v is None:
                continue
            try:
                total += float(v)
            except (TypeError, ValueError):
                continue
            ids.append(str(it.get("id")))
        if not ids:
            continue
        if distinct_periods >= 2:
            direction_note = "multiple periods available in sample; snapshot uses latest period only for structure"
            direction = "structure_snapshot"
        else:
            direction_note = "single period in sample; no trend inferred"
            direction = "structure_snapshot"
        r0 = items[0]
        cc, cc_lab = _resolve_contents_code_fields(r0)
        sig_lab = f"Occupation structure ({latest}): {direction}"
        if cc_lab:
            sig_lab = f"{sig_lab} — ContentsCode: {cc_lab}" + (f" ({cc})" if cc and cc != cc_lab else "")
        note = (
            f"Occupation slice total employment-related value for period {latest} "
            f"({direction_note})."
        )
        if cc_lab:
            note += f" ContentsCode (label): {cc_lab}."
            if cc and cc != cc_lab:
                note += f" Code: {cc}."
        qf = ["preview_not_product_ready", "structure_snapshot"]
        if ctx.exclude_unspecified:
            qf.append("no_blocked_unspecified_phrase_in_labels_rows")
        out.append(
            {
                "signal_type": "occupation_structure_signal",
                "signal_label": sig_lab,
                "table_id": TABLE_OCCUPATION,
                "periods_compared": latest,
                "value_start": "",
                "value_end": total,
                "absolute_change": "",
                "percent_change": "",
                "direction_label": direction,
                "confidence_category": "verified_statistical",
                "confidence_score": 1.0,
                "source_observation_ids": ",".join(ids),
                "source_table": TABLE_OCCUPATION,
                "dimensions_json": json.dumps(dims_ref, ensure_ascii=False),
                "dimension_labels_json": json.dumps(labels_ref, ensure_ascii=False),
                "explainability_note": note,
                "lineage_json": _lineage_json(r0, None, {"aggregation": "sum(values) for same Yrke slice latest period"}),
                "quality_flags": json.dumps(qf, ensure_ascii=False),
                "min_baseline": "",
                "contents_code": cc,
                "contents_code_label": cc_lab,
            }
        )
        stats.preview_signals_generated += 1
    return out


def run_education_workforce(
    get_rows: Callable[[str], list[dict[str, Any]]],
    table_filter: str | None,
    stats: RunStats,
    ctx: QualityContext,
) -> list[dict[str, Any]]:
    if table_filter and table_filter != TABLE_WORKFORCE:
        return []
    rows = get_rows(TABLE_WORKFORCE)
    stats.rows_read += len(rows)
    periods = sorted({str(r["period"]) for r in rows if r.get("period") is not None}, key=_period_sort_key)
    if not periods:
        return []
    latest = periods[-1]

    agg: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if str(r.get("period")) != latest:
            continue
        if ctx.exclude_unspecified and _has_unspecified_category(r.get("dimension_labels_json")):
            stats.skipped_unspecified_category += 1
            continue
        dims = r.get("dimensions_json") or {}
        if not isinstance(dims, dict):
            continue
        if "UtdNivaa" not in dims:
            continue
        sub_keys = {"UtdNivaa", "HeltidDeltid"} & set(dims.keys())
        sub = _dims_subset(dims, sub_keys)
        gk = json.dumps(sorted(sub.items()), ensure_ascii=False)
        agg[gk].append(r)

    out: list[dict[str, Any]] = []
    for _gk, items in agg.items():
        total = 0.0
        ids: list[str] = []
        dims_ref = (items[0].get("dimensions_json") or {}) if items else {}
        labels_ref = (items[0].get("dimension_labels_json") or {}) if items else {}
        sub_dims = _dims_subset(dims_ref, {"UtdNivaa", "HeltidDeltid"} & set(dims_ref.keys()))
        sub_labels = _labels_for(sub_dims, labels_ref if isinstance(labels_ref, dict) else {})
        for it in items:
            v = it.get("value")
            if v is None:
                continue
            try:
                total += float(v)
            except (TypeError, ValueError):
                continue
            ids.append(str(it.get("id")))
        if not ids:
            continue
        r0 = items[0]
        cc, cc_lab = _resolve_contents_code_fields(r0)
        sig_lab = f"Education/workforce snapshot ({latest})"
        if cc_lab:
            sig_lab = f"{sig_lab} — ContentsCode: {cc_lab}" + (f" ({cc})" if cc and cc != cc_lab else "")
        note = (
            f"Education level × employment type workforce snapshot for period {latest} "
            f"(summed observations sharing slice keys)."
        )
        if cc_lab:
            note += f" ContentsCode (label): {cc_lab}."
            if cc and cc != cc_lab:
                note += f" Code: {cc}."
        qf = ["preview_not_product_ready", "workforce_snapshot"]
        if ctx.exclude_unspecified:
            qf.append("no_blocked_unspecified_phrase_in_labels_rows")
        out.append(
            {
                "signal_type": "education_level_workforce_signal",
                "signal_label": sig_lab,
                "table_id": TABLE_WORKFORCE,
                "periods_compared": latest,
                "value_start": "",
                "value_end": total,
                "absolute_change": "",
                "percent_change": "",
                "direction_label": "snapshot",
                "confidence_category": "verified_statistical",
                "confidence_score": 1.0,
                "source_observation_ids": ",".join(ids),
                "source_table": TABLE_WORKFORCE,
                "dimensions_json": json.dumps(sub_dims, ensure_ascii=False),
                "dimension_labels_json": json.dumps(sub_labels, ensure_ascii=False),
                "explainability_note": note,
                "lineage_json": _lineage_json(r0, None, {"aggregation": "sum(values) per UtdNivaa×HeltidDeltid latest period"}),
                "quality_flags": json.dumps(qf, ensure_ascii=False),
                "min_baseline": "",
                "contents_code": cc,
                "contents_code_label": cc_lab,
            }
        )
        stats.preview_signals_generated += 1
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            row = {k: r.get(k, "") for k in CSV_COLUMNS}
            for k in ("value_start", "value_end", "absolute_change", "percent_change", "confidence_score", "min_baseline"):
                if row[k] == "":
                    continue
                if row[k] is None:
                    row[k] = ""
                elif isinstance(row[k], float):
                    row[k] = repr(row[k]) if row[k] != int(row[k]) else str(int(row[k]))
                else:
                    row[k] = str(row[k])
            w.writerow(row)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Preview verified_statistical signals from observations (no DB writes).")
    p.add_argument("--limit", type=int, default=5000, help="Max observations to read per table (default 5000).")
    p.add_argument(
        "--balanced-periods",
        action="store_true",
        help=(
            "For change-style generators only: discover the two latest distinct periods, "
            "then fetch up to --limit rows per period (not one mixed cap). Snapshot generators "
            "still use a single sequential cap per table."
        ),
    )
    p.add_argument(
        "--min-baseline",
        type=float,
        default=100.0,
        help="For two-period change signals: skip emission when value_start is below this (default 100).",
    )
    mx = p.add_mutually_exclusive_group()
    mx.add_argument(
        "--exclude-unspecified",
        dest="exclude_unspecified",
        action="store_true",
        help="Exclude rows/slices whose dimension labels match reserved unspecified-style phrases (default).",
    )
    mx.add_argument(
        "--include-unspecified",
        dest="exclude_unspecified",
        action="store_false",
        help="Do not filter on unspecified/unknown-style phrases in dimension_labels_json.",
    )
    p.set_defaults(exclude_unspecified=True)
    p.add_argument(
        "--contents-code",
        type=str,
        default=None,
        metavar="CODE",
        help="Restrict observations to this contents_code (exact match); also applied in balanced-period discovery/fetch.",
    )
    p.add_argument("--table", type=str, default=None, help="Restrict to one SSB table_id (e.g. 11615).")
    p.add_argument(
        "--signal-type",
        type=str,
        default="all",
        choices=SIGNAL_TYPES,
        help="Which preview generator to run.",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    if args.limit < 1:
        print("ERROR: --limit must be >= 1", file=sys.stderr)
        return 1
    if args.min_baseline < 0:
        print("ERROR: --min-baseline must be >= 0", file=sys.stderr)
        return 1

    cc_filter = (args.contents_code or "").strip() or None
    quality_ctx = QualityContext(
        min_baseline=float(args.min_baseline),
        exclude_unspecified=bool(args.exclude_unspecified),
        contents_code_filter=cc_filter,
    )

    print("=" * 60)
    print("Preview verified statistical signals (Supabase read-only)")
    print(f"  script_version: {SCRIPT_VERSION}")
    print(f"  limit per table: {args.limit}")
    if args.balanced_periods:
        print("  balanced_periods: ON (change-style: --limit per period; snapshots: unchanged)")
    else:
        print("  balanced_periods: OFF")
    print(f"  min_baseline (change emit): {quality_ctx.min_baseline}")
    print(f"  exclude_unspecified: {quality_ctx.exclude_unspecified}")
    print(f"  contents_code filter: {cc_filter or '(none)'}")
    print(f"  table filter:   {args.table or '(none)'}")
    print(f"  signal_type:   {args.signal_type}")
    print(f"  growth pct >=: {GROWTH_PCT_THRESHOLD}")
    print(f"  decline pct <=: {DECLINE_PCT_THRESHOLD}")
    print("=" * 60)

    total_stats = RunStats()
    all_signals: list[dict[str, Any]] = []

    try:
        client = _load_client()
    except RuntimeError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1

    row_cache: dict[str, list[dict[str, Any]]] = {}
    balanced_period_meta: dict[str, dict[str, Any]] = {}

    def obs_cache_key(table_id: str) -> str:
        return f"{table_id}\x1fcc={cc_filter or '*'}"

    def get_rows_sequential(table_id: str) -> list[dict[str, Any]]:
        if args.table and args.table != table_id:
            return []
        ck = obs_cache_key(table_id)
        if ck not in row_cache:
            row_cache[ck] = _fetch_observations(client, table_id, args.limit, contents_code=cc_filter)
        return row_cache[ck]

    def get_rows_balanced_change(table_id: str) -> list[dict[str, Any]]:
        if args.table and args.table != table_id:
            return []
        ck = obs_cache_key(table_id)
        if ck not in row_cache:
            rows, meta = _fetch_balanced_change_rows(client, table_id, args.limit, contents_code=cc_filter)
            row_cache[ck] = rows
            balanced_period_meta[ck] = meta
            print(
                f"[balanced-periods] table_id={table_id} selected_periods={meta['periods_selected']} "
                f"rows_per_period={meta['per_period_counts']} total_rows={meta['total_rows']} "
                f"(discovery rows scanned={meta['discovery_rows_scanned']})"
            )
        return row_cache[ck]

    st = args.signal_type
    if args.balanced_periods:
        if st in ("occupation_structure_signal", "education_level_workforce_signal"):
            print(
                "NOTE: --balanced-periods applies only to change-style signal types; "
                "this run uses sequential fetch per table (same as without the flag)."
            )
        get_change_rows = get_rows_balanced_change
        get_default_rows = get_rows_sequential
    else:
        get_change_rows = get_rows_sequential
        get_default_rows = get_rows_sequential

    def run_if(name: str, fn: Any, getter: Callable[[str], list[dict[str, Any]]]) -> None:
        nonlocal all_signals
        if st not in ("all", name):
            return
        s = RunStats()
        rows = fn(getter, args.table, s, quality_ctx)
        all_signals.extend(rows)
        total_stats.merge(s)
        print(
            f"[{name}] preview rows: {len(rows)} | "
            f"rows_read={s.rows_read} candidate_pairs={s.candidate_pairs} "
            f"generated={s.preview_signals_generated} "
            f"skip_no_prior={s.skipped_missing_prior_period} skip_zero={s.skipped_zero_baseline} "
            f"skip_low_baseline={s.skipped_low_baseline} skip_unspecified={s.skipped_unspecified_category}"
        )

    run_if("employment_count_change", run_employment_count_change, get_change_rows)
    run_if("regional_education_employment_signal", run_regional_education, get_change_rows)
    run_if("industry_education_employment_signal", run_industry_education, get_change_rows)
    run_if("occupation_structure_signal", run_occupation_structure, get_default_rows)
    run_if("education_level_workforce_signal", run_education_workforce, get_default_rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "signal_preview_rows.csv"
    json_path = OUT_DIR / "signal_preview_summary.json"

    _write_csv(csv_path, all_signals)

    summary = {
        "script_version": SCRIPT_VERSION,
        "timestamp_utc": _now_utc(),
        "thresholds": {
            "growth_percent_min": GROWTH_PCT_THRESHOLD,
            "decline_percent_max": DECLINE_PCT_THRESHOLD,
        },
        "confidence_rules": {
            "two_period_change": 0.9,
            "direct_snapshot": 1.0,
        },
        "args": {
            "limit": args.limit,
            "table": args.table,
            "signal_type": args.signal_type,
            "balanced_periods": args.balanced_periods,
            "min_baseline": quality_ctx.min_baseline,
            "exclude_unspecified": quality_ctx.exclude_unspecified,
            "contents_code": cc_filter,
        },
        "counts": {
            "rows_read": total_stats.rows_read,
            "candidate_pairs": total_stats.candidate_pairs,
            "preview_signals_generated": total_stats.preview_signals_generated,
            "skipped_missing_prior_period": total_stats.skipped_missing_prior_period,
            "skipped_zero_baseline": total_stats.skipped_zero_baseline,
            "skipped_low_baseline": total_stats.skipped_low_baseline,
            "skipped_unspecified_category": total_stats.skipped_unspecified_category,
        },
        "output_csv": str(csv_path.relative_to(ROOT)),
        "preview_row_count": len(all_signals),
        "observations_fetched_by_table": {tid: len(rows) for tid, rows in row_cache.items()},
    }
    if args.balanced_periods and balanced_period_meta:
        summary["balanced_period_fetch"] = balanced_period_meta
    datasets_meta: list[dict[str, Any]] = []
    try:
        if row_cache:
            tids = list(row_cache.keys())
            ds = (
                client.table("statistical_datasets")
                .select("id, table_id, title, slug")
                .in_("table_id", tids)
                .execute()
            )
            datasets_meta = ds.data or []
    except Exception as exc:
        datasets_meta = [{"error": str(exc)}]
    summary["statistical_datasets_snapshot"] = datasets_meta

    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("\n--- Summary ---")
    print(f"  rows read (sum over generators):     {total_stats.rows_read}")
    print(f"  candidate pairs (two-period groups): {total_stats.candidate_pairs}")
    print(f"  preview signals generated:           {total_stats.preview_signals_generated}")
    print(f"  skipped missing prior period:        {total_stats.skipped_missing_prior_period}")
    print(f"  skipped zero baseline:               {total_stats.skipped_zero_baseline}")
    print(f"  skipped low baseline:                {total_stats.skipped_low_baseline}")
    print(f"  skipped unspecified category:        {total_stats.skipped_unspecified_category}")
    if row_cache:
        print(f"  observations fetched (cache keys): { {k: len(v) for k, v in row_cache.items()} }")
    print(f"\nWrote: {csv_path}")
    print(f"Wrote: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

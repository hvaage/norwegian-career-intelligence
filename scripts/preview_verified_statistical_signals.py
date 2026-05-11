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

Quality filters (``--min-baseline``, ``--min-absolute-change``, ``--exclude-unspecified``,
``--exclude-small-slices``, ``--exclude-total-categories``, ``--contents-code``) trim noisy
preview rows before manual review. v1.3+ adds temporal pairing checks, slice/unit/contents
consistency, hardened ``lineage_json``, deterministic ordering and ``signal_deterministic_hash``,
``signal_quality_score`` / ``quality_reasoning_json``, expanded explainability fields, optional
``--strict-validation`` and ``--preview-report-only``, and ``review_samples/*.csv`` for spot review.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from supabase import Client, create_client

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "processed" / "signal_preview"
REVIEW_SAMPLES_DIR = OUT_DIR / "review_samples"
SCRIPT_VERSION = "preview_verified_statistical_signals_v1.3"
SIGNAL_LOGIC_VERSION = "verified_stat_preview_emit_v1.3.0"

SELECT_OBS_COLS = (
    "id, table_id, period, value, unit, contents_code, dimensions_json, dimension_labels_json, "
    "statistical_dataset_id, dataset_version_id, source_file, normalization_version, transformation_version, "
    "confidence_category, confidence_score, metadata_json, observation_signature, ingestion_batch_id"
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
    "period_type",
    "period_granularity",
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
    "explainability_summary_json",
    "lineage_json",
    "quality_flags",
    "signal_quality_score",
    "quality_reasoning_json",
    "signal_deterministic_hash",
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
    skipped_low_absolute_change: int = 0
    skipped_both_periods_below_baseline: int = 0
    skipped_total_categories: int = 0
    skipped_invalid_period_pairing: int = 0
    skipped_lineage_failures: int = 0
    skipped_source_observation_missing: int = 0
    skipped_slice_mismatch: int = 0
    skipped_unit_mismatch: int = 0
    skipped_dimension_mismatch: int = 0
    skipped_invalid_aggregation: int = 0
    strict_validation_abort: bool = False
    strict_validation_messages: list[str] = field(default_factory=list)

    def merge(self, other: RunStats) -> None:
        self.rows_read += other.rows_read
        self.candidate_pairs += other.candidate_pairs
        self.preview_signals_generated += other.preview_signals_generated
        self.skipped_missing_prior_period += other.skipped_missing_prior_period
        self.skipped_zero_baseline += other.skipped_zero_baseline
        self.skipped_low_baseline += other.skipped_low_baseline
        self.skipped_unspecified_category += other.skipped_unspecified_category
        self.skipped_low_absolute_change += other.skipped_low_absolute_change
        self.skipped_both_periods_below_baseline += other.skipped_both_periods_below_baseline
        self.skipped_total_categories += other.skipped_total_categories
        self.skipped_invalid_period_pairing += other.skipped_invalid_period_pairing
        self.skipped_lineage_failures += other.skipped_lineage_failures
        self.skipped_source_observation_missing += other.skipped_source_observation_missing
        self.skipped_slice_mismatch += other.skipped_slice_mismatch
        self.skipped_unit_mismatch += other.skipped_unit_mismatch
        self.skipped_dimension_mismatch += other.skipped_dimension_mismatch
        self.skipped_invalid_aggregation += other.skipped_invalid_aggregation
        self.strict_validation_abort = self.strict_validation_abort or other.strict_validation_abort
        self.strict_validation_messages.extend(other.strict_validation_messages)


@dataclass
class PreviewConfig:
    min_baseline: float
    min_absolute_change: float
    exclude_unspecified: bool
    contents_code_filter: str | None
    exclude_small_slices: bool
    exclude_total_categories: bool
    strict_validation: bool
    preview_report_only: bool
    generation_timestamp_utc: str


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


# Align with scripts/import_ssb_observations.py total detection (labels + codes).
TOTAL_LABEL_HINTS_PREVIEW = (
    "i alt",
    "alle",
    "begge kjønn",
    "begge kjonn",
    "alle yrker",
    "alle næringer",
    "alle naeringer",
    "hele landet",
    "hele norge",
    "total",
    "nasjonalt",
)
_PERIOD_QUARTER_RE = re.compile(r"^\d{4}K[1-4]$", re.IGNORECASE)


def _classify_period(period: str | None) -> tuple[str, str]:
    """Return (period_type, period_granularity)."""
    if period is None:
        return "unknown", "unknown"
    s = str(period).strip()
    if len(s) == 4 and s.isdigit():
        return "calendar_year", "year"
    if _PERIOD_QUARTER_RE.match(s):
        return "ssb_quarter", "quarter"
    if re.fullmatch(r"\d{6}", s):
        return "year_month", "month"
    return "other", "unknown"


def _periods_pair_valid_for_change(p_start: str, p_end: str, stats: RunStats, cfg: PreviewConfig) -> bool:
    t0, g0 = _classify_period(p_start)
    t1, g1 = _classify_period(p_end)
    if g0 == "unknown" or g1 == "unknown" or g0 != g1:
        stats.skipped_invalid_period_pairing += 1
        if cfg.strict_validation:
            stats.strict_validation_abort = True
            stats.strict_validation_messages.append(
                f"invalid_period_pairing:{p_start}|{p_end}|{g0}|{g1}"
            )
        return False
    return True


def _non_time_dimensions(dims: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in dims.items():
        if k in TIME_KEYS or k == "ContentsCode":
            continue
        if v is None:
            continue
        out[str(k)] = v
    return dict(sorted(out.items(), key=lambda kv: kv[0]))


def _canonical_slice_dimension_json(dims: dict[str, Any]) -> str:
    return json.dumps(_non_time_dimensions(dims), ensure_ascii=False, separators=(",", ":"))


def _slice_non_time_equal(dims_a: dict[str, Any], dims_b: dict[str, Any]) -> bool:
    return _non_time_dimensions(dims_a) == _non_time_dimensions(dims_b)


def _row_suggests_total_category(dims: dict[str, Any], labels: Any) -> bool:
    if not isinstance(dims, dict):
        return False
    lab = labels if isinstance(labels, dict) else {}
    for k, v in dims.items():
        if k in TIME_KEYS or k == "ContentsCode":
            continue
        if v is None:
            continue
        code = str(v).strip().upper()
        if code == "TOT":
            return True
        lbl = lab.get(k)
        if lbl is not None:
            low = str(lbl).strip().lower()
            if any(h in low for h in TOTAL_LABEL_HINTS_PREVIEW):
                return True
    return False


def _strict_note(stats: RunStats, cfg: PreviewConfig, msg: str) -> None:
    if cfg.strict_validation:
        stats.strict_validation_abort = True
        stats.strict_validation_messages.append(msg)


def _build_lineage_object(
    r_start: dict[str, Any],
    r_end: dict[str, Any] | None,
    cfg: PreviewConfig,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ds_ids: list[str] = []
    dv_ids: list[str] = []
    for r in (r_start, r_end):
        if not r:
            continue
        sid = r.get("statistical_dataset_id")
        if sid is not None and str(sid) not in ds_ids:
            ds_ids.append(str(sid))
        vid = r.get("dataset_version_id")
        if vid is not None and str(vid) not in dv_ids:
            dv_ids.append(str(vid))
    sigs: list[str] = []
    for r in (r_start, r_end):
        if not r:
            continue
        s = _observation_signature_for_row(r)
        if s:
            sigs.append(s)
    meta_a = r_start.get("metadata_json") or {}
    importer_ver = None
    if isinstance(meta_a, dict):
        importer_ver = meta_a.get("importer_version")
    base: dict[str, Any] = {
        "preview_script_version": SCRIPT_VERSION,
        "signal_logic_version": SIGNAL_LOGIC_VERSION,
        "generator": "preview_verified_statistical_signals.py",
        "generation_timestamp_utc": cfg.generation_timestamp_utc,
        "importer_version": importer_ver or "unknown",
        "normalization_version": r_start.get("normalization_version"),
        "transformation_version": r_start.get("transformation_version"),
        "ingestion_batch_id": r_start.get("ingestion_batch_id"),
        "statistical_dataset_id": r_start.get("statistical_dataset_id"),
        "dataset_version_id": r_start.get("dataset_version_id"),
        "source_dataset_ids": ds_ids,
        "source_dataset_version_ids": dv_ids,
        "source_observation_signature_count": len(sigs),
        "source_file": r_start.get("source_file"),
        "table_id": r_start.get("table_id"),
    }
    if r_end:
        base["source_file_b"] = r_end.get("source_file")
        base["dataset_version_id_b"] = r_end.get("dataset_version_id")
    if extra:
        base.update(extra)
    return base


def _lineage_json_from_obj(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _observation_signature_for_row(row: dict[str, Any]) -> str:
    s = row.get("observation_signature")
    if isinstance(s, str) and s.strip():
        return s.strip()
    meta = row.get("metadata_json") or {}
    if isinstance(meta, dict):
        inner = meta.get("observation_signature")
        if isinstance(inner, str) and inner.strip():
            return inner.strip()
    return ""


def _lineage_is_nonempty(obj: dict[str, Any]) -> bool:
    required_any = (
        obj.get("source_file"),
        obj.get("table_id"),
        obj.get("normalization_version"),
        obj.get("transformation_version"),
    )
    return any(x is not None and str(x).strip() for x in required_any)


def _compute_change_quality(
    *,
    f_start: float,
    f_end: float,
    pct: float,
    abs_chg: float,
    cfg: PreviewConfig,
    unspecified_hit: bool,
    total_hit: bool,
    labels_complete: bool,
    units_match: bool,
) -> tuple[float, dict[str, Any], list[str]]:
    flags: list[str] = []
    parts: dict[str, float] = {}
    # Baseline size (0..1), capped
    b = min(1.0, max(0.0, (f_start / max(1.0, cfg.min_baseline * 3))))
    parts["baseline_size"] = round(b, 4)
    # Absolute change vs threshold
    ac = min(1.0, max(0.0, abs(abs_chg) / max(1e-9, cfg.min_absolute_change * 5)))
    parts["absolute_change_magnitude"] = round(ac, 4)
    # Completeness
    c = 1.0 if labels_complete else 0.45
    parts["label_completeness"] = round(c, 4)
    # Unit
    u = 1.0 if units_match else 0.0
    parts["unit_consistency"] = round(u, 4)
    score = 0.34 * b + 0.22 * ac + 0.22 * c + 0.22 * u
    if unspecified_hit:
        score *= 0.55
        flags.append("unspecified_dimension")
    if total_hit:
        score *= 0.35
        flags.append("total_category")
    if not labels_complete:
        flags.append("missing_labels")
    if not units_match:
        flags.append("mixed_units")
    if f_start < cfg.min_baseline:
        flags.append("low_baseline")
    if abs(abs_chg) < cfg.min_absolute_change:
        flags.append("noisy_change")
    if abs(pct) >= 40 and f_start < 250:
        flags.append("unstable_slice")
        score *= 0.75
    if score < 0.55:
        flags.append("low_confidence_preview_only")
    score = max(0.0, min(1.0, round(score, 4)))
    reasoning = {
        "components": parts,
        "weights_note": "deterministic_preview_v1.3: baseline 0.34, abs_change 0.22, labels 0.22, units 0.22; multiplicative penalties",
        "penalty_flags": [f for f in flags if f not in ("noisy_change",)],
    }
    return score, reasoning, sorted(set(flags))


def _explainability_summary_change(
    *,
    signal_type: str,
    dims: dict[str, Any],
    labels: dict[str, Any],
    p_start: str,
    p_end: str,
    period_type: str,
    period_granularity: str,
    direction: str,
    pct: float,
    cfg: PreviewConfig,
    comparison_mode: str,
) -> dict[str, Any]:
    included = sorted(_non_time_dimensions(dims).keys())
    return {
        "signal_type": signal_type,
        "comparison_mode": comparison_mode,
        "periods": {"start": p_start, "end": p_end, "period_type": period_type, "period_granularity": period_granularity},
        "dimensions_included": included,
        "dimensions_excluded_from_slice_identity": sorted(TIME_KEYS | {"ContentsCode"}),
        "thresholds_applied": {
            "min_baseline": cfg.min_baseline,
            "min_absolute_change": cfg.min_absolute_change,
            "growth_pct_min": GROWTH_PCT_THRESHOLD,
            "decline_pct_max": DECLINE_PCT_THRESHOLD,
            "exclude_unspecified": cfg.exclude_unspecified,
            "exclude_small_slices": cfg.exclude_small_slices,
            "exclude_total_categories": cfg.exclude_total_categories,
        },
        "direction_rule": f"percent_change>={GROWTH_PCT_THRESHOLD} growth; <={DECLINE_PCT_THRESHOLD} decline; else stable",
        "direction_result": direction,
        "percent_change": round(pct, 6),
        "dimension_labels_present": {k: (labels.get(k) is not None) for k in included if isinstance(labels, dict)},
    }


def _deterministic_signal_hash(
    *,
    signal_type: str,
    dims: dict[str, Any],
    p_start: str,
    p_end: str,
    obs_ids: tuple[str, ...],
) -> str:
    payload = {
        "signal_logic_version": SIGNAL_LOGIC_VERSION,
        "signal_type": signal_type,
        "slice_dims": _non_time_dimensions(dims),
        "period_start": str(p_start),
        "period_end": str(p_end),
        "source_observation_ids": sorted(obs_ids),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
    cfg: PreviewConfig,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r_start, r_end, pct, abs_chg, p_start, p_end in pairs:
        if pct is None:
            continue
        if not _periods_pair_valid_for_change(str(p_start), str(p_end), stats, cfg):
            continue

        dims_s = r_start.get("dimensions_json") or {}
        dims_e = r_end.get("dimensions_json") or {}
        if not isinstance(dims_s, dict):
            dims_s = {}
        if not isinstance(dims_e, dict):
            dims_e = {}

        if not _slice_non_time_equal(dims_s, dims_e):
            stats.skipped_slice_mismatch += 1
            _strict_note(stats, cfg, "slice_mismatch")
            continue

        cc_s = str(r_start.get("contents_code") or "").strip()
        cc_e = str(r_end.get("contents_code") or "").strip()
        if cc_s != cc_e:
            stats.skipped_dimension_mismatch += 1
            _strict_note(stats, cfg, "contents_code_mismatch")
            continue

        u_s = str(r_start.get("unit") or "").strip()
        u_e = str(r_end.get("unit") or "").strip()
        if u_s != u_e:
            stats.skipped_unit_mismatch += 1
            _strict_note(stats, cfg, "unit_mismatch")
            continue

        f_start = float(r_start.get("value"))
        f_end = float(r_end.get("value"))
        labels_s = r_start.get("dimension_labels_json")
        labels_e = r_end.get("dimension_labels_json")

        lineage_obj = _build_lineage_object(r_start, r_end, cfg, extra=None)
        if not _lineage_is_nonempty(lineage_obj):
            stats.skipped_lineage_failures += 1
            _strict_note(stats, cfg, "lineage_empty")
            continue

        if cfg.exclude_total_categories and (
            _row_suggests_total_category(dims_s, labels_s) or _row_suggests_total_category(dims_e, labels_e)
        ):
            stats.skipped_total_categories += 1
            continue

        if cfg.exclude_small_slices and f_start < cfg.min_baseline and f_end < cfg.min_baseline:
            stats.skipped_both_periods_below_baseline += 1
            continue

        unspecified_hit = _has_unspecified_category(labels_s) or _has_unspecified_category(labels_e)
        if cfg.exclude_unspecified and unspecified_hit:
            stats.skipped_unspecified_category += 1
            continue

        if abs(abs_chg) < cfg.min_absolute_change:
            stats.skipped_low_absolute_change += 1
            continue

        if f_start < cfg.min_baseline:
            stats.skipped_low_baseline += 1
            continue

        dims = dims_e
        labels = labels_e if isinstance(labels_e, dict) else {}
        included_keys = sorted(_non_time_dimensions(dims).keys())
        labels_complete = all(
            isinstance(labels, dict) and labels.get(k) not in (None, "", [])
            for k in included_keys
        )

        direction = _direction_from_pct(pct)
        cc, cc_lab = _resolve_contents_code_fields(r_end)
        base_label = label_fn(r_start, r_end, p_start, p_end, direction)
        if cc_lab:
            signal_label = f"{base_label} — ContentsCode: {cc_lab}" + (f" ({cc})" if cc and cc != cc_lab else "")
        else:
            signal_label = base_label

        pt_s, pg_s = _classify_period(str(p_start))
        pt_e, pg_e = _classify_period(str(p_end))
        period_type = f"{pt_s}→{pt_e}"
        period_granularity = pg_s if pg_s == pg_e else f"{pg_s}|{pg_e}"

        dim_json_sorted = json.dumps(dims, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        note = (
            f"Direct two-period comparison (not aggregated). Periods {p_start}→{p_end} "
            f"({period_granularity}). Slice dimensions (non-time, excluding time keys from identity): "
            f"{', '.join(included_keys) or '(none)'}. Excluded from slice identity: {', '.join(sorted(TIME_KEYS | {'ContentsCode'}))}. "
            f"Values {f_start}→{f_end}, absolute change {abs_chg:.6g}, percent change {pct:.4f}% → `{direction}` "
            f"(growth if pct>={GROWTH_PCT_THRESHOLD}, decline if pct<={DECLINE_PCT_THRESHOLD}, else stable). "
            f"Filters: min_baseline>={cfg.min_baseline} on start value, min|Δ|>={cfg.min_absolute_change}, "
            f"exclude_unspecified={cfg.exclude_unspecified}, exclude_small_slices={cfg.exclude_small_slices}, "
            f"exclude_total_categories={cfg.exclude_total_categories}. Units `{u_s or 'n/a'}` (matched across periods)."
        )
        if cc_lab:
            note += f" ContentsCode label: {cc_lab}."
            if cc and cc != cc_lab:
                note += f" Code: {cc}."

        score, reasoning, q_flags = _compute_change_quality(
            f_start=f_start,
            f_end=f_end,
            pct=float(pct),
            abs_chg=abs_chg,
            cfg=cfg,
            unspecified_hit=unspecified_hit,
            total_hit=False,
            labels_complete=labels_complete,
            units_match=True,
        )
        qf_set: set[str] = {
            "preview_not_product_ready",
            "two_period_change",
            f"min_baseline_met(>={cfg.min_baseline})",
            *q_flags,
        }
        if cfg.exclude_unspecified:
            qf_set.add("no_blocked_unspecified_phrase_in_labels")
        qf = sorted(qf_set)

        expl_sum = _explainability_summary_change(
            signal_type=signal_type,
            dims=dims,
            labels=labels if isinstance(labels, dict) else {},
            p_start=str(p_start),
            p_end=str(p_end),
            period_type=period_type,
            period_granularity=str(period_granularity),
            direction=direction,
            pct=float(pct),
            cfg=cfg,
            comparison_mode="pairwise_two_period_same_slice",
        )
        oid_a = str(r_start.get("id") or "")
        oid_b = str(r_end.get("id") or "")
        det_hash = _deterministic_signal_hash(
            signal_type=signal_type,
            dims=dims,
            p_start=str(p_start),
            p_end=str(p_end),
            obs_ids=tuple(sorted({oid_a, oid_b})),
        )

        row_out: dict[str, Any] = {
            "signal_type": signal_type,
            "signal_label": signal_label,
            "table_id": r_end.get("table_id"),
            "periods_compared": f"{p_start}→{p_end}",
            "period_type": period_type,
            "period_granularity": period_granularity,
            "value_start": f_start,
            "value_end": f_end,
            "absolute_change": abs_chg,
            "percent_change": round(pct, 6),
            "direction_label": direction,
            "confidence_category": "verified_statistical",
            "confidence_score": 0.9,
            "source_observation_ids": f"{oid_a},{oid_b}",
            "source_table": r_end.get("table_id"),
            "dimensions_json": dim_json_sorted,
            "dimension_labels_json": json.dumps(labels, ensure_ascii=False, sort_keys=True),
            "explainability_note": note,
            "explainability_summary_json": json.dumps(expl_sum, ensure_ascii=False, sort_keys=True),
            "lineage_json": _lineage_json_from_obj(lineage_obj),
            "quality_flags": json.dumps(qf, ensure_ascii=False),
            "signal_quality_score": score,
            "quality_reasoning_json": json.dumps(reasoning, ensure_ascii=False, sort_keys=True),
            "signal_deterministic_hash": det_hash,
            "min_baseline": cfg.min_baseline,
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


def _snapshot_agg_bucket_valid(items: list[dict[str, Any]], stats: RunStats) -> bool:
    """MVP aggregation: refuse sums across mixed contents_code, unit, or period granularity."""
    if not items:
        return False
    c0 = str(items[0].get("contents_code") or "").strip()
    u0 = str(items[0].get("unit") or "").strip()
    g0 = _classify_period(str(items[0].get("period") or ""))[1]
    if g0 == "unknown":
        stats.skipped_invalid_aggregation += 1
        return False
    for it in items[1:]:
        if str(it.get("contents_code") or "").strip() != c0:
            stats.skipped_invalid_aggregation += 1
            return False
        if str(it.get("unit") or "").strip() != u0:
            stats.skipped_invalid_aggregation += 1
            return False
        if _classify_period(str(it.get("period") or ""))[1] != g0:
            stats.skipped_invalid_aggregation += 1
            return False
    return True


def _sort_signals_deterministic(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def k(r: dict[str, Any]) -> tuple[str, ...]:
        return (
            str(r.get("signal_type") or ""),
            str(r.get("table_id") or ""),
            str(r.get("periods_compared") or ""),
            str(r.get("dimensions_json") or ""),
            str(r.get("source_observation_ids") or ""),
            str(r.get("signal_deterministic_hash") or ""),
        )

    return sorted(rows, key=k)


def _collect_observation_uuids(signals: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for s in signals:
        raw = str(s.get("source_observation_ids") or "")
        for p in raw.split(","):
            t = p.strip()
            if t:
                seen.setdefault(t, None)
    return list(seen.keys())


def _verify_observations_exist(client: Client, ids: list[str], chunk: int = 120) -> tuple[set[str], int]:
    found: set[str] = set()
    for i in range(0, len(ids), chunk):
        batch = ids[i : i + chunk]
        try:
            res = client.table("statistical_observations").select("id").in_("id", batch).execute()
            for r in res.data or []:
                if r.get("id") is not None:
                    found.add(str(r["id"]))
        except Exception:
            continue
    return found, len(ids)


def _drop_signals_with_missing_observations(
    signals: list[dict[str, Any]],
    client: Client,
    stats: RunStats,
) -> list[dict[str, Any]]:
    ids = _collect_observation_uuids(signals)
    if not ids:
        return signals
    found, _n = _verify_observations_exist(client, ids)
    kept: list[dict[str, Any]] = []
    for s in signals:
        parts = [p.strip() for p in str(s.get("source_observation_ids") or "").split(",") if p.strip()]
        if parts and not all(p in found for p in parts):
            stats.skipped_source_observation_missing += 1
            continue
        kept.append(s)
    return kept


def _quality_score_histogram(scores: list[float]) -> dict[str, int]:
    bins = {
        "0.0-0.2": 0,
        "0.2-0.4": 0,
        "0.4-0.6": 0,
        "0.6-0.8": 0,
        "0.8-1.0": 0,
    }
    for x in scores:
        if x < 0.2:
            bins["0.0-0.2"] += 1
        elif x < 0.4:
            bins["0.2-0.4"] += 1
        elif x < 0.6:
            bins["0.4-0.6"] += 1
        elif x < 0.8:
            bins["0.6-0.8"] += 1
        else:
            bins["0.8-1.0"] += 1
    return bins


def _write_review_sample_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            row = {k: r.get(k, "") for k in CSV_COLUMNS}
            for k in (
                "value_start",
                "value_end",
                "absolute_change",
                "percent_change",
                "confidence_score",
                "min_baseline",
                "signal_quality_score",
            ):
                if row.get(k) == "" or row.get(k) is None:
                    continue
                v = row[k]
                if isinstance(v, float):
                    row[k] = repr(v) if v != int(v) else str(int(v))
                else:
                    row[k] = str(v)
            w.writerow(row)


def _write_review_samples(signals: list[dict[str, Any]]) -> list[str]:
    """Deterministic capped CSVs for manual inspection."""
    REVIEW_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    cap = 200
    change_only = [s for s in signals if s.get("percent_change") not in ("", None)]
    by_growth = sorted(
        change_only,
        key=lambda r: (-float(r.get("percent_change") or 0.0), str(r.get("source_observation_ids"))),
    )[:cap]
    by_decline = sorted(
        change_only,
        key=lambda r: (float(r.get("percent_change") or 0.0), str(r.get("source_observation_ids"))),
    )[:cap]
    unstable = sorted(
        change_only,
        key=lambda r: (
            -abs(float(r.get("percent_change") or 0.0)),
            float(r.get("value_start") or 0.0),
            str(r.get("source_observation_ids")),
        ),
    )[:cap]
    low_q = sorted(
        signals,
        key=lambda r: (float(r.get("signal_quality_score") or 1.0), str(r.get("source_observation_ids"))),
    )[:cap]
    paths = []
    for name, subset in (
        ("top_growth.csv", by_growth),
        ("top_decline.csv", by_decline),
        ("unstable_signals.csv", unstable),
        ("low_quality_signals.csv", low_q),
    ):
        p = REVIEW_SAMPLES_DIR / name
        _write_review_sample_csv(p, subset)
        paths.append(str(p.relative_to(ROOT)))
    return paths


def _table_ids_from_row_cache_keys(row_cache: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for k in row_cache:
        tid = k.split("\x1f", 1)[0]
        if tid not in out:
            out.append(tid)
    return out


def run_employment_count_change(
    get_rows: Callable[[str], list[dict[str, Any]]],
    table_filter: str | None,
    stats: RunStats,
    ctx: PreviewConfig,
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
    ctx: PreviewConfig,
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
    ctx: PreviewConfig,
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
    ctx: PreviewConfig,
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
        if not _snapshot_agg_bucket_valid(items, stats):
            continue
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
        if ctx.exclude_total_categories and _row_suggests_total_category(
            dims_ref if isinstance(dims_ref, dict) else {}, labels_ref
        ):
            stats.skipped_total_categories += 1
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
        pt, pg = _classify_period(str(latest))
        note = (
            f"MVP aggregation: summed {len(ids)} observations for the same Yrke slice in period {latest} ({pg}). "
            f"{direction_note}. Non-aggregated dimensions excluded from grouping key remain in dimensions_json. "
            f"Safeguards: single ContentsCode={cc or 'n/a'}, uniform unit across summed rows."
        )
        if cc_lab:
            note += f" ContentsCode (label): {cc_lab}."
            if cc and cc != cc_lab:
                note += f" Code: {cc}."
        qf = sorted(
            {
                "preview_not_product_ready",
                "structure_snapshot",
                "aggregated_sum_latest_period",
            }
        )
        if ctx.exclude_unspecified:
            qf.append("no_blocked_unspecified_phrase_in_labels_rows")
        expl = {
            "signal_type": "occupation_structure_signal",
            "comparison_mode": "aggregated_sum_same_yrke_latest_period",
            "period": latest,
            "period_type": pt,
            "period_granularity": pg,
            "observation_count": len(ids),
        }
        score = round(min(1.0, 0.45 + 0.12 * min(len(ids), 20) / 20 + (0.43 if len(ids) >= 3 else 0)), 4)
        reasoning = {"snapshot_components": {"observation_count": len(ids), "rule": "deterministic_preview_v1.3"}}
        lineage_obj = _build_lineage_object(
            r0,
            None,
            ctx,
            extra={"aggregation": "sum(values) for same Yrke slice latest period"},
        )
        oid_tuple = tuple(sorted(ids))
        det_hash = _deterministic_signal_hash(
            signal_type="occupation_structure_signal",
            dims=dims_ref if isinstance(dims_ref, dict) else {},
            p_start=str(latest),
            p_end=str(latest),
            obs_ids=oid_tuple,
        )
        out.append(
            {
                "signal_type": "occupation_structure_signal",
                "signal_label": sig_lab,
                "table_id": TABLE_OCCUPATION,
                "periods_compared": latest,
                "period_type": pt,
                "period_granularity": pg,
                "value_start": "",
                "value_end": total,
                "absolute_change": "",
                "percent_change": "",
                "direction_label": direction,
                "confidence_category": "verified_statistical",
                "confidence_score": 1.0,
                "source_observation_ids": ",".join(ids),
                "source_table": TABLE_OCCUPATION,
                "dimensions_json": json.dumps(dims_ref, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "dimension_labels_json": json.dumps(labels_ref, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "explainability_note": note,
                "explainability_summary_json": json.dumps(expl, ensure_ascii=False, sort_keys=True),
                "lineage_json": _lineage_json_from_obj(lineage_obj),
                "quality_flags": json.dumps(qf, ensure_ascii=False),
                "signal_quality_score": score,
                "quality_reasoning_json": json.dumps(reasoning, ensure_ascii=False, sort_keys=True),
                "signal_deterministic_hash": det_hash,
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
    ctx: PreviewConfig,
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
        if not _snapshot_agg_bucket_valid(items, stats):
            continue
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
        if ctx.exclude_total_categories and _row_suggests_total_category(
            dims_ref if isinstance(dims_ref, dict) else {}, labels_ref
        ):
            stats.skipped_total_categories += 1
            continue
        r0 = items[0]
        cc, cc_lab = _resolve_contents_code_fields(r0)
        sig_lab = f"Education/workforce snapshot ({latest})"
        if cc_lab:
            sig_lab = f"{sig_lab} — ContentsCode: {cc_lab}" + (f" ({cc})" if cc and cc != cc_lab else "")
        pt, pg = _classify_period(str(latest))
        note = (
            f"MVP aggregation: summed {len(ids)} observations for UtdNivaa×HeltidDeltid slice in period {latest} ({pg}). "
            f"Safeguards: single ContentsCode={cc or 'n/a'}, uniform unit across summed rows."
        )
        if cc_lab:
            note += f" ContentsCode (label): {cc_lab}."
            if cc and cc != cc_lab:
                note += f" Code: {cc}."
        qf = sorted({"preview_not_product_ready", "workforce_snapshot", "aggregated_sum_latest_period"})
        if ctx.exclude_unspecified:
            qf.append("no_blocked_unspecified_phrase_in_labels_rows")
        expl = {
            "signal_type": "education_level_workforce_signal",
            "comparison_mode": "aggregated_sum_utdnivaa_heltiddeltid_latest_period",
            "period": latest,
            "period_type": pt,
            "period_granularity": pg,
            "observation_count": len(ids),
        }
        score = round(min(1.0, 0.45 + 0.12 * min(len(ids), 20) / 20 + (0.43 if len(ids) >= 3 else 0)), 4)
        reasoning = {"snapshot_components": {"observation_count": len(ids), "rule": "deterministic_preview_v1.3"}}
        lineage_obj = _build_lineage_object(
            r0,
            None,
            ctx,
            extra={"aggregation": "sum(values) per UtdNivaa×HeltidDeltid latest period"},
        )
        oid_tuple = tuple(sorted(ids))
        det_hash = _deterministic_signal_hash(
            signal_type="education_level_workforce_signal",
            dims=sub_dims,
            p_start=str(latest),
            p_end=str(latest),
            obs_ids=oid_tuple,
        )
        out.append(
            {
                "signal_type": "education_level_workforce_signal",
                "signal_label": sig_lab,
                "table_id": TABLE_WORKFORCE,
                "periods_compared": latest,
                "period_type": pt,
                "period_granularity": pg,
                "value_start": "",
                "value_end": total,
                "absolute_change": "",
                "percent_change": "",
                "direction_label": "snapshot",
                "confidence_category": "verified_statistical",
                "confidence_score": 1.0,
                "source_observation_ids": ",".join(ids),
                "source_table": TABLE_WORKFORCE,
                "dimensions_json": json.dumps(sub_dims, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "dimension_labels_json": json.dumps(sub_labels, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "explainability_note": note,
                "explainability_summary_json": json.dumps(expl, ensure_ascii=False, sort_keys=True),
                "lineage_json": _lineage_json_from_obj(lineage_obj),
                "quality_flags": json.dumps(qf, ensure_ascii=False),
                "signal_quality_score": score,
                "quality_reasoning_json": json.dumps(reasoning, ensure_ascii=False, sort_keys=True),
                "signal_deterministic_hash": det_hash,
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
            for k in (
                "value_start",
                "value_end",
                "absolute_change",
                "percent_change",
                "confidence_score",
                "min_baseline",
                "signal_quality_score",
            ):
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
    p.add_argument(
        "--min-absolute-change",
        type=float,
        default=10.0,
        help="For two-period change signals: skip when abs(value_end-value_start) is below this (default 10).",
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
    mxs = p.add_mutually_exclusive_group()
    mxs.add_argument(
        "--exclude-small-slices",
        dest="exclude_small_slices",
        action="store_true",
        help="Skip change pairs where BOTH periods are below --min-baseline (default: on).",
    )
    mxs.add_argument(
        "--include-small-slices",
        dest="exclude_small_slices",
        action="store_false",
        help="Allow change pairs where both period values are below --min-baseline.",
    )
    mxt = p.add_mutually_exclusive_group()
    mxt.add_argument(
        "--exclude-total-categories",
        dest="exclude_total_categories",
        action="store_true",
        help="Skip rows/signals that look like SSB totals (labels/codes heuristics; default: on).",
    )
    mxt.add_argument(
        "--include-total-categories",
        dest="exclude_total_categories",
        action="store_false",
        help="Do not skip total-like categories.",
    )
    p.set_defaults(exclude_unspecified=True, exclude_small_slices=True, exclude_total_categories=True)
    p.add_argument(
        "--contents-code",
        type=str,
        default=None,
        metavar="CODE",
        help="Restrict observations to this contents_code (exact match); also applied in balanced-period discovery/fetch.",
    )
    p.add_argument(
        "--strict-validation",
        action="store_true",
        help="Abort with non-zero exit on lineage/period/unit/slice violations encountered during emission.",
    )
    p.add_argument(
        "--preview-report-only",
        action="store_true",
        help="Do not write signal CSV rows or review samples; still write summary JSON with counters.",
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
    t_run0 = time.perf_counter()
    args = _parse_args()
    if args.limit < 1:
        print("ERROR: --limit must be >= 1", file=sys.stderr)
        return 1
    if args.min_baseline < 0:
        print("ERROR: --min-baseline must be >= 0", file=sys.stderr)
        return 1
    if args.min_absolute_change < 0:
        print("ERROR: --min-absolute-change must be >= 0", file=sys.stderr)
        return 1

    cc_filter = (args.contents_code or "").strip() or None
    gen_ts = _now_utc()
    preview_cfg = PreviewConfig(
        min_baseline=float(args.min_baseline),
        min_absolute_change=float(args.min_absolute_change),
        exclude_unspecified=bool(args.exclude_unspecified),
        contents_code_filter=cc_filter,
        exclude_small_slices=bool(args.exclude_small_slices),
        exclude_total_categories=bool(args.exclude_total_categories),
        strict_validation=bool(args.strict_validation),
        preview_report_only=bool(args.preview_report_only),
        generation_timestamp_utc=gen_ts,
    )

    print("=" * 60)
    print("Preview verified statistical signals (Supabase read-only)")
    print(f"  script_version: {SCRIPT_VERSION}")
    print(f"  signal_logic_version: {SIGNAL_LOGIC_VERSION}")
    print(f"  limit per table: {args.limit}")
    if args.balanced_periods:
        print("  balanced_periods: ON (change-style: --limit per period; snapshots: unchanged)")
    else:
        print("  balanced_periods: OFF")
    print(f"  min_baseline: {preview_cfg.min_baseline}")
    print(f"  min_absolute_change: {preview_cfg.min_absolute_change}")
    print(f"  exclude_unspecified: {preview_cfg.exclude_unspecified}")
    print(f"  exclude_small_slices: {preview_cfg.exclude_small_slices}")
    print(f"  exclude_total_categories: {preview_cfg.exclude_total_categories}")
    print(f"  strict_validation: {preview_cfg.strict_validation}")
    print(f"  preview_report_only: {preview_cfg.preview_report_only}")
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
        rows = fn(getter, args.table, s, preview_cfg)
        if not preview_cfg.preview_report_only:
            all_signals.extend(rows)
        total_stats.merge(s)
        print(
            f"[{name}] preview rows: {len(rows)} | "
            f"rows_read={s.rows_read} candidate_pairs={s.candidate_pairs} "
            f"generated={s.preview_signals_generated} "
            f"skip_no_prior={s.skipped_missing_prior_period} skip_zero={s.skipped_zero_baseline} "
            f"skip_low_baseline={s.skipped_low_baseline} skip_unspecified={s.skipped_unspecified_category} "
            f"skip_low_abs={s.skipped_low_absolute_change} skip_both_below={s.skipped_both_periods_below_baseline} "
            f"skip_totals={s.skipped_total_categories} skip_bad_period={s.skipped_invalid_period_pairing} "
            f"skip_lineage={s.skipped_lineage_failures} skip_obs_missing={s.skipped_source_observation_missing} "
            f"skip_slice={s.skipped_slice_mismatch} skip_unit={s.skipped_unit_mismatch} "
            f"skip_dim={s.skipped_dimension_mismatch} skip_agg={s.skipped_invalid_aggregation}"
        )

    run_if("employment_count_change", run_employment_count_change, get_change_rows)
    run_if("regional_education_employment_signal", run_regional_education, get_change_rows)
    run_if("industry_education_employment_signal", run_industry_education, get_change_rows)
    run_if("occupation_structure_signal", run_occupation_structure, get_default_rows)
    run_if("education_level_workforce_signal", run_education_workforce, get_default_rows)

    if preview_cfg.strict_validation and total_stats.strict_validation_abort:
        print("\nSTRICT validation aborted:", file=sys.stderr)
        for m in total_stats.strict_validation_messages[:50]:
            print(f"  {m}", file=sys.stderr)
        return 1

    before_verify = len(all_signals)
    all_signals = _drop_signals_with_missing_observations(all_signals, client, total_stats)
    if before_verify != len(all_signals):
        print(f"[observation-ids] dropped {before_verify - len(all_signals)} rows with missing source observations")

    all_signals = _sort_signals_deterministic(all_signals)

    scores = [
        float(s["signal_quality_score"])
        for s in all_signals
        if s.get("signal_quality_score") is not None and str(s.get("signal_quality_score")) != ""
    ]
    hist = _quality_score_histogram(scores)

    by_type: dict[str, int] = defaultdict(int)
    by_dir: dict[str, int] = defaultdict(int)
    by_table: dict[str, int] = defaultdict(int)
    for s in all_signals:
        by_type[str(s.get("signal_type") or "")] += 1
        by_dir[str(s.get("direction_label") or "")] += 1
        by_table[str(s.get("table_id") or "")] += 1

    elapsed = round(time.perf_counter() - t_run0, 4)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "signal_preview_rows.csv"
    json_path = OUT_DIR / "signal_preview_summary.json"

    review_paths: list[str] = []
    if not preview_cfg.preview_report_only:
        _write_csv(csv_path, all_signals)
        if all_signals:
            review_paths = _write_review_samples(all_signals)

    summary = {
        "script_version": SCRIPT_VERSION,
        "signal_logic_version": SIGNAL_LOGIC_VERSION,
        "timestamp_utc": _now_utc(),
        "runtime_seconds": elapsed,
        "thresholds": {
            "growth_percent_min": GROWTH_PCT_THRESHOLD,
            "decline_percent_max": DECLINE_PCT_THRESHOLD,
            "min_baseline": preview_cfg.min_baseline,
            "min_absolute_change": preview_cfg.min_absolute_change,
        },
        "filters_enabled": {
            "exclude_unspecified": preview_cfg.exclude_unspecified,
            "exclude_small_slices": preview_cfg.exclude_small_slices,
            "exclude_total_categories": preview_cfg.exclude_total_categories,
            "strict_validation": preview_cfg.strict_validation,
            "preview_report_only": preview_cfg.preview_report_only,
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
            "min_baseline": preview_cfg.min_baseline,
            "min_absolute_change": preview_cfg.min_absolute_change,
            "exclude_unspecified": preview_cfg.exclude_unspecified,
            "exclude_small_slices": preview_cfg.exclude_small_slices,
            "exclude_total_categories": preview_cfg.exclude_total_categories,
            "strict_validation": preview_cfg.strict_validation,
            "preview_report_only": preview_cfg.preview_report_only,
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
            "skipped_low_absolute_change": total_stats.skipped_low_absolute_change,
            "skipped_both_periods_below_baseline": total_stats.skipped_both_periods_below_baseline,
            "skipped_total_categories": total_stats.skipped_total_categories,
            "skipped_invalid_period_pairing": total_stats.skipped_invalid_period_pairing,
            "skipped_lineage_failures": total_stats.skipped_lineage_failures,
            "skipped_source_observation_missing": total_stats.skipped_source_observation_missing,
            "skipped_slice_mismatch": total_stats.skipped_slice_mismatch,
            "skipped_unit_mismatch": total_stats.skipped_unit_mismatch,
            "skipped_dimension_mismatch": total_stats.skipped_dimension_mismatch,
            "skipped_invalid_aggregation": total_stats.skipped_invalid_aggregation,
        },
        "counts_by_signal_type": dict(sorted(by_type.items())),
        "counts_by_direction_label": dict(sorted(by_dir.items())),
        "counts_by_table_id": dict(sorted(by_table.items())),
        "quality_score_distribution": hist,
        "output_csv": str(csv_path.relative_to(ROOT)) if not preview_cfg.preview_report_only else None,
        "preview_row_count": len(all_signals),
        "observations_fetched_by_table": {tid: len(rows) for tid, rows in row_cache.items()},
        "review_sample_outputs": review_paths,
    }
    if args.balanced_periods and balanced_period_meta:
        summary["balanced_period_fetch"] = balanced_period_meta
    datasets_meta: list[dict[str, Any]] = []
    try:
        if row_cache:
            tids = _table_ids_from_row_cache_keys(row_cache)
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
    print(f"  runtime_seconds:                     {elapsed}")
    print(f"  rows read (sum over generators):     {total_stats.rows_read}")
    print(f"  candidate pairs (two-period groups): {total_stats.candidate_pairs}")
    print(f"  preview signals generated:           {total_stats.preview_signals_generated}")
    print(f"  skipped missing prior period:        {total_stats.skipped_missing_prior_period}")
    print(f"  skipped zero baseline:               {total_stats.skipped_zero_baseline}")
    print(f"  skipped low baseline:                {total_stats.skipped_low_baseline}")
    print(f"  skipped unspecified category:        {total_stats.skipped_unspecified_category}")
    print(f"  skipped low absolute change:         {total_stats.skipped_low_absolute_change}")
    print(f"  skipped both periods below baseline: {total_stats.skipped_both_periods_below_baseline}")
    print(f"  skipped total categories:            {total_stats.skipped_total_categories}")
    print(f"  skipped invalid period pairing:      {total_stats.skipped_invalid_period_pairing}")
    print(f"  skipped lineage failures:            {total_stats.skipped_lineage_failures}")
    print(f"  skipped source obs missing:          {total_stats.skipped_source_observation_missing}")
    print(f"  skipped slice mismatch:              {total_stats.skipped_slice_mismatch}")
    print(f"  skipped unit mismatch:               {total_stats.skipped_unit_mismatch}")
    print(f"  skipped dimension mismatch:          {total_stats.skipped_dimension_mismatch}")
    print(f"  skipped invalid aggregation:         {total_stats.skipped_invalid_aggregation}")
    print(f"  quality_score_distribution:          {hist}")
    if row_cache:
        print(f"  observations fetched (cache keys): { {k: len(v) for k, v in row_cache.items()} }")
    if not preview_cfg.preview_report_only:
        print(f"\nWrote: {csv_path}")
    else:
        print("\n(preview-report-only: skipped signal CSV and review samples)")
    print(f"Wrote: {json_path}")
    if review_paths:
        print("Review samples:")
        for rp in review_paths:
            print(f"  {rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

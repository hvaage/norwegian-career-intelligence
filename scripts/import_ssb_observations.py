#!/usr/bin/env python3
"""
First MVP SSB observation importer.

Imports normalized SSB JSON-stat2 observations into:
  - statistical_datasets
  - statistical_dimensions
  - statistical_dimension_values
  - statistical_observations

Scope tables:
  - 11615
  - 12850
  - 08417
  - 09793

No signals/gaps/overlaps/recommendations/RAG writes in this script.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "ssb"
REPORTS_DIR = ROOT / "data" / "processed" / "ssb_import_reports"

TABLE_IDS = ["11615", "12850", "08417", "09793"]
SOURCE_SYSTEM = "ssb_pxwebapi_v2"
PROVIDER = "SSB"
DEFAULT_BATCH_SIZE = 1000
DEFAULT_CONFIDENCE_CATEGORY = "verified_statistical"
DEFAULT_CONFIDENCE_SCORE = 1.0
TRANSFORMATION_VERSION = "ssb_jsonstat2_flatten_v1"
NORMALIZATION_VERSION = "ssb_norm_v1"
IMPORTER_VERSION = "1.2.0"

TOTAL_LABEL_HINTS = (
    "i alt",
    "alle",
    "begge kjønn",
    "begge kjonn",
    "alle yrker",
    "alle næringer",
    "alle naeringer",
)
TOTAL_CODE_HINTS = {"TOT", "0-9", "00-99"}


@dataclass
class Stats:
    datasets_upserted: int = 0
    dimensions_upserted: int = 0
    dimension_values_upserted: int = 0
    observations_inserted: int = 0
    skipped_null_values: int = 0
    warnings: int = 0
    tables_processed: int = 0
    tables_skipped: int = 0


@dataclass
class TableReport:
    """Per-table validation / import report (JSON-serializable via asdict)."""

    table_id: str
    source_file: str | None = None
    metadata_file: str | None = None
    hard_fails: int = 0
    warnings: int = 0
    infos: int = 0
    hard_fail_messages: list[str] = field(default_factory=list)
    warning_messages: list[str] = field(default_factory=list)
    info_messages: list[str] = field(default_factory=list)
    expected_cells: int = 0
    value_array_length: int = 0
    cartesian_match: bool = True
    flattened_row_count: int = 0
    skipped_null_count: int = 0
    inserted_row_count: int = 0
    duplicate_signature_warnings: int = 0
    missing_label_count: int = 0
    missing_dimension_value_id_count: int = 0
    lineage_complete_count: int = 0
    lineage_incomplete_count: int = 0
    existing_observations_count: int = 0
    status: str = "ok"  # ok | skipped | failed

    def add_hard_fail(self, msg: str) -> None:
        self.hard_fails += 1
        self.hard_fail_messages.append(msg)
        self.status = "failed"

    def add_warning(self, msg: str) -> None:
        self.warnings += 1
        self.warning_messages.append(msg)

    def add_info(self, msg: str) -> None:
        self.infos += 1
        self.info_messages.append(msg)


def _slugify(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t)
    t = re.sub(r"-{2,}", "-", t).strip("-")
    return t or "unknown"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_env_client() -> Client:
    load_dotenv(ROOT / ".env")
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise RuntimeError(
            "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment/.env"
        )
    return create_client(url, key)


def _safe_json_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Malformed JSON in file: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected JSON shape (not object): {path}")
    return data


def _main_raw_file_for_table(table_id: str) -> Path:
    candidates = sorted(RAW_DIR.glob(f"{table_id}_*.json"))
    main = [
        p
        for p in candidates
        if not p.name.endswith("_metadata.json")
        and not p.name.endswith("_basic_metadata.json")
        and not p.name.endswith("_sample_data.json")
    ]
    if not main:
        raise RuntimeError(
            f"No main raw JSON-stat2 file found for table {table_id} in {RAW_DIR}"
        )
    return main[-1]


def _metadata_file_for_table(table_id: str) -> Path | None:
    path = RAW_DIR / f"{table_id}_metadata.json"
    return path if path.exists() else None


def _unwrap_dataset_payload(raw: dict[str, Any], path: Path) -> dict[str, Any]:
    if raw.get("class") == "dataset":
        return raw
    data = raw.get("data")
    if isinstance(data, dict) and data.get("class") == "dataset":
        return data
    raise RuntimeError(f"Unexpected JSON-stat2 structure in {path}")


def _find_ssb_source_id(client: Client) -> str | None:
    for fld, value in (
        ("slug", "ssb"),
        ("name", "SSB"),
        ("source_system", SOURCE_SYSTEM),
    ):
        try:
            res = client.table("sources").select("id").ilike(fld, f"%{value}%").limit(1).execute()
            if res.data:
                return str(res.data[0]["id"])
        except Exception:
            continue
    return None


def _extract_output_format_hint(metadata_or_basic: dict[str, Any]) -> str | None:
    links = metadata_or_basic.get("links")
    if not isinstance(links, list):
        return None
    for link in links:
        href = link.get("href") if isinstance(link, dict) else None
        if not isinstance(href, str):
            continue
        m = re.search(r"outputFormat=([^&]+)", href, flags=re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _upsert_statistical_dataset(
    client: Client,
    table_id: str,
    source_id: str | None,
    metadata: dict[str, Any] | None,
    raw_file: Path,
    dry_run: bool,
) -> str:
    slug = f"ssb-{table_id}"
    title = None
    if metadata:
        title = metadata.get("label") or metadata.get("title")
    if not title:
        title = f"SSB table {table_id}"

    payload = {
        "source_id": source_id,
        "dataset_id": None,
        "slug": slug,
        "external_id": table_id,
        "title": str(title),
        "provider": PROVIDER,
        "dataset_type": "statistical_dataset",
        "source_system": SOURCE_SYSTEM,
        "table_id": table_id,
        "description": metadata.get("description") if isinstance(metadata, dict) else None,
        "language": "no",
        "license_note": None,
        "access_url": f"https://data.ssb.no/api/pxwebapi/v2/tables/{table_id}?lang=no",
        "metadata_json": {
            "raw_file": raw_file.name,
            "output_format_hint": _extract_output_format_hint(metadata or {}),
            "metadata_keys": sorted(list((metadata or {}).keys())),
        },
        "classification_json": {
            "dataset_type": "statistical_dataset",
            "provider": PROVIDER,
            "source_system": SOURCE_SYSTEM,
        },
        "confidence_category": DEFAULT_CONFIDENCE_CATEGORY,
        "confidence_score": DEFAULT_CONFIDENCE_SCORE,
        "status": "active",
    }

    if dry_run:
        return f"dryrun-{slug}"

    res = (
        client.table("statistical_datasets")
        .upsert(payload, on_conflict="slug")
        .select("id")
        .execute()
    )
    if not res.data:
        fetched = (
            client.table("statistical_datasets")
            .select("id")
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
        if not fetched.data:
            raise RuntimeError(f"Failed to upsert statistical_datasets for table {table_id}")
        return str(fetched.data[0]["id"])
    return str(res.data[0]["id"])


def _dimension_positions(dim_meta: dict[str, Any]) -> list[str]:
    idx = ((dim_meta.get("category") or {}).get("index") or {})
    if not isinstance(idx, dict):
        return []
    ordered = sorted(idx.items(), key=lambda kv: kv[1])
    return [str(code) for code, _ in ordered]


def _dimension_label(dim_meta: dict[str, Any]) -> str | None:
    lbl = dim_meta.get("label")
    return str(lbl) if lbl is not None else None


def _value_label(dim_meta: dict[str, Any], value_code: str) -> str | None:
    labels = ((dim_meta.get("category") or {}).get("label") or {})
    if isinstance(labels, dict):
        v = labels.get(value_code)
        if v is not None:
            return str(v)
    return None


def _is_total(value_code: str, label_no: str | None) -> bool:
    if value_code in TOTAL_CODE_HINTS:
        return True
    if label_no:
        text = label_no.strip().lower()
        return any(h in text for h in TOTAL_LABEL_HINTS)
    return False


def _upsert_dimension(
    client: Client,
    dim_code: str,
    dim_meta: dict[str, Any],
    dry_run: bool,
) -> str:
    slug = f"ssb-{_slugify(dim_code)}"
    payload = {
        "slug": slug,
        "dimension_code": dim_code,
        "canonical_name": dim_code,
        "label_no": _dimension_label(dim_meta),
        "label_en": None,
        "dimension_type": "statistical_dimension",
        "source_system": SOURCE_SYSTEM,
        "description": None,
        "hierarchy_supported": False,
        "metadata_json": {"source_dimension_code": dim_code},
        "aliases_json": [],
        "taxonomy_mapping_json": {},
        "status": "active",
    }
    if dry_run:
        return f"dryrun-{slug}"
    res = (
        client.table("statistical_dimensions")
        .upsert(payload, on_conflict="slug")
        .select("id")
        .execute()
    )
    if not res.data:
        fetched = (
            client.table("statistical_dimensions")
            .select("id")
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
        if not fetched.data:
            raise RuntimeError(f"Failed to upsert dimension {dim_code}")
        return str(fetched.data[0]["id"])
    return str(res.data[0]["id"])


def _upsert_dimension_value(
    client: Client,
    dimension_id: str,
    value_code: str,
    label_no: str | None,
    sort_order: int,
    dry_run: bool,
) -> str:
    payload = {
        "dimension_id": dimension_id,
        "value_code": value_code,
        "label_no": label_no,
        "label_en": None,
        "parent_value_id": None,
        "sort_order": sort_order,
        "is_total": _is_total(value_code, label_no),
        "is_deprecated": False,
        "metadata_json": {},
        "aliases_json": [],
        "taxonomy_mapping_json": {},
    }
    if dry_run:
        return f"dryrun-{dimension_id}-{value_code}"

    res = (
        client.table("statistical_dimension_values")
        .upsert(payload, on_conflict="dimension_id,value_code")
        .select("id")
        .execute()
    )
    if not res.data:
        fetched = (
            client.table("statistical_dimension_values")
            .select("id")
            .eq("dimension_id", dimension_id)
            .eq("value_code", value_code)
            .limit(1)
            .execute()
        )
        if not fetched.data:
            raise RuntimeError(f"Failed to upsert dimension value {dimension_id}:{value_code}")
        return str(fetched.data[0]["id"])
    return str(res.data[0]["id"])


def _time_dimension_id(dataset: dict[str, Any]) -> str | None:
    role = dataset.get("role")
    if isinstance(role, dict):
        t = role.get("time")
        if isinstance(t, list) and t:
            return str(t[0])
    ids = dataset.get("id")
    if isinstance(ids, list):
        for item in ids:
            s = str(item)
            if s.lower() in ("tid", "time", "år", "aar"):
                return s
    return None


def _metric_dimension_id(dataset: dict[str, Any]) -> str | None:
    role = dataset.get("role")
    if isinstance(role, dict):
        metric = role.get("metric")
        if isinstance(metric, list) and metric:
            return str(metric[0])
    ids = dataset.get("id")
    if isinstance(ids, list):
        for item in ids:
            s = str(item)
            if s.lower() in ("contentscode", "statistikkvariabel"):
                return s
    return None


def _unit_for_cell(dataset: dict[str, Any], code_map: dict[str, str], metric_dim: str | None) -> str | None:
    if not metric_dim:
        return None
    dims = dataset.get("dimension") or {}
    if not isinstance(dims, dict):
        return None
    dim_meta = dims.get(metric_dim)
    if not isinstance(dim_meta, dict):
        return None
    code = code_map.get(metric_dim)
    if not code:
        return None
    unit = (((dim_meta.get("category") or {}).get("unit") or {}).get(code) or {})
    if isinstance(unit, dict):
        if unit.get("label"):
            return str(unit["label"])
        if unit.get("base"):
            return str(unit["base"])
    return None


def _period_bounds(period: str | None) -> tuple[str | None, str | None]:
    if not period:
        return None, None
    if re.fullmatch(r"\d{4}", period):
        return f"{period}-01-01", f"{period}-12-31"
    return None, None


def _observation_signature(
    *,
    table_id: str,
    source_file: str,
    period: str | None,
    contents_code: str | None,
    code_map: dict[str, str],
    dim_order: list[str],
    normalization_version: str,
) -> str:
    ordered = [(d, code_map.get(d)) for d in dim_order]
    payload = {
        "table_id": table_id,
        "source_file": source_file,
        "period": period,
        "contents_code": contents_code,
        "dimensions": ordered,
        "normalization_version": normalization_version,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _expected_cartesian_cells(dim_ids: list[str], positions_per_dim: dict[str, list[str]]) -> int:
    return math.prod(len(positions_per_dim[d]) for d in dim_ids)


def _build_local_dimension_maps(
    dims: dict[str, Any],
) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """Synthetic dimension / value ids for validate-only and dry-run (no Supabase)."""
    dim_id_by_code: dict[str, str] = {}
    for dim_code, dim_meta in dims.items():
        if isinstance(dim_meta, dict):
            dim_id_by_code[str(dim_code)] = f"local-dim-{_slugify(str(dim_code))}"

    dim_value_map: dict[tuple[str, str], str] = {}
    for dim_code, dim_meta in dims.items():
        if not isinstance(dim_meta, dict):
            continue
        idx_map = ((dim_meta.get("category") or {}).get("index") or {})
        if not isinstance(idx_map, dict):
            continue
        ordered = sorted(idx_map.items(), key=lambda kv: kv[1])
        for value_code, _so in ordered:
            value_code = str(value_code)
            dslug = _slugify(str(dim_code))
            dim_value_map[(str(dim_code), value_code)] = f"local-dv-{dslug}-{_slugify(value_code)}"
    return dim_id_by_code, dim_value_map


def _flatten_observations(
    table_id: str,
    source_file: Path,
    dataset: dict[str, Any],
    statistical_dataset_id: str,
    source_id: str | None,
    dimension_value_map: dict[tuple[str, str], str],
    *,
    ingestion_batch_id: str,
    limit: int | None,
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    ids = dataset.get("id")
    values = dataset.get("value")
    dims = dataset.get("dimension")
    if not isinstance(ids, list) or not isinstance(values, list) or not isinstance(dims, dict):
        raise RuntimeError(f"[{table_id}] Unexpected JSON-stat2 structure (id/value/dimension)")

    dim_ids = [str(x) for x in ids]
    positions_per_dim: dict[str, list[str]] = {}
    for d in dim_ids:
        meta = dims.get(d)
        if not isinstance(meta, dict):
            raise RuntimeError(f"[{table_id}] Missing dimension metadata for {d}")
        pos = _dimension_positions(meta)
        if not pos:
            raise RuntimeError(f"[{table_id}] Empty category index for {d}")
        positions_per_dim[d] = pos

    expected_cells = _expected_cartesian_cells(dim_ids, positions_per_dim)
    value_len = len(values)
    extras: dict[str, Any] = {
        "expected_cells": expected_cells,
        "value_array_length": value_len,
        "cartesian_match": expected_cells == value_len,
    }
    if expected_cells != value_len:
        raise RuntimeError(
            f"[{table_id}] Cartesian size mismatch: product(dimensions)={expected_cells} "
            f"but len(value)={value_len}"
        )

    time_dim = _time_dimension_id(dataset)
    metric_dim = _metric_dimension_id(dataset)
    rows: list[dict[str, Any]] = []
    skipped_null = 0

    combos = itertools.product(*(positions_per_dim[d] for d in dim_ids))
    for idx, combo in enumerate(combos):
        if idx >= len(values):
            break
        if limit is not None and len(rows) >= limit:
            break
        val = values[idx]
        if val is None:
            skipped_null += 1
            continue

        code_map = {dim_ids[i]: str(combo[i]) for i in range(len(dim_ids))}
        label_map: dict[str, str | None] = {}
        dim_value_ids: list[str] = []
        for d_id, code in code_map.items():
            d_meta = dims.get(d_id, {})
            label_map[d_id] = _value_label(d_meta if isinstance(d_meta, dict) else {}, code)
            dv_id = dimension_value_map.get((d_id, code))
            if dv_id:
                dim_value_ids.append(dv_id)

        period = code_map.get(time_dim) if time_dim else None
        period_start, period_end = _period_bounds(period)
        unit = _unit_for_cell(dataset, code_map, metric_dim)
        contents_code = code_map.get(metric_dim) if metric_dim else None

        signature = _observation_signature(
            table_id=table_id,
            source_file=source_file.name,
            period=period,
            contents_code=contents_code,
            code_map=code_map,
            dim_order=dim_ids,
            normalization_version=NORMALIZATION_VERSION,
        )

        raw_obs: dict[str, Any] = {
            "value": val,
            "dimension_codes": code_map,
            "dimension_labels": label_map,
            "observation_signature": signature,
        }
        row = {
            "statistical_dataset_id": statistical_dataset_id,
            "dataset_version_id": None,
            "source_id": source_id,
            "table_id": table_id,
            "source_file": source_file.name,
            "period": period,
            "period_start": period_start,
            "period_end": period_end,
            "value": val,
            "unit": unit,
            "contents_code": contents_code,
            "dimensions_json": code_map,
            "dimension_labels_json": label_map,
            "dimension_value_ids": dim_value_ids,
            "metadata_json": {
                "dimension_ids": dim_ids,
                "observation_signature": signature,
            },
            "raw_observation_json": raw_obs,
            "confidence_category": DEFAULT_CONFIDENCE_CATEGORY,
            "confidence_score": DEFAULT_CONFIDENCE_SCORE,
            "observed_at": None,
            "valid_from": None,
            "valid_to": None,
            "stale_after": None,
            "ingestion_batch_id": ingestion_batch_id,
            "transformation_version": TRANSFORMATION_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
        }
        rows.append(row)
    return rows, skipped_null, extras


def _row_lineage_complete(row: dict[str, Any], dim_ids: list[str]) -> bool:
    if not row.get("table_id") or not row.get("source_file"):
        return False
    if not row.get("ingestion_batch_id"):
        return False
    if not row.get("normalization_version") or not row.get("transformation_version"):
        return False
    dj = row.get("dimensions_json")
    if not isinstance(dj, dict) or not dj:
        return False
    raw = row.get("raw_observation_json")
    if not isinstance(raw, dict) or not raw.get("observation_signature"):
        return False
    dvi = row.get("dimension_value_ids")
    if not isinstance(dvi, list) or len(dvi) < len(dim_ids):
        return False
    return True


def _analyze_observation_rows(rows: list[dict[str, Any]], dim_ids: list[str]) -> dict[str, Any]:
    missing_labels = 0
    missing_dv_ids = 0
    lineage_ok = 0
    lineage_bad = 0
    for row in rows:
        labels = row.get("dimension_labels_json") or {}
        codes = row.get("dimensions_json") or {}
        for d in dim_ids:
            if d not in codes:
                continue
            lab = labels.get(d) if isinstance(labels, dict) else None
            if lab is None or (isinstance(lab, str) and not lab.strip()):
                missing_labels += 1
        dvi = row.get("dimension_value_ids")
        if not isinstance(dvi, list) or len(dvi) < len(dim_ids):
            missing_dv_ids += 1
        if _row_lineage_complete(row, dim_ids):
            lineage_ok += 1
        else:
            lineage_bad += 1

    sigs = []
    for row in rows:
        raw = row.get("raw_observation_json") or {}
        s = raw.get("observation_signature")
        if isinstance(s, str):
            sigs.append(s)
    dup_surplus = 0
    if sigs:
        cnt = Counter(sigs)
        dup_surplus = sum(c - 1 for c in cnt.values() if c > 1)

    return {
        "missing_label_cells": missing_labels,
        "missing_dimension_value_id_rows": missing_dv_ids,
        "lineage_complete_count": lineage_ok,
        "lineage_incomplete_count": lineage_bad,
        "duplicate_signature_warnings": dup_surplus,
    }


def _apply_analysis_to_report(rep: TableReport, analysis: dict[str, Any], dim_ids: list[str]) -> None:
    rep.missing_label_count = int(analysis["missing_label_cells"])
    rep.missing_dimension_value_id_count = int(analysis["missing_dimension_value_id_rows"])
    rep.lineage_complete_count = int(analysis["lineage_complete_count"])
    rep.lineage_incomplete_count = int(analysis["lineage_incomplete_count"])
    rep.duplicate_signature_warnings = int(analysis["duplicate_signature_warnings"])

    if rep.missing_label_count > 0:
        rep.add_warning(
            f"missing label cells: {rep.missing_label_count} (dimension × row cells with empty label)"
        )
    if rep.missing_dimension_value_id_count > 0:
        rep.add_warning(
            f"rows with incomplete dimension_value_ids: {rep.missing_dimension_value_id_count} "
            f"(expected {len(dim_ids)} ids per row)"
        )
    if rep.duplicate_signature_warnings > 0:
        rep.add_warning(
            f"duplicate observation_signature surplus rows: {rep.duplicate_signature_warnings} "
            "(within this batch; possible duplicate facts or flatten bug)"
        )
    if rep.lineage_incomplete_count > 0:
        rep.add_warning(f"rows failing lineage completeness check: {rep.lineage_incomplete_count}")


def _existing_observations_count(client: Client, table_id: str, source_file: str) -> int:
    try:
        res = (
            client.table("statistical_observations")
            .select("id", count="exact")
            .eq("table_id", table_id)
            .eq("source_file", source_file)
            .limit(1)
            .execute()
        )
        if hasattr(res, "count") and res.count is not None:
            return int(res.count)
        return len(res.data or [])
    except Exception as exc:
        raise RuntimeError(
            f"Supabase query failed while counting existing observations for "
            f"table_id={table_id} source_file={source_file}: {exc}"
        ) from exc


def _insert_observations_batched(
    client: Client,
    table_id: str,
    rows: list[dict[str, Any]],
    batch_size: int,
) -> int:
    inserted = 0
    total = len(rows)
    if total == 0:
        return 0
    n_batches = (total + batch_size - 1) // batch_size
    for start in range(0, total, batch_size):
        batch = rows[start : start + batch_size]
        end = min(start + batch_size, total)
        bi = start // batch_size + 1
        print(f"  [{table_id}] insert batch {bi}/{n_batches}: rows {start + 1}-{end}/{total}")
        try:
            client.table("statistical_observations").insert(batch).execute()
        except Exception as exc:
            raise RuntimeError(
                f"[{table_id}] Supabase insert error in batch {bi}/{n_batches}: {exc}"
            ) from exc
        inserted += len(batch)
    return inserted


def _print_table_validation_block(table_id: str, rep: TableReport) -> None:
    print(f"\n  --- Validation summary: {table_id} ---")
    print(f"  source_file: {rep.source_file}")
    print(f"  expected_cells (cartesian): {rep.expected_cells}")
    print(f"  value_array_length:         {rep.value_array_length}")
    print(f"  cartesian_match:            {rep.cartesian_match}")
    print(f"  flattened_row_count:        {rep.flattened_row_count}")
    print(f"  skipped_null_count:         {rep.skipped_null_count}")
    print(f"  inserted_row_count:         {rep.inserted_row_count}")
    print(f"  duplicate_signature_warn:   {rep.duplicate_signature_warnings}")
    print(f"  missing_label_cells:        {rep.missing_label_count}")
    print(f"  missing_dim_value_id_rows:  {rep.missing_dimension_value_id_count}")
    print(f"  lineage_complete_rows:      {rep.lineage_complete_count}")
    print(f"  lineage_incomplete_rows:    {rep.lineage_incomplete_count}")
    print(f"  existing_observations (DB): {rep.existing_observations_count}")
    print(f"  hard_fails / warnings / infos: {rep.hard_fails} / {rep.warnings} / {rep.infos}")
    if rep.hard_fail_messages:
        print("  HARD FAIL messages:")
        for m in rep.hard_fail_messages:
            print(f"    - {m}")
    if rep.warning_messages:
        print("  WARNING messages:")
        for m in rep.warning_messages:
            print(f"    - {m}")
    if rep.info_messages:
        print("  INFO messages:")
        for m in rep.info_messages:
            print(f"    - {m}")


def _write_import_report(
    *,
    mode: str,
    ingestion_batch_id: str,
    table_reports: list[TableReport],
    totals: dict[str, Any],
) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_batch = re.sub(r"[^a-zA-Z0-9._-]+", "_", ingestion_batch_id)[:40]
    path = REPORTS_DIR / f"ssb_import_{safe_batch}_{ts}.json"
    payload = {
        "importer_version": IMPORTER_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "transformation_version": TRANSFORMATION_VERSION,
        "ingestion_batch_id": ingestion_batch_id,
        "timestamp_utc": _now_utc(),
        "mode": mode,
        "tables": [asdict(tr) for tr in table_reports],
        "totals": totals,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _load_and_flatten_table(
    table_id: str,
    args: argparse.Namespace,
    *,
    statistical_dataset_id: str,
    source_id: str | None,
    dimension_value_map: dict[tuple[str, str], str],
    ingestion_batch_id: str,
) -> tuple[list[dict[str, Any]], int, dict[str, Any], list[str], TableReport]:
    rep = TableReport(table_id=table_id)
    raw_file = _main_raw_file_for_table(table_id)
    rep.source_file = raw_file.name
    raw = _safe_json_file(raw_file)
    dataset = _unwrap_dataset_payload(raw, raw_file)

    metadata_path = _metadata_file_for_table(table_id)
    if metadata_path is None:
        rep.add_warning("no sidecar metadata file (*_metadata.json); continuing with dataset metadata only")
    else:
        rep.metadata_file = metadata_path.name
        rep.add_info(f"metadata file present: {metadata_path.name}")

    dims = dataset.get("dimension")
    if not isinstance(dims, dict):
        rep.add_hard_fail("missing or invalid 'dimension' object on dataset")
        return [], 0, {}, [], rep

    dim_ids = [str(x) for x in (dataset.get("id") or [])]
    if not dim_ids:
        rep.add_hard_fail("dataset 'id' dimension order list is empty")
        return [], 0, {}, [], rep

    try:
        rows, skipped_null, extras = _flatten_observations(
            table_id=table_id,
            source_file=raw_file,
            dataset=dataset,
            statistical_dataset_id=statistical_dataset_id,
            source_id=source_id,
            dimension_value_map=dimension_value_map,
            ingestion_batch_id=ingestion_batch_id,
            limit=args.limit,
        )
    except RuntimeError as exc:
        rep.add_hard_fail(str(exc))
        return [], 0, {}, dim_ids, rep

    rep.expected_cells = int(extras["expected_cells"])
    rep.value_array_length = int(extras["value_array_length"])
    rep.cartesian_match = bool(extras["cartesian_match"])
    rep.flattened_row_count = len(rows)
    rep.skipped_null_count = skipped_null

    analysis = _analyze_observation_rows(rows, dim_ids)
    _apply_analysis_to_report(rep, analysis, dim_ids)
    return rows, skipped_null, extras, dim_ids, rep


def _process_table_local(
    table_id: str,
    args: argparse.Namespace,
    ingestion_batch_id: str,
    *,
    label: str,
) -> TableReport:
    print(f"\n[{label}] table={table_id}")
    raw_path = _main_raw_file_for_table(table_id)
    raw = _safe_json_file(raw_path)
    dataset = _unwrap_dataset_payload(raw, raw_path)
    d = dataset.get("dimension")
    if not isinstance(d, dict):
        rep = TableReport(table_id=table_id, source_file=raw_path.name)
        rep.add_hard_fail("missing dimension object")
        _print_table_validation_block(table_id, rep)
        return rep
    _, dim_value_map = _build_local_dimension_maps(d)
    rows, _sk, _ex, dim_ids, rep = _load_and_flatten_table(
        table_id,
        args,
        statistical_dataset_id=f"local-{table_id}",
        source_id=None,
        dimension_value_map=dim_value_map,
        ingestion_batch_id=ingestion_batch_id,
    )
    rep.inserted_row_count = 0
    rep.existing_observations_count = 0
    if rep.hard_fails == 0:
        rep.add_info(f"{label}: no database writes; {len(rows)} rows validated in memory")
    _print_table_validation_block(table_id, rep)
    return rep


def _process_table_import(
    client: Client,
    table_id: str,
    args: argparse.Namespace,
    source_id: str | None,
    stats: Stats,
    ingestion_batch_id: str,
) -> TableReport:
    print(f"\n[import] table={table_id}")
    raw_file = _main_raw_file_for_table(table_id)
    raw = _safe_json_file(raw_file)
    dataset = _unwrap_dataset_payload(raw, raw_file)

    metadata_path = _metadata_file_for_table(table_id)
    metadata = _safe_json_file(metadata_path) if metadata_path else None
    if metadata_path is None:
        print(f"  [{table_id}] WARNING: no metadata file found (continuing)")
        stats.warnings += 1

    print(f"  [{table_id}] source file: {raw_file.name}")
    if metadata_path:
        print(f"  [{table_id}] metadata file: {metadata_path.name}")

    statistical_dataset_id = _upsert_statistical_dataset(
        client, table_id, source_id, metadata or dataset, raw_file, dry_run=False
    )
    stats.datasets_upserted += 1

    dims = dataset.get("dimension")
    if not isinstance(dims, dict):
        rep = TableReport(table_id=table_id, source_file=raw_file.name)
        rep.add_hard_fail("missing or invalid 'dimension' object on dataset")
        _print_table_validation_block(table_id, rep)
        return rep

    dim_id_by_code: dict[str, str] = {}
    for dim_code, dim_meta in dims.items():
        if not isinstance(dim_meta, dict):
            rep = TableReport(table_id=table_id, source_file=raw_file.name)
            rep.add_hard_fail(f"unexpected dimension shape for '{dim_code}'")
            _print_table_validation_block(table_id, rep)
            return rep
        dim_id = _upsert_dimension(client, str(dim_code), dim_meta, dry_run=False)
        dim_id_by_code[str(dim_code)] = dim_id
        stats.dimensions_upserted += 1

    dim_value_map: dict[tuple[str, str], str] = {}
    for dim_code, dim_meta in dims.items():
        if not isinstance(dim_meta, dict):
            continue
        idx_map = ((dim_meta.get("category") or {}).get("index") or {})
        if not isinstance(idx_map, dict):
            continue
        ordered = sorted(idx_map.items(), key=lambda kv: kv[1])
        for value_code, sort_order in ordered:
            value_code = str(value_code)
            label_no = _value_label(dim_meta, value_code)
            so = int(sort_order) if sort_order is not None else 0
            dv_id = _upsert_dimension_value(
                client=client,
                dimension_id=dim_id_by_code[str(dim_code)],
                value_code=value_code,
                label_no=label_no,
                sort_order=so,
                dry_run=False,
            )
            dim_value_map[(str(dim_code), value_code)] = dv_id
            stats.dimension_values_upserted += 1

    rows, skipped_null, _extras, dim_ids, rep = _load_and_flatten_table(
        table_id,
        args,
        statistical_dataset_id=statistical_dataset_id,
        source_id=source_id,
        dimension_value_map=dim_value_map,
        ingestion_batch_id=ingestion_batch_id,
    )
    stats.skipped_null_values += skipped_null

    if rep.hard_fails:
        stats.tables_skipped += 1
        _print_table_validation_block(table_id, rep)
        return rep

    try:
        existing = _existing_observations_count(client, table_id, raw_file.name)
    except RuntimeError as exc:
        rep.add_hard_fail(str(exc))
        stats.tables_skipped += 1
        _print_table_validation_block(table_id, rep)
        return rep

    rep.existing_observations_count = existing
    print(f"  [{table_id}] existing observations in DB (table_id + source_file): {existing}")

    if existing > 0 and not args.allow_existing:
        msg = (
            f"refusing import: {existing} existing observation row(s) for table_id={table_id} "
            f"and source_file={raw_file.name}. Pass --allow-existing to insert anyway, "
            "or remove/rename conflicting data."
        )
        rep.add_hard_fail(msg)
        stats.tables_skipped += 1
        print(f"  [{table_id}] HARD STOP: {msg}", file=sys.stderr)
        _print_table_validation_block(table_id, rep)
        return rep

    if existing > 0 and args.allow_existing:
        rep.add_warning(
            f"--allow-existing: {existing} row(s) already present; inserting {len(rows)} additional row(s) (possible duplicates)"
        )
        stats.warnings += 1

    inserted = _insert_observations_batched(client, table_id, rows, args.batch_size)
    rep.inserted_row_count = inserted
    stats.observations_inserted += inserted
    stats.tables_processed += 1
    print(f"  [{table_id}] inserted observations: {inserted}")
    _print_table_validation_block(table_id, rep)
    return rep


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import SSB observations into statistical observation tables.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Local parse + flatten + validation only; no Supabase (same workload as --validate-only; mode tag differs).",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Parse local JSON only; validate structure and rows; no Supabase.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit imported observations per table (for testing).",
    )
    parser.add_argument(
        "--table",
        type=str,
        default=None,
        help="Import only one table id (e.g. 11615).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Observation insert batch size (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--allow-existing",
        action="store_true",
        help="Allow import even if observations already exist for same table_id and source_file.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.batch_size <= 0:
        print("ERROR: --batch-size must be > 0", file=sys.stderr)
        return 1
    if args.limit is not None and args.limit <= 0:
        print("ERROR: --limit must be > 0", file=sys.stderr)
        return 1
    if args.validate_only and args.dry_run:
        print("ERROR: use only one of --validate-only or --dry-run", file=sys.stderr)
        return 1

    selected_tables = [args.table] if args.table else TABLE_IDS
    for t in selected_tables:
        if t not in TABLE_IDS:
            print(f"ERROR: unsupported table '{t}'. Allowed: {TABLE_IDS}", file=sys.stderr)
            return 1

    validate_only = args.validate_only
    importer_dry_run = args.dry_run
    mode = "validate_only" if validate_only else ("dry_run" if importer_dry_run else "import")

    print("=" * 60)
    print("SSB observation import MVP")
    print(f"  importer_version:       {IMPORTER_VERSION}")
    print(f"  normalization_version:  {NORMALIZATION_VERSION}")
    print(f"  transformation_version:{TRANSFORMATION_VERSION}")
    print(f"  mode:                   {mode}")
    print(f"  tables:                 {selected_tables}")
    print(f"  limit:                  {args.limit}")
    print(f"  batch_size:             {args.batch_size}")
    print(f"  allow_existing:         {args.allow_existing}")
    print("=" * 60)

    ingest_seed = f"{_now_utc()}|{selected_tables}|{args.limit}|{mode}|{IMPORTER_VERSION}"
    ingestion_batch_id = f"ssb-import-{hashlib.sha1(ingest_seed.encode('utf-8')).hexdigest()[:12]}"
    print(f"ingestion_batch_id: {ingestion_batch_id}")

    table_reports: list[TableReport] = []
    stats = Stats()
    exit_code = 0

    try:
        if validate_only or importer_dry_run:
            tag = "validate-only" if validate_only else "dry-run"
            print(f"\n--{tag}: no Supabase (local parse + flatten + validation; no writes)")
            for table_id in selected_tables:
                tr = _process_table_local(table_id, args, ingestion_batch_id, label=tag)
                table_reports.append(tr)
                if tr.hard_fails:
                    exit_code = 1
        else:
            client = _load_env_client()
            source_id = _find_ssb_source_id(client)
            if source_id:
                print(f"resolved SSB source_id: {source_id}")
            else:
                print("warning: could not resolve SSB source_id from sources; using NULL source_id")
                stats.warnings += 1
            for table_id in selected_tables:
                tr = _process_table_import(
                    client,
                    table_id,
                    args,
                    source_id,
                    stats,
                    ingestion_batch_id,
                )
                table_reports.append(tr)
                if tr.hard_fails:
                    exit_code = 1

    except RuntimeError as exc:
        print(f"\nFATAL: {exc}", file=sys.stderr)
        exit_code = 1
    except Exception as exc:  # noqa: BLE001
        print(f"\nFATAL (unexpected): {exc}", file=sys.stderr)
        exit_code = 1

    total_hf = sum(tr.hard_fails for tr in table_reports)
    total_wn = sum(tr.warnings for tr in table_reports)
    total_inf = sum(tr.infos for tr in table_reports)
    totals = {
        "datasets_upserted": stats.datasets_upserted,
        "dimensions_upserted": stats.dimensions_upserted,
        "dimension_values_upserted": stats.dimension_values_upserted,
        "observations_inserted": stats.observations_inserted,
        "skipped_null_values": stats.skipped_null_values,
        "tables_processed": stats.tables_processed,
        "tables_skipped": stats.tables_skipped,
        "total_hard_fails": total_hf,
        "total_warnings": total_wn,
        "total_infos": total_inf,
        "flattened_rows_sum": sum(tr.flattened_row_count for tr in table_reports),
        "exit_code": exit_code,
    }

    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print(f"  total hard_fail (messages): {total_hf}")
    print(f"  total warnings:             {total_wn}")
    print(f"  total infos:                {total_inf}")
    print(f"  flattened rows (sum tables):{totals['flattened_rows_sum']}")
    if not validate_only and not importer_dry_run:
        print(f"  datasets upserted:          {stats.datasets_upserted}")
        print(f"  dimensions upserted:        {stats.dimensions_upserted}")
        print(f"  dimension values upserted:  {stats.dimension_values_upserted}")
        print(f"  observations inserted:      {stats.observations_inserted}")
        print(f"  skipped null values:        {stats.skipped_null_values}")
        print(f"  tables processed:           {stats.tables_processed}")
        print(f"  tables skipped:             {stats.tables_skipped}")
    print("=" * 60)

    try:
        report_path = _write_import_report(
            mode=mode,
            ingestion_batch_id=ingestion_batch_id,
            table_reports=table_reports,
            totals=totals,
        )
        print(f"\nWrote import report: {report_path}")
    except OSError as exc:
        print(f"\nWARNING: could not write import report: {exc}", file=sys.stderr)
        stats.warnings += 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

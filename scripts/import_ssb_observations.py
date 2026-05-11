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
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "ssb"

TABLE_IDS = ["11615", "12850", "08417", "09793"]
SOURCE_SYSTEM = "ssb_pxwebapi_v2"
PROVIDER = "SSB"
DEFAULT_BATCH_SIZE = 1000
DEFAULT_CONFIDENCE_CATEGORY = "verified_statistical"
DEFAULT_CONFIDENCE_SCORE = 1.0
TRANSFORMATION_VERSION = "ssb_jsonstat2_flatten_v1"
NORMALIZATION_VERSION = "ssb_norm_v1"

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
    # Best effort: source may not exist yet in some environments.
    for field, value in (
        ("slug", "ssb"),
        ("name", "SSB"),
        ("source_system", SOURCE_SYSTEM),  # field may not exist; ignore errors below
    ):
        try:
            res = client.table("sources").select("id").ilike(field, f"%{value}%").limit(1).execute()
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
        # fallback fetch if upsert returns empty
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
    # Keep lightweight MVP behavior: if YYYY or YYYYKx we preserve period text only.
    # period_start/end remain null for now unless strict date can be inferred.
    if not period:
        return None, None
    if re.fullmatch(r"\d{4}", period):
        return f"{period}-01-01", f"{period}-12-31"
    return None, None


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
) -> tuple[list[dict[str, Any]], int]:
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

        raw_obs = {
            "value": val,
            "dimension_codes": code_map,
            "dimension_labels": label_map,
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
    return rows, skipped_null


def _existing_observations_count(
    client: Client,
    table_id: str,
    source_file: str,
) -> int:
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
    for start in range(0, total, batch_size):
        batch = rows[start : start + batch_size]
        end = min(start + batch_size, total)
        print(f"[{table_id}] inserting batch {start // batch_size + 1}: rows {start + 1}-{end}/{total}")
        try:
            client.table("statistical_observations").insert(batch).execute()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"[{table_id}] Supabase insert error in batch {start // batch_size + 1}: {exc}") from exc
        inserted += len(batch)
    return inserted


def _process_table(
    client: Client,
    table_id: str,
    args: argparse.Namespace,
    source_id: str | None,
    stats: Stats,
    ingestion_batch_id: str,
) -> None:
    raw_file = _main_raw_file_for_table(table_id)
    raw = _safe_json_file(raw_file)
    dataset = _unwrap_dataset_payload(raw, raw_file)

    metadata_path = _metadata_file_for_table(table_id)
    metadata = _safe_json_file(metadata_path) if metadata_path else None
    if metadata_path is None:
        print(f"[{table_id}] warning: no metadata file found (continuing)")
        stats.warnings += 1

    print(f"\n[{table_id}] source file: {raw_file.name}")
    if metadata_path:
        print(f"[{table_id}] metadata file: {metadata_path.name}")

    statistical_dataset_id = _upsert_statistical_dataset(
        client, table_id, source_id, metadata or dataset, raw_file, args.dry_run
    )
    stats.datasets_upserted += 1

    dims = dataset.get("dimension")
    if not isinstance(dims, dict):
        raise RuntimeError(f"[{table_id}] Unexpected metadata shape: missing 'dimension' object")

    # Upsert dimensions and values, build lookup map.
    dim_id_by_code: dict[str, str] = {}
    for dim_code, dim_meta in dims.items():
        if not isinstance(dim_meta, dict):
            raise RuntimeError(f"[{table_id}] Unexpected dimension shape for '{dim_code}'")
        dim_id = _upsert_dimension(client, str(dim_code), dim_meta, args.dry_run)
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
            dv_id = _upsert_dimension_value(
                client=client,
                dimension_id=dim_id_by_code[str(dim_code)],
                value_code=value_code,
                label_no=label_no,
                sort_order=int(sort_order) if sort_order is not None else None,
                dry_run=args.dry_run,
            )
            dim_value_map[(str(dim_code), value_code)] = dv_id
            stats.dimension_values_upserted += 1

    rows, skipped_null = _flatten_observations(
        table_id=table_id,
        source_file=raw_file,
        dataset=dataset,
        statistical_dataset_id=statistical_dataset_id,
        source_id=source_id,
        dimension_value_map=dim_value_map,
        ingestion_batch_id=ingestion_batch_id,
        limit=args.limit,
    )
    stats.skipped_null_values += skipped_null

    print(f"[{table_id}] flattened observations: {len(rows)}")
    print(f"[{table_id}] skipped null values: {skipped_null}")

    if args.dry_run:
        print(f"[{table_id}] dry-run: no inserts executed.")
        stats.tables_processed += 1
        return

    existing = _existing_observations_count(client, table_id, raw_file.name)
    if existing > 0 and not args.allow_existing:
        print(
            f"[{table_id}] WARNING: found {existing} existing observations for table_id={table_id} and source_file={raw_file.name}. "
            "Use --allow-existing to continue."
        )
        stats.warnings += 1
        stats.tables_skipped += 1
        return
    if existing > 0:
        print(f"[{table_id}] existing observations found ({existing}); continuing due to --allow-existing.")
        stats.warnings += 1

    inserted = _insert_observations_batched(client, table_id, rows, args.batch_size)
    stats.observations_inserted += inserted
    stats.tables_processed += 1
    print(f"[{table_id}] inserted observations: {inserted}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import SSB observations into statistical observation tables.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and count without inserting/upserting.")
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
        raise RuntimeError("--batch-size must be > 0")
    if args.limit is not None and args.limit <= 0:
        raise RuntimeError("--limit must be > 0")

    selected_tables = [args.table] if args.table else TABLE_IDS
    for t in selected_tables:
        if t not in TABLE_IDS:
            raise RuntimeError(f"Unsupported table '{t}'. Allowed: {TABLE_IDS}")

    print("SSB observation import MVP")
    print(f"tables: {selected_tables}")
    print(f"dry_run: {args.dry_run}")
    print(f"limit: {args.limit}")
    print(f"batch_size: {args.batch_size}")
    print(f"allow_existing: {args.allow_existing}")

    client = _load_env_client()
    source_id = _find_ssb_source_id(client)
    if source_id:
        print(f"resolved SSB source_id: {source_id}")
    else:
        print("warning: could not resolve SSB source_id from sources; using NULL source_id")

    stats = Stats()
    ingest_seed = f"{_now_utc()}|{selected_tables}|{args.limit}|{args.dry_run}"
    ingestion_batch_id = f"ssb-import-{hashlib.sha1(ingest_seed.encode('utf-8')).hexdigest()[:12]}"
    print(f"ingestion_batch_id: {ingestion_batch_id}")

    try:
        for table_id in selected_tables:
            _process_table(client, table_id, args, source_id, stats, ingestion_batch_id)
    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR: {exc}", file=sys.stderr)
        print(
            "\nPartial summary before failure:\n"
            f"  datasets upserted: {stats.datasets_upserted}\n"
            f"  dimensions upserted: {stats.dimensions_upserted}\n"
            f"  dimension values upserted: {stats.dimension_values_upserted}\n"
            f"  observations inserted: {stats.observations_inserted}\n"
            f"  skipped null values: {stats.skipped_null_values}\n"
            f"  warnings: {stats.warnings}\n"
            f"  tables processed: {stats.tables_processed}\n"
            f"  tables skipped: {stats.tables_skipped}",
            file=sys.stderr,
        )
        return 1

    print("\n--- Summary ---")
    print(f"datasets upserted: {stats.datasets_upserted}")
    print(f"dimensions upserted: {stats.dimensions_upserted}")
    print(f"dimension values upserted: {stats.dimension_values_upserted}")
    print(f"observations inserted: {stats.observations_inserted}")
    print(f"skipped null values: {stats.skipped_null_values}")
    print(f"warnings: {stats.warnings}")
    print(f"tables processed: {stats.tables_processed}")
    print(f"tables skipped: {stats.tables_skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


#!/usr/bin/env python3
"""
SSB JSON-stat2 normalization inspection MVP.

Reads local raw SSB JSON files for selected tables, inspects metadata/dimensions,
flattens observations to row format, and writes CSV previews.

Scope tables:
  - 11615
  - 12850
  - 08417
  - 09793
"""

from __future__ import annotations

import csv
import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TABLE_IDS = ["11615", "12850", "08417", "09793"]
ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "ssb"
OUT_DIR = ROOT / "data" / "processed" / "ssb_preview"


@dataclass
class TableReport:
    table_id: str
    files_used: list[str]
    row_count: int
    missing_values: int
    dimensions: list[str]
    cardinality: dict[str, int]
    has_time_dimension: bool
    fits_shared_observation_model: bool
    sample_rows: list[dict[str, Any]]


def _is_candidate_raw(path: Path, table_id: str) -> bool:
    name = path.name
    if not name.startswith(f"{table_id}_"):
        return False
    if name.endswith("_metadata.json") or name.endswith("_basic_metadata.json"):
        return False
    return True


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Failed to read JSON {path}: {exc}")
        return None
    if not isinstance(payload, dict):
        return None
    # Some files wrap sample data as {"request_method_used": "...", "data": {...}}
    if isinstance(payload.get("data"), dict) and payload["data"].get("class") == "dataset":
        return payload["data"]
    if payload.get("class") == "dataset":
        return payload
    return None


def _dim_positions(dim_meta: dict[str, Any]) -> list[str]:
    idx = ((dim_meta.get("category") or {}).get("index") or {})
    if not isinstance(idx, dict) or not idx:
        return []
    ordered = sorted(idx.items(), key=lambda kv: kv[1])
    return [str(code) for code, _ in ordered]


def _dim_label(dim_meta: dict[str, Any], code: str) -> str | None:
    labels = ((dim_meta.get("category") or {}).get("label") or {})
    if isinstance(labels, dict):
        label = labels.get(code)
        if label is not None:
            return str(label)
    return None


def _time_dimension_id(dataset: dict[str, Any]) -> str | None:
    role = dataset.get("role")
    if isinstance(role, dict):
        t = role.get("time")
        if isinstance(t, list) and t:
            return str(t[0])
    ids = dataset.get("id") or []
    if isinstance(ids, list):
        for c in ids:
            c_str = str(c)
            if c_str.lower() in ("tid", "time", "år", "aar"):
                return c_str
    return None


def _unit_for_cell(dataset: dict[str, Any], dim_code_values: dict[str, str]) -> str | None:
    # JSON-stat2 extensions can expose unit under dimension.<metric>.category.unit.<code>
    # Try metric role dimension first.
    role = dataset.get("role")
    metric_dim = None
    if isinstance(role, dict):
        metric = role.get("metric")
        if isinstance(metric, list) and metric:
            metric_dim = str(metric[0])
    dims = dataset.get("dimension") or {}
    if not isinstance(dims, dict) or not metric_dim:
        return None
    dim_meta = dims.get(metric_dim)
    if not isinstance(dim_meta, dict):
        return None
    code = dim_code_values.get(metric_dim)
    if not code:
        return None
    unit = (((dim_meta.get("category") or {}).get("unit") or {}).get(code) or {})
    if isinstance(unit, dict):
        if unit.get("label"):
            return str(unit["label"])
        if unit.get("base"):
            return str(unit["base"])
    return None


def flatten_jsonstat_dataset(table_id: str, source_file: Path, dataset: dict[str, Any]) -> list[dict[str, Any]]:
    ids = dataset.get("id")
    size = dataset.get("size")
    values = dataset.get("value")
    dims = dataset.get("dimension")

    if not isinstance(ids, list) or not isinstance(size, list) or not isinstance(values, list) or not isinstance(dims, dict):
        raise ValueError("Unexpected JSON-stat2 shape: missing id/size/value/dimension")

    dimension_ids = [str(x) for x in ids]
    positions_per_dim: dict[str, list[str]] = {}
    for dim_id in dimension_ids:
        meta = dims.get(dim_id)
        if not isinstance(meta, dict):
            raise ValueError(f"Unexpected metadata shape for dimension '{dim_id}'")
        positions = _dim_positions(meta)
        if not positions:
            raise ValueError(f"Empty or missing category.index for dimension '{dim_id}'")
        positions_per_dim[dim_id] = positions

    # Cartesian expansion follows dimension order in `id`.
    combos = itertools.product(*(positions_per_dim[d] for d in dimension_ids))
    time_dim = _time_dimension_id(dataset)
    rows: list[dict[str, Any]] = []
    for idx, combo in enumerate(combos):
        if idx >= len(values):
            break
        val = values[idx]
        code_map = {dimension_ids[i]: combo[i] for i in range(len(dimension_ids))}
        label_map = {}
        for d_id, code in code_map.items():
            d_meta = dims.get(d_id)
            if isinstance(d_meta, dict):
                label_map[d_id] = _dim_label(d_meta, code)
            else:
                label_map[d_id] = None

        period = code_map.get(time_dim) if time_dim else None
        unit = _unit_for_cell(dataset, code_map)
        row: dict[str, Any] = {
            "table_id": table_id,
            "source_file": source_file.name,
            "period": period,
            "value": val,
            "unit": unit,
            "dimension_ids_json": json.dumps(dimension_ids, ensure_ascii=False),
            "dimension_codes_json": json.dumps(code_map, ensure_ascii=False),
            "dimension_labels_json": json.dumps(label_map, ensure_ascii=False),
            # Keep full original dimension metadata for traceability/debug.
            "raw_dimension_json": json.dumps(dims, ensure_ascii=False),
        }
        # Add explicit dynamic columns for easier manual inspection.
        for d_id in dimension_ids:
            row[f"dim_{d_id}_code"] = code_map.get(d_id)
            row[f"dim_{d_id}_label"] = label_map.get(d_id)
        rows.append(row)
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with path.open("w", newline="", encoding="utf-8") as f:
            f.write("")
        return

    # Union all keys to handle dynamic dim columns.
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _table_files(table_id: str) -> list[Path]:
    files = sorted(
        [p for p in RAW_DIR.glob("*.json") if _is_candidate_raw(p, table_id)],
        key=lambda p: p.name,
    )
    # Prefer full timestamp snapshots and exclude sample wrapper unless no full file.
    full = [p for p in files if p.stem.count("_") >= 1 and not p.name.endswith("_sample_data.json")]
    if full:
        return full
    return files


def _dimension_summary(dataset: dict[str, Any]) -> tuple[list[str], dict[str, int]]:
    dims = dataset.get("dimension") or {}
    if not isinstance(dims, dict):
        return [], {}
    names = []
    card = {}
    for dim_id, meta in dims.items():
        names.append(str(dim_id))
        idx = ((meta or {}).get("category") or {}).get("index") if isinstance(meta, dict) else None
        card[str(dim_id)] = len(idx) if isinstance(idx, dict) else 0
    return names, card


def process_table(table_id: str) -> tuple[list[dict[str, Any]], TableReport]:
    files = _table_files(table_id)
    all_rows: list[dict[str, Any]] = []
    missing_values = 0
    dimensions: list[str] = []
    cardinality: dict[str, int] = {}
    has_time_dim = False

    for fp in files:
        data = _load_json(fp)
        if data is None:
            print(f"[{table_id}] skipping non-dataset JSON file: {fp.name}")
            continue
        try:
            rows = flatten_jsonstat_dataset(table_id, fp, data)
        except Exception as exc:  # noqa: BLE001
            print(f"[{table_id}] flatten error in {fp.name}: {exc}")
            continue
        all_rows.extend(rows)
        missing_values += sum(1 for r in rows if r.get("value") is None)
        dims, card = _dimension_summary(data)
        if dims:
            dimensions = dims
            cardinality = card
        if _time_dimension_id(data):
            has_time_dim = True

    preview_path = OUT_DIR / f"{table_id}_preview.csv"
    _write_csv(preview_path, all_rows)

    fits_shared = bool(all_rows) and has_time_dim
    report = TableReport(
        table_id=table_id,
        files_used=[p.name for p in files],
        row_count=len(all_rows),
        missing_values=missing_values,
        dimensions=dimensions,
        cardinality=cardinality,
        has_time_dimension=has_time_dim,
        fits_shared_observation_model=fits_shared,
        sample_rows=all_rows[:3],
    )
    return all_rows, report


def _print_report(report: TableReport) -> None:
    print(f"\n=== Table {report.table_id} ===")
    print(f"Files used: {report.files_used}")
    print(f"Row count: {report.row_count}")
    print(f"Dimension names: {report.dimensions}")
    print(f"Dimension cardinality: {report.cardinality}")
    print(f"Missing values: {report.missing_values}")
    print(f"Fits shared observation model: {report.fits_shared_observation_model}")
    if report.sample_rows:
        print("Sample rows:")
        for r in report.sample_rows:
            print(f"  - {json.dumps(r, ensure_ascii=False)[:500]}")


def main() -> int:
    print("SSB JSON-stat2 normalization inspection MVP")
    print(f"Raw directory: {RAW_DIR}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    combined_rows: list[dict[str, Any]] = []
    reports: list[TableReport] = []
    for table_id in TABLE_IDS:
        rows, report = process_table(table_id)
        reports.append(report)
        combined_rows.extend(rows)
        _print_report(report)

    # Combined shared observation preview (safe aligned minimal model + dynamic dims)
    combined_path = OUT_DIR / "ssb_combined_observation_preview.csv"
    _write_csv(combined_path, combined_rows)
    print(f"\nCombined preview rows: {len(combined_rows)}")
    print(f"Combined preview saved: {combined_path}")

    all_fit = all(r.fits_shared_observation_model for r in reports)
    print(f"All tables fit shared observation model: {all_fit}")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


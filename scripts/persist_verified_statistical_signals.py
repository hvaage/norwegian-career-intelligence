#!/usr/bin/env python3
"""
First controlled persistence workflow for manually review-approved preview signals.

Governance-first MVP: inserts ONLY into verified_statistical_signal_batches,
verified_statistical_signals, verified_statistical_signal_sources,
verified_statistical_signal_reviews.

Does NOT modify statistical_observations or any other tables.

See docs/persistent-verified-statistical-signal-persistence-workflow.md
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# Constants (governance)
# -----------------------------------------------------------------------------

PERSISTENCE_LOGIC_VERSION = "persist_verified_statistical_signals_v1.1.0"

REVIEW_DECISION_APPROVE = "approve_for_persistence"
REVIEW_DECISION_REJECT = "reject"
REVIEW_DECISION_QUARANTINE = "quarantine"
REVIEW_DECISION_NEEDS_MORE = "needs_more_review"
VALID_REVIEW_DECISIONS = frozenset(
    {
        REVIEW_DECISION_APPROVE,
        REVIEW_DECISION_REJECT,
        REVIEW_DECISION_QUARANTINE,
        REVIEW_DECISION_NEEDS_MORE,
    }
)

ALLOWED_SIGNAL_TYPES = frozenset(
    {
        "regional_education_employment_signal",
        "industry_education_employment_signal",
    }
)

BLOCKING_QUALITY_FLAGS = frozenset(
    {
        "unstable_slice",
        "preview_not_product_ready",
        "unspecified_category",
        "unspecified_dimension",  # emitted by preview when unspecified slice detected
        "total_category",
    }
)

PREVIEW_CSV = Path("data/processed/signal_preview/signal_preview_rows.csv")
OUTPUT_DIR = Path("data/processed/persistent_signal_preview")

DEFAULT_PREVIEW_PATH = "data/processed/signal_preview/signal_preview_rows.csv"
DEFAULT_SUMMARY_PATH = "data/processed/signal_preview/signal_preview_summary.json"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _parse_json_field(raw: str, field_name: str) -> Any:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {field_name}: {e}") from e


def _parse_quality_flags(raw: str) -> list[str]:
    v = _parse_json_field(raw, "quality_flags")
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return []


def _to_decimal(x: Any) -> Decimal | None:
    if x is None or x == "":
        return None
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return None


def _to_float(x: Any) -> float | None:
    d = _to_decimal(x)
    if d is None:
        return None
    return float(d)


def _parse_observation_ids(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def _period_bounds(row: dict[str, str]) -> tuple[str | None, str | None]:
    exp = _parse_json_field(row.get("explainability_summary_json") or "", "explainability_summary_json")
    if isinstance(exp, dict):
        periods = exp.get("periods")
        if isinstance(periods, dict):
            s, e = periods.get("start"), periods.get("end")
            if s is not None and e is not None:
                return str(s), str(e)
    pc = (row.get("periods_compared") or "").strip()
    if "→" in pc:
        parts = pc.split("→", 1)
        return parts[0].strip() or None, parts[1].strip() or None
    return None, None


def _lineage_dict(row: dict[str, str]) -> dict[str, Any]:
    li = _parse_json_field(row.get("lineage_json") or "", "lineage_json")
    if not isinstance(li, dict):
        return {}
    return dict(li)


def _merge_lineage_for_persist(row: dict[str, str], obs_ids: list[str]) -> dict[str, Any]:
    """Ensure lineage payload documents required provenance fields."""
    base = _lineage_dict(row)
    out = dict(base)
    out["source_observation_ids"] = obs_ids
    sig_count = out.get("source_observation_signature_count")
    if sig_count is None and "source_observation_signature_count" in base:
        pass
    if "source_observation_signature_count" not in out and base.get("source_observation_signature_count") is not None:
        out["source_observation_signature_count"] = base["source_observation_signature_count"]
    if "source_dataset_ids" not in out:
        out["source_dataset_ids"] = base.get("source_dataset_ids") or []
    if "source_dataset_version_ids" not in out:
        out["source_dataset_version_ids"] = base.get("source_dataset_version_ids") or []
    return out


def load_review_decisions(path: Path) -> dict[str, dict[str, str]]:
    """
    Load manual review decisions keyed by signal_deterministic_hash (last row wins on duplicate hash).
    Expected columns: signal_deterministic_hash, review_decision, reviewer_id, reviewed_at, review_notes
    """
    if not path.is_file():
        raise FileNotFoundError(f"Review decisions CSV not found: {path}")
    out: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"signal_deterministic_hash", "review_decision", "reviewer_id", "reviewed_at", "review_notes"}
        if reader.fieldnames:
            cols = {c.strip() for c in reader.fieldnames if c and str(c).strip()}
            if not required.issubset(cols):
                raise ValueError(
                    f"Review decisions CSV missing columns {sorted(required - cols)}; have {sorted(cols)}"
                )
        for lineno, raw in enumerate(reader, start=2):
            h = (raw.get("signal_deterministic_hash") or "").strip()
            if not h:
                continue
            raw_dec = (raw.get("review_decision") or "").strip()
            dnorm = _normalize_review_decision(raw_dec)
            if dnorm is None:
                raise ValueError(f"{path}: line {lineno}: invalid review_decision {raw_dec!r}")
            rid = (raw.get("reviewer_id") or "").strip()
            if dnorm == REVIEW_DECISION_APPROVE and not rid:
                raise ValueError(f"{path}: line {lineno}: reviewer_id required for approve_for_persistence")
            out[h] = {
                "signal_deterministic_hash": h,
                "review_decision": dnorm,
                "reviewer_id": rid,
                "reviewed_at": (raw.get("reviewed_at") or "").strip(),
                "review_notes": (raw.get("review_notes") or "").strip(),
            }
    return out


def _normalize_review_decision(raw: str) -> str | None:
    s = (raw or "").strip().lower()
    if s in VALID_REVIEW_DECISIONS:
        return s
    return None


def signal_type_reason(row: dict[str, str], signal_type_filter: str | None) -> str | None:
    st = (row.get("signal_type") or "").strip()
    if signal_type_filter and st != signal_type_filter:
        return "signal_type_filtered"
    if st not in ALLOWED_SIGNAL_TYPES:
        return "signal_type_not_allowed"
    return None


def _thresholds(row: dict[str, str]) -> tuple[Decimal, Decimal]:
    """Returns (min_baseline, min_absolute_change) from row / explainability_summary."""
    mb = _to_decimal(row.get("min_baseline")) or Decimal("100")
    exp = _parse_json_field(row.get("explainability_summary_json") or "", "explainability_summary_json")
    ma = Decimal("10")
    if isinstance(exp, dict):
        ta = exp.get("thresholds_applied")
        if isinstance(ta, dict):
            m = _to_decimal(ta.get("min_absolute_change"))
            if m is not None:
                ma = m
            m2 = _to_decimal(ta.get("min_baseline"))
            if m2 is not None:
                mb = m2
    return mb, ma


def eligibility_after_signal_type(
    row: dict[str, str],
    *,
    ignore_preview_not_product_ready: bool,
) -> str | None:
    """
    Statistical and governance checks after signal_type allowlist passes.
    If ignore_preview_not_product_ready is True, preview_not_product_ready in quality_flags
    does not block (human approve_for_persistence only); all other blocking flags still apply.
    """
    if (row.get("confidence_category") or "").strip() != "verified_statistical":
        return "confidence_category"

    cs = _to_float(row.get("confidence_score"))
    if cs is None or cs < 0.9:
        return "confidence_score"

    sq = _to_float(row.get("signal_quality_score"))
    if sq is None or sq < 0.8:
        return "signal_quality_score"

    mb, ma = _thresholds(row)
    vs = _to_decimal(row.get("value_start"))
    ac = _to_decimal(row.get("absolute_change"))
    if vs is None:
        return "value_start_missing"
    if vs < mb:
        return "baseline_below_min"
    if ac is None:
        return "absolute_change_missing"
    # Preview uses min|Δ|; rule text uses "absolute_change >= min_absolute_change" interpretively.
    if abs(ac) < ma:
        return "absolute_change_below_min"

    flags = set(_parse_quality_flags(row.get("quality_flags") or ""))
    effective_block = set(BLOCKING_QUALITY_FLAGS)
    if ignore_preview_not_product_ready:
        effective_block.discard("preview_not_product_ready")
    blocked = flags & effective_block
    if blocked:
        return f"blocked_quality_flags:{','.join(sorted(blocked))}"

    note = (row.get("explainability_note") or "").strip()
    if not note:
        return "explainability_note_missing"

    lineage = _lineage_dict(row)
    if not lineage:
        return "lineage_json_missing"

    h = (row.get("signal_deterministic_hash") or "").strip()
    if not h:
        return "signal_deterministic_hash_missing"

    obs_ids = _parse_observation_ids(row.get("source_observation_ids") or "")
    if not obs_ids:
        return "source_observation_ids_missing"

    return None


def _signal_metadata_base() -> dict[str, Any]:
    return {"signal_stability": "stable"}


def _signal_metadata_with_review(decision_row: dict[str, str]) -> dict[str, Any]:
    m = _signal_metadata_base()
    m["manual_review_decision"] = {
        "review_decision": REVIEW_DECISION_APPROVE,
        "reviewer_id": decision_row.get("reviewer_id") or "",
        "reviewed_at": decision_row.get("reviewed_at") or "",
        "review_notes": decision_row.get("review_notes") or "",
    }
    m["preview_not_product_ready_override"] = True
    return m


def load_preview_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"Preview CSV not found: {path}")
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch_existing_hashes(supabase: Any, hashes: list[str]) -> set[str]:
    if not hashes:
        return set()
    existing: set[str] = set()
    chunk = 80
    for i in range(0, len(hashes), chunk):
        part = hashes[i : i + chunk]
        res = (
            supabase.table("verified_statistical_signals")
            .select("signal_deterministic_hash")
            .in_("signal_deterministic_hash", part)
            .execute()
        )
        for r in res.data or []:
            h = r.get("signal_deterministic_hash")
            if h:
                existing.add(str(h))
    return existing


def fetch_observations(supabase: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    chunk = 80
    fields = (
        "id,table_id,source_file,period,value,unit,dimensions_json,"
        "dimension_labels_json,observation_signature"
    )
    for i in range(0, len(ids), chunk):
        part = ids[i : i + chunk]
        res = supabase.table("statistical_observations").select(fields).in_("id", part).execute()
        for r in res.data or []:
            rid = str(r.get("id"))
            out[rid] = r
    return out


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fn = fieldnames
    if not fn and rows:
        fn = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        if not fn:
            f.write("")
            return
        w = csv.DictWriter(f, fieldnames=fn, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fn})


def main() -> int:
    load_dotenv()
    root = _repo_root()
    os.chdir(root)

    p = argparse.ArgumentParser(description="Persist approved verified statistical preview signals (MVP).")
    p.add_argument("--preview-csv", default=str(PREVIEW_CSV), help="Path to signal_preview_rows.csv")
    p.add_argument("--limit", type=int, default=None, help="Max signals to persist after eligibility + dedupe")
    p.add_argument(
        "--signal-type",
        default=None,
        help="Restrict to one signal_type (must be in allowed MVP set)",
    )
    p.add_argument("--dry-run", action="store_true", help="Validate and write previews; no INSERTs")
    p.add_argument("--reviewer-id", default=os.getenv("PERSISTENCE_REVIEWER_ID", "manual_reviewer"), type=str)
    p.add_argument("--review-notes", default="", type=str)
    p.add_argument(
        "--review-decisions-file",
        default=None,
        help="CSV of manual decisions (approve_for_persistence bypasses preview_not_product_ready only).",
    )
    args = p.parse_args()

    if args.signal_type and args.signal_type not in ALLOWED_SIGNAL_TYPES:
        print(f"ERROR: --signal-type must be one of {sorted(ALLOWED_SIGNAL_TYPES)}", file=sys.stderr)
        return 2

    preview_path = Path(args.preview_csv)
    t0 = time.perf_counter()

    rows = load_preview_csv(preview_path)
    loaded = len(rows)

    decisions_by_hash: dict[str, dict[str, str]] | None = None
    decisions_rel: str | None = None
    if args.review_decisions_file:
        dp = Path(args.review_decisions_file)
        decisions_by_hash = load_review_decisions(dp)
        try:
            decisions_rel = str(dp.resolve().relative_to(root.resolve()))
        except ValueError:
            decisions_rel = str(dp)

    preview_hashes = {(r.get("signal_deterministic_hash") or "").strip() for r in rows if (r.get("signal_deterministic_hash") or "").strip()}
    orphaned_decision_hashes: list[str] = []
    if decisions_by_hash is not None:
        orphaned_decision_hashes = sorted(h for h in decisions_by_hash if h not in preview_hashes)

    decision_outcome_counts: dict[str, int] = {}
    if decisions_by_hash is not None:
        decision_outcome_counts = dict(Counter(r["review_decision"] for r in decisions_by_hash.values()))

    eligible: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    reject_counts: dict[str, int] = {}
    decision_context_by_hash: dict[str, dict[str, str]] = {}

    for row in rows:
        rr = dict(row)
        st_reason = signal_type_reason(row, args.signal_type)
        if st_reason:
            rr["reject_reason"] = st_reason
            rejected.append(rr)
            reject_counts[st_reason] = reject_counts.get(st_reason, 0) + 1
            continue

        h = (row.get("signal_deterministic_hash") or "").strip()

        if decisions_by_hash is not None:
            if not h:
                rr["reject_reason"] = "signal_deterministic_hash_missing"
                rejected.append(rr)
                reject_counts[rr["reject_reason"]] = reject_counts.get(rr["reject_reason"], 0) + 1
                continue
            rec = decisions_by_hash.get(h)
            if rec is None:
                rr["reject_reason"] = "not_review_approved"
                rejected.append(rr)
                reject_counts[rr["reject_reason"]] = reject_counts.get(rr["reject_reason"], 0) + 1
                continue
            d = rec["review_decision"]
            if d == REVIEW_DECISION_REJECT:
                rr["reject_reason"] = "review_decision_reject"
                rejected.append(rr)
                reject_counts[rr["reject_reason"]] = reject_counts.get(rr["reject_reason"], 0) + 1
                continue
            if d == REVIEW_DECISION_QUARANTINE:
                rr["reject_reason"] = "review_decision_quarantine"
                rejected.append(rr)
                reject_counts[rr["reject_reason"]] = reject_counts.get(rr["reject_reason"], 0) + 1
                continue
            if d == REVIEW_DECISION_NEEDS_MORE:
                rr["reject_reason"] = "review_decision_needs_more_review"
                rejected.append(rr)
                reject_counts[rr["reject_reason"]] = reject_counts.get(rr["reject_reason"], 0) + 1
                continue
            assert d == REVIEW_DECISION_APPROVE
            core = eligibility_after_signal_type(row, ignore_preview_not_product_ready=True)
            if core:
                rr["reject_reason"] = core
                rejected.append(rr)
                reject_counts[core] = reject_counts.get(core, 0) + 1
            else:
                eligible.append(row)
                decision_context_by_hash[h] = rec
        else:
            reason = eligibility_after_signal_type(row, ignore_preview_not_product_ready=False)
            if reason is None:
                eligible.append(row)
            else:
                rr["reject_reason"] = reason
                rejected.append(rr)
                reject_counts[reason] = reject_counts.get(reason, 0) + 1

    if args.limit is not None and args.limit >= 0:
        eligible = eligible[: args.limit]

    hashes = [(r.get("signal_deterministic_hash") or "").strip() for r in eligible]
    hashes = [h for h in hashes if h]

    duplicates_skipped = 0
    to_insert: list[dict[str, str]] = []

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    if not args.dry_run and (not url or not key):
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required for live persistence.", file=sys.stderr)
        return 1

    supabase: Any = None
    if url and key:
        from supabase import create_client

        supabase = create_client(url, key)
    elif args.dry_run:
        print("WARN: Missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY — duplicate and observation checks skipped.")

    existing_hashes: set[str] = set()
    if supabase:
        existing_hashes = fetch_existing_hashes(supabase, hashes)

    for row in eligible:
        h = (row.get("signal_deterministic_hash") or "").strip()
        if h in existing_hashes:
            duplicates_skipped += 1
            rr = dict(row)
            rr["reject_reason"] = "duplicate_hash_already_persisted"
            rejected.append(rr)
            reject_counts["duplicate_hash_already_persisted"] = (
                reject_counts.get("duplicate_hash_already_persisted", 0) + 1
            )
            continue
        to_insert.append(row)

    # Observation validation (required for live; dry-run with client validates too)
    obs_by_id: dict[str, dict[str, Any]] = {}
    final_rows: list[dict[str, str]] = []
    if supabase and to_insert:
        all_obs: list[str] = []
        for r in to_insert:
            all_obs.extend(_parse_observation_ids(r.get("source_observation_ids") or ""))
        uniq_obs = list(dict.fromkeys(all_obs))
        obs_by_id = fetch_observations(supabase, uniq_obs)
        for r in to_insert:
            ids = _parse_observation_ids(r.get("source_observation_ids") or "")
            missing = [oid for oid in ids if oid not in obs_by_id]
            if missing:
                rr = dict(r)
                rr["reject_reason"] = f"observation_not_found:{','.join(missing[:3])}"
                rejected.append(rr)
                k = "observation_not_found"
                reject_counts[k] = reject_counts.get(k, 0) + 1
            else:
                final_rows.append(r)
    else:
        final_rows = list(to_insert)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    persisted_fields = list(rows[0].keys()) if rows else []
    write_csv(OUTPUT_DIR / "persisted_signals_preview.csv", final_rows, persisted_fields)
    rej_fields = (list(rows[0].keys()) + ["reject_reason"]) if rows else ["reject_reason"]
    write_csv(OUTPUT_DIR / "rejected_signals_preview.csv", rejected, rej_fields)

    batch_id: str | None = None
    signals_written = 0
    sources_written = 0
    reviews_written = 0

    if not args.dry_run and final_rows and supabase:
        now = datetime.now(timezone.utc)
        batch_slug = f"persist-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:10]}"
        first_lineage = _lineage_dict(final_rows[0])
        gen_ts = first_lineage.get("generation_timestamp_utc")
        if gen_ts:
            try:
                gen_ts_parsed = datetime.fromisoformat(str(gen_ts).replace("Z", "+00:00"))
            except ValueError:
                gen_ts_parsed = now
        else:
            gen_ts_parsed = now

        batch_row = {
            "batch_slug": batch_slug,
            "source_preview_file": str(preview_path).replace(str(root) + "/", ""),
            "source_summary_file": DEFAULT_SUMMARY_PATH,
            "signal_logic_version": first_lineage.get("signal_logic_version") or "unknown",
            "preview_script_version": first_lineage.get("preview_script_version"),
            "persistence_logic_version": PERSISTENCE_LOGIC_VERSION,
            "generation_timestamp": gen_ts_parsed.isoformat(),
            "persisted_at": now.isoformat(),
            "status": "persisted",
            "review_status": "approved",
            "reviewer_id": args.reviewer_id,
            "approved_by": args.reviewer_id,
            "approved_at": now.isoformat(),
            "notes": args.review_notes or None,
            "metadata_json": {
                "persistence_mvp": True,
                "reviewer_type": "manual_review_round1",
                "workflow": PERSISTENCE_LOGIC_VERSION,
                **(
                    {
                        "manual_review_decisions_file": decisions_rel,
                        "manual_review_decisions_summary": {
                            "unique_hashes_in_file": len(decisions_by_hash),
                            "decisions_by_outcome": decision_outcome_counts,
                            "orphaned_hashes_not_in_preview": orphaned_decision_hashes,
                        },
                    }
                    if decisions_by_hash is not None
                    else {}
                ),
            },
            "quality_summary_json": {
                "preview_rows_loaded": loaded,
                "eligible_before_dedupe": len(eligible),
                "after_duplicate_filter": len(to_insert),
                "after_observation_validation": len(final_rows),
                "reject_counts": reject_counts,
                "review_decisions_file": decisions_rel,
            },
        }

        br = supabase.table("verified_statistical_signal_batches").insert(batch_row).execute()
        if not br.data:
            print("ERROR: batch insert returned no data", file=sys.stderr)
            return 3
        batch_id = str(br.data[0]["id"])

        for r in final_rows:
            ps, pe = _period_bounds(r)
            obs_ids = _parse_observation_ids(r.get("source_observation_ids") or "")
            lineage = _merge_lineage_for_persist(r, obs_ids)
            cs = _to_decimal(r.get("confidence_score"))
            sq = _to_decimal(r.get("signal_quality_score"))
            hkey = (r.get("signal_deterministic_hash") or "").strip()
            dec_rec = decision_context_by_hash.get(hkey) if decisions_by_hash is not None else None
            sig_meta = _signal_metadata_with_review(dec_rec) if dec_rec else _signal_metadata_base()

            sig_row = {
                "batch_id": batch_id,
                "signal_type": r.get("signal_type"),
                "signal_label": r.get("signal_label"),
                "signal_deterministic_hash": (r.get("signal_deterministic_hash") or "").strip(),
                "signal_logic_version": lineage.get("signal_logic_version") or "unknown",
                "source_system": "ssb",
                "table_id": r.get("table_id"),
                "source_table": r.get("source_table"),
                "periods_compared": r.get("periods_compared"),
                "period_start": ps,
                "period_end": pe,
                "period_type": r.get("period_type"),
                "period_granularity": r.get("period_granularity"),
                "value_start": _to_float(r.get("value_start")),
                "value_end": _to_float(r.get("value_end")),
                "absolute_change": _to_float(r.get("absolute_change")),
                "percent_change": _to_float(r.get("percent_change")),
                "direction_label": r.get("direction_label"),
                "confidence_category": r.get("confidence_category"),
                "confidence_score": float(cs) if cs is not None else None,
                "signal_quality_score": float(sq) if sq is not None else None,
                "review_status": "approved",
                "lifecycle_status": "active",
                "persistence_eligibility": "eligible",
                "quality_flags": _parse_quality_flags(r.get("quality_flags") or ""),
                "quality_reasoning_json": _parse_json_field(r.get("quality_reasoning_json") or "", "quality_reasoning_json")
                or {},
                "dimensions_json": _parse_json_field(r.get("dimensions_json") or "", "dimensions_json") or {},
                "dimension_labels_json": _parse_json_field(r.get("dimension_labels_json") or "", "dimension_labels_json")
                or {},
                "explainability_note": r.get("explainability_note"),
                "explainability_summary_json": _parse_json_field(r.get("explainability_summary_json") or "", "explainability_summary_json")
                or {},
                "lineage_json": lineage,
                "metadata_json": sig_meta,
            }
            sr = supabase.table("verified_statistical_signals").insert(sig_row).execute()
            if not sr.data:
                print("ERROR: signal insert failed", r.get("signal_deterministic_hash"), file=sys.stderr)
                return 4
            signal_id = str(sr.data[0]["id"])
            signals_written += 1

            for oid in obs_ids:
                ob = obs_by_id[oid]
                src_row = {
                    "signal_id": signal_id,
                    "statistical_observation_id": oid,
                    "observation_signature": ob.get("observation_signature"),
                    "table_id": ob.get("table_id"),
                    "source_file": ob.get("source_file"),
                    "period": ob.get("period"),
                    "value": float(ob["value"]) if ob.get("value") is not None else None,
                    "unit": ob.get("unit"),
                    "dimensions_json": ob.get("dimensions_json") or {},
                    "dimension_labels_json": ob.get("dimension_labels_json") or {},
                    "role": "source_observation",
                    "metadata_json": {},
                }
                supabase.table("verified_statistical_signal_sources").insert(src_row).execute()
                sources_written += 1

            rev_meta: dict[str, Any] = {
                "reviewer_type": "manual_review_round1",
                "review_notes": args.review_notes or "",
            }
            rev_reviewer = args.reviewer_id
            if dec_rec:
                rev_reviewer = dec_rec.get("reviewer_id") or args.reviewer_id
                rev_meta["manual_review_decision"] = {
                    "review_decision": REVIEW_DECISION_APPROVE,
                    "reviewer_id": dec_rec.get("reviewer_id") or "",
                    "reviewed_at": dec_rec.get("reviewed_at") or "",
                    "review_notes": dec_rec.get("review_notes") or "",
                }
                rev_meta["review_decisions_file"] = decisions_rel
                rev_meta["preview_not_product_ready_override"] = True
                rev_meta["cli_reviewer_id"] = args.reviewer_id

            rev_row = {
                "signal_id": signal_id,
                "batch_id": batch_id,
                "review_status": "approved",
                "reviewer_id": rev_reviewer,
                "review_round": "manual_review_round1",
                "decision": "approve",
                "decision_reason": (dec_rec.get("review_notes") if dec_rec else None)
                or args.review_notes
                or "manual_review_round1 persistence MVP",
                "metadata_json": rev_meta,
            }
            supabase.table("verified_statistical_signal_reviews").insert(rev_row).execute()
            reviews_written += 1

    elapsed = time.perf_counter() - t0

    summary: dict[str, Any] = {
        "persistence_logic_version": PERSISTENCE_LOGIC_VERSION,
        "dry_run": bool(args.dry_run),
        "preview_csv": str(preview_path),
        "review_decisions_file": decisions_rel,
        "manual_review_decisions_in_file": len(decisions_by_hash) if decisions_by_hash else 0,
        "orphaned_decision_hashes_count": len(orphaned_decision_hashes),
        "decision_outcome_counts_in_file": decision_outcome_counts,
        "preview_rows_loaded": loaded,
        "rows_eligible_after_rules": len(eligible),
        "rows_rejected": len(rejected),
        "duplicates_skipped": duplicates_skipped,
        "rows_after_observation_validation": len(final_rows),
        "signals_persisted": signals_written if not args.dry_run else 0,
        "signal_source_rows_persisted": sources_written if not args.dry_run else 0,
        "review_rows_persisted": reviews_written if not args.dry_run else 0,
        "would_persist_signal_count": len(final_rows) if args.dry_run else signals_written,
        "would_persist_source_rows": sum(len(_parse_observation_ids(r.get("source_observation_ids") or "")) for r in final_rows)
        if args.dry_run
        else sources_written,
        "would_persist_review_rows": len(final_rows) if args.dry_run else reviews_written,
        "batch_id": batch_id,
        "reject_reason_counts": reject_counts,
        "runtime_seconds": round(elapsed, 3),
        "signal_type_filter": args.signal_type,
        "limit": args.limit,
        "reviewer_id": args.reviewer_id,
    }

    out_json = OUTPUT_DIR / "persistence_summary.json"
    out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("--- persist_verified_statistical_signals ---")
    print(f"  review decisions file:           {decisions_rel or '(none)'}")
    print(f"  preview rows loaded:              {loaded}")
    print(f"  rows eligible (rules + limit):   {len(eligible)}")
    print(f"  rows rejected (cumulative):      {len(rejected)}")
    print(f"  duplicates skipped:              {duplicates_skipped}")
    print(f"  rows after obs validation:       {len(final_rows)}")
    print(f"  signals persisted:               {signals_written}")
    print(f"  signal source rows persisted:    {sources_written}")
    print(f"  review rows persisted:           {reviews_written}")
    print(f"  dry_run:                         {args.dry_run}")
    print(f"  runtime_seconds:                 {elapsed:.3f}")
    print(f"  summary_json:                    {out_json}")
    if args.dry_run:
        print(f"  (dry-run) would persist signals: {len(final_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

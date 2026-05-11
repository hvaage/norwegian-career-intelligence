# Verified statistical signal extraction — pilot (preview only)

**Specification and pilot documentation:** no SQL migrations, no writes to `signals` or related intelligence tables, no orchestration, no frontend. This document describes the first **controlled preview** of `verified_statistical`-style signals derived **in memory** from normalized SSB rows in Supabase (`statistical_observations`).

**Related documents:**

- `docs/verified-statistical-signal-generation-mvp.md`
- `docs/statistical-observation-schema.md`
- `docs/ssb-import-validation-checklist.md`
- `docs/scoring-and-signal-model.md`

**Pilot artifact:**

- `scripts/preview_verified_statistical_signals.py`

**Output directory (local only):**

- `data/processed/signal_preview/`

---

## 1. Purpose (preview only)

The pilot proves that:

- observations already in `statistical_observations` can be read safely;
- a small, deterministic set of **explainable** derived summaries can be produced;
- lineage and confidence can be attached **without** touching production signal tables.

**This is not production signal ingestion.** Nothing is inserted into `signals`, `signal_sources`, gaps, overlaps, or RAG/vector stores.

**Preview CSV/JSON is not product-ready:** even after automated quality filters (low baseline, unspecified-style labels, optional `ContentsCode` scoping), outputs are for **manual review** only. Each emitted row includes `quality_flags` with `preview_not_product_ready` until a human accepts the row for downstream use.

---

## 2. Included signal types (MVP preview)

| `signal_type` | Tables | Intent |
|----------------|--------|--------|
| `employment_count_change` | `11615`, `12850` | Two-period change on the **same non-time dimension slice** (time dimension `Tid` excluded from slice identity), same `ContentsCode` where present. Absolute and percent change when allowed. |
| `regional_education_employment_signal` | `11615` | Same comparison logic as employment change, restricted to rows whose `dimensions_json` includes **`Region`** and at least one of **`Fagfelt`**, **`UtdNivaa`**. Human-oriented label. |
| `industry_education_employment_signal` | `12850` | Same comparison logic, restricted to rows including **`NACE2007`** and **`Fagfelt`** or **`UtdNivaa`**. |
| `occupation_structure_signal` | `09793` | **Latest period only:** per-occupation (`Yrke`) slice, observed employment-related value. **No trend** unless at least two distinct periods exist in the fetched window; if only one period, direction is a neutral snapshot label. |
| `education_level_workforce_signal` | `08417` | **Latest period:** workforce-style counts by **`UtdNivaa`** and **`HeltidDeltid`** (when present). Snapshot only in the default pilot path. |

---

## 3. Excluded (explicitly not in this pilot)

- Writes to **`signals`**, **`signal_sources`**, **`signal_relationships`**
- **Recommendations**, **gaps**, **overlaps**
- **RAG / embeddings / vector** tables
- **NAV** or non-SSB sources
- **Cross-table fused signals** (e.g. joining 11615 with 09793)
- **Taxonomy mapping** beyond what is already in `dimensions_json` / `dimension_labels_json`
- **Forecasting**, **seasonal adjustment**, **smoothing**
- **Machine-learned** or opaque composite scores

---

## 4. Thresholds (conservative)

| Rule | Condition |
|------|------------|
| **Growth** | `percent_change` is not null and `percent_change >= 5` |
| **Decline** | `percent_change` is not null and `percent_change <= -5` |
| **Stable** | `percent_change` is not null and between -5 and 5 (exclusive of growth/decline bands as above) |
| **No percent** | If the earlier period value is **0**, **do not** compute `percent_change` (and **do not** emit change-style preview rows that depend on `%` thresholds for that pair—baseline-zero pairs are skipped as **skipped_zero_baseline**). |

Absolute change is still computed as `value_end - value_start` when two periods exist; directional labeling for the pilot relies on **percent_change** when present, per the table above.

---

## 4b. Quality filters (noise reduction before review)

These flags apply in `scripts/preview_verified_statistical_signals.py` and are intended to shrink **low-value micro-signals** before you read CSVs.

| CLI | Default | Effect |
|-----|---------|--------|
| `--min-baseline` | `100` | **Change-style signals only:** if `value_start` (earlier period) is **strictly less** than this threshold, the script **does not emit** that growth/decline/stable row. Counted as **`skipped_low_baseline`**. Does not replace `skipped_zero_baseline` (zero still handled at pair construction). |
| `--exclude-unspecified` / `--include-unspecified` | exclude **on** | When exclusion is on, **rows or two-period pairs** whose `dimension_labels_json` text (all values, case-insensitive) contains any of: `Uoppgitt`, `unspecified`, `unknown`, `not stated`, `ikke oppgitt` are skipped. Change-style: counted as **`skipped_unspecified_category`** at emit time. Snapshots: contributing **observation rows** with those phrases are dropped before aggregation; each dropped row increments **`skipped_unspecified_category`**. Use `--include-unspecified` to disable this filter. |
| `--contents-code CODE` | *(none)* | Restricts Supabase reads to **exact** `contents_code` match (e.g. `SysselsatteBosted`), including **balanced-period** discovery and per-period fetches. When omitted, all codes for the table remain eligible; **`signal_label`** and **`explainability_note`** still append the resolved **ContentsCode label** (from `dimension_labels_json['ContentsCode']` when present, else the raw code). |

CSV/JSON output adds **`quality_flags`** (JSON array string), **`min_baseline`** (threshold for change rows; empty for snapshots), **`contents_code`**, and **`contents_code_label`** on each preview row.

---

## 5. Confidence logic (pilot)

| Situation | `confidence_score` | Notes |
|-----------|-------------------|--------|
| **Direct snapshot** of a single observation or summed slice for one period (structure / workforce snapshots) | `1.0` | `confidence_category`: `verified_statistical` |
| **Simple two-period** change on the same slice from authoritative observations | `0.9` | Still `verified_statistical` category for the pilot preview payload; strength of *interpretation* is slightly discounted vs raw cell citation. |

Anything requiring **extra taxonomy mapping** or **multi-hop aggregation** beyond dimensions already on the row is **out of scope** for this script.

---

## 6. Period handling

- Observations are compared on **`period`** text as stored (e.g. `2023`, `2024`).
- Periods are sorted using a **lightweight parser**: 4-digit years sort numerically; other formats sort lexicographically as fallback.
- **Missing prior period:** if a slice does not have **two** usable periods after sorting, **no** change-style signal is emitted (`skipped_missing_prior_period`).
- **Change-style previews should use `--balanced-periods`:** the default fetch orders by `period` ascending and caps total rows. For wide tables, the first `--limit` rows are often a single period, so almost every slice lacks a prior period in the sample. With `--balanced-periods`, the script resolves the **two latest distinct** periods for that `table_id`, then fetches up to **`--limit` rows per period** and merges them—so comparable slices across periods are available without raising the total row cap blindly.

---

## 7. Explainability format (each preview row)

Each preview record includes:

- **`signal_type`**, **`signal_label`**
- **`table_id`**, **`source_table`** (same as `table_id` for SSB)
- **`periods_compared`** (e.g. `2023→2024` or single period for snapshots)
- **`value_start`**, **`value_end`**, **`absolute_change`**, **`percent_change`**, **`direction_label`**
- **`confidence_category`** = `verified_statistical`
- **`confidence_score`** as per §5
- **`source_observation_ids`** (UUIDs of contributing observations)
- **`dimensions_json`**, **`dimension_labels_json`** (slice context; may omit `Tid` in duplicated form for readability in CSV—see script implementation)
- **`explainability_note`** (short human-readable sentence)
- **`lineage_json`** (dataset id if available, `source_file`, `normalization_version`, `transformation_version`, script name/version)
- **`quality_flags`**, **`min_baseline`**, **`contents_code`**, **`contents_code_label`** (see §4b)

---

## 8. Why preview-only

- The **`signals`** table in `001_intelligence_foundation.sql` carries product semantics (`signal_type` enum, subject linkage, strength). This pilot **does not** map columns 1:1 to that table yet.
- Governance (threshold owners, sparse-cell policy, versioning) is not finalized (`docs/verified-statistical-signal-generation-mvp.md` open questions).
- Manual review of CSV/JSON is required before any automated insert pipeline.

---

## 9. How this maps later to `signals` (conceptual)

When approved, each preview row could inform a row in `public.signals` with:

- `signal_type` aligned to product enums (e.g. market / risk subtypes) or stored in `payload_json`;
- `confidence_category` / `confidence_score` copied or adjusted;
- `signal_sources` linking to `statistical_observations.id` and dataset version ids;
- `payload_json` holding `dimensions_json`, `periods_compared`, and metrics.

The pilot output is intentionally **flat** (CSV + summary JSON) for diffing and review.

---

## 10. Validation alignment

Cross-check preview outputs with:

- `docs/ssb-import-validation-checklist.md` (lineage completeness, period sanity)
- Row counts vs known table cardinalities for the selected `--limit`

---

## 11. Operational notes

- Requires **Supabase** env vars (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`) and migration **`003`** if selecting `observation_signature` (script tolerates missing column by selecting a fixed column set).
- Use **`--limit`** to cap rows **per table** read from Supabase during the pilot. **Very small limits** (e.g. 100) often return **no two-period slices** for change-style signals, because each `(slice, ContentsCode)` group may not include two distinct periods inside the capped fetch—`skipped_missing_prior_period` will be high and `employment_count_change` may be empty. Raise **`--limit`** (e.g. full table or several thousand) **or** use **`--balanced-periods`** so the cap applies **per period** for change-style generators (see §6).
- For **`employment_count_change`**, **`regional_education_employment_signal`**, and **`industry_education_employment_signal`**, prefer **`--balanced-periods`** whenever you are not scanning the full table, so the preview window includes both latest periods.
- Use **`--min-baseline`**, **`--exclude-unspecified`** (default), and optional **`--contents-code`** to tighten preview sets before review (§4b). Summary JSON includes **`skipped_low_baseline`** and **`skipped_unspecified_category`**.
- Use **`--table`** to restrict to one SSB table id.
- Use **`--signal-type`** to run one generator in isolation.

---

## 12. Open items (pilot follow-up)

- Formal `signals` / `signal_sources` insert mapping and idempotency keys
- Quarter / month period ordering
- Multi-metric (`ContentsCode`) dashboards
- Statistical significance / sample size guards for sparse slices

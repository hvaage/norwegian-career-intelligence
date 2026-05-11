# SSB normalization MVP (JSON-stat2 inspection and flattening)

**Purpose:** define and test a first normalization-inspection workflow for SSB JSON-stat2 across the four selected MVP tables before any Supabase writes.

**Scope tables:** `11615`, `12850`, `08417`, `09793`

**Artifacts:**

- Script: `scripts/inspect_ssb_jsonstat.py`
- Output previews: `data/processed/ssb_preview/`

This is a **normalization design + inspection MVP**, not a production import.

---

## 1. Objective

Understand table structures and flatten JSON-stat2 into comparable observation rows while preserving traceability.

The MVP is designed to answer:

- Can all four tables be represented in a shared observation model?
- Which dimensions are shared vs table-specific?
- Where is over-normalization risk highest?
- What is sufficient lineage for later `verified_statistical` signals?

---

## 2. Input data used

From local raw storage (`data/raw/ssb/`):

- `11615_*.json`
- `12850_*.json`
- `08417_*.json`
- `09793_*.json`
- `*_metadata.json` used as interpretation reference

The script reads JSON-stat2 dataset files directly and ignores non-dataset JSON payloads automatically.

---

## 3. JSON-stat2 structure (as used here)

The relevant JSON-stat2 keys for flattening:

| Key | Role in flattening |
|---|---|
| `id` | ordered list of dimensions |
| `size` | cardinality per dimension in `id` order |
| `dimension` | per-dimension metadata and category mappings |
| `dimension.{dim}.category.index` | code -> position |
| `dimension.{dim}.category.label` | code -> human label |
| `value` | flattened measure array aligned to Cartesian product in `id` order |
| `role.time` | identifies period dimension when available |
| `role.metric` | used for unit lookup where present |

The flattening logic reconstructs each observation by iterating the Cartesian product of dimension category codes in `id` order and aligning it with `value` index position.

---

## 4. Flattening approach

### Output row model (preview)

Each row contains:

- `table_id`
- `source_file`
- `period` (from `role.time` or equivalent dimension when available)
- `value`
- `unit` (if available in metric dimension metadata)
- `dimension_ids_json`
- `dimension_codes_json`
- `dimension_labels_json`
- `raw_dimension_json`
- explicit dynamic columns:
  - `dim_<dimension_id>_code`
  - `dim_<dimension_id>_label`

### Why this model

- preserves statistical semantics,
- keeps dimensions queryable without destructive simplification,
- keeps full metadata lineage for later transformations,
- supports safe shared previews even when schemas differ.

---

## 5. Preview outputs

Generated CSV previews:

- `data/processed/ssb_preview/11615_preview.csv`
- `data/processed/ssb_preview/12850_preview.csv`
- `data/processed/ssb_preview/08417_preview.csv`
- `data/processed/ssb_preview/09793_preview.csv`
- `data/processed/ssb_preview/ssb_combined_observation_preview.csv`

The combined preview is considered **safe** because all rows include a common minimum model (`table_id`, `source_file`, `period`, `value`, JSON dimension maps), while table-specific columns remain additive.

---

## 6. Table-specific differences and compatibility analysis

### Shared dimensions across all four tables

| Dimension family | Presence |
|---|---|
| Time (`Tid`) | Present in all four |
| Metric (`ContentsCode`) | Present in all four |
| Demographic (`Kjonn`) | Present in all four |
| Education (`UtdNivaa`) | Present in all four |

### Table-specific dimensions

| Table | Key specific dimensions |
|---|---|
| `11615` | `Region`, `Alder`, `Fagfelt` |
| `12850` | `Region`, `NACE2007`, `Alder`, `Fagfelt` |
| `08417` | `HeltidDeltid` |
| `09793` | `Yrke`, `Alder` |

### Education dimensions

- Strongly represented (`UtdNivaa` in all tables).
- `Fagfelt` appears in `11615` and `12850` only.
- Implies shared education backbone with optional field-of-study enrichment.

### Occupation dimensions

- `Yrke` is explicit in `09793` only.
- Role-family inference should remain table-aware; do not force role mapping on tables without occupation dimension.

### Demographic dimensions

- `Kjonn` and `Alder` are available in multiple tables.
- `Alder` is not universal across all four, so shared demographic normalization should support null/absent dimensions.

### Temporal dimensions

- `Tid` appears in all tables and is the safest shared index for cross-table joins.

### Compatibility conclusion

All four tables fit a shared observation model **at row level** if:

1. dimensions are stored as flexible key-value maps (or normalized bridge later),
2. table-specific dimensions remain optional,
3. cross-table comparisons are constrained by compatible dimensional subsets.

---

## 7. Normalization risks

### Main risks

| Risk | Impact | MVP mitigation |
|---|---|---|
| Over-normalization of dimensions too early | Loss of statistical meaning | Keep raw dimension JSON + code/label pairs in previews |
| Assuming one universal schema | Invalid joins and misleading aggregates | Table-aware transforms and explicit compatibility checks |
| Flattening without period semantics | Wrong trend interpretation | Always preserve `period`, `observed_at` and source file lineage |
| Losing category hierarchies | Broken rollups and subgroup analysis | Preserve full dimension metadata for later hierarchy extraction |
| Unit ambiguity | Incorrect comparison across tables | Extract/store unit when available; otherwise mark unknown |

### Over-normalization warning

Do **not** force all dimension names into a single rigid set in MVP.  
Use shared observation model + optional dimension handling first, then normalize high-confidence stable dimensions.

---

## 8. Shared observation model (MVP)

### Proposed minimum shared observation schema (conceptual)

| Field | Type (conceptual) | Notes |
|---|---|---|
| `table_id` | string | source table identifier |
| `source_file` | string | raw provenance |
| `period` | string/null | from time dimension |
| `value` | numeric/null | metric value |
| `unit` | string/null | if available |
| `dimension_codes_json` | object | dim -> category code |
| `dimension_labels_json` | object | dim -> category label |
| `raw_dimension_json` | object | full structure for replay/explainability |

This model is intentionally broad and inspectable before stricter production normalization.

---

## 9. What can later become `verified_statistical` signals

From these four tables, likely candidates include:

- labor-market participation/employment structure signals by education level,
- occupation-distribution signals (from `09793`),
- industry participation mix signals (from `12850` via `NACE2007`),
- full-time/part-time structure signals (from `08417`),
- regional and demographic breakdown signals (where dimensions are present).

A row can support `verified_statistical` only when:

1. value lineage is intact (`table_id` + `source_file` + dimension codes + period),
2. transformation logic is explicit and reviewable,
3. confidence is assigned independently from derived interpretation.

---

## 10. Script behavior and diagnostics

The script prints, for each table:

- table id,
- row counts,
- dimension names,
- dimension cardinality,
- missing values,
- sample rows,
- whether the table fits the shared observation model.

It also prints combined row count and combined output path.

---

## 11. MVP recommendations before Supabase import

1. Keep this flattening step as a mandatory pre-import inspection gate.  
2. Freeze one reviewed preview snapshot per table as benchmark fixture.  
3. Define a table-specific compatibility matrix before any cross-table aggregate.  
4. Add reviewer signoff for period interpretation and dimension mapping assumptions.  
5. Only then design the first normalized observation tables for database ingestion.

---

## 12. Summary

This MVP establishes a safe, explainable bridge between raw SSB JSON-stat2 and future normalized intelligence.

It confirms:

- all four selected SSB tables can be flattened into a shared observation shape,
- compatibility exists but is dimension-dependent,
- preserving raw dimension metadata is essential to avoid semantic loss,
- over-normalization is the primary near-term risk,
- verified statistical signals are feasible once lineage and mapping review are enforced.

This is an inspection and normalization-design phase, not production ingestion.


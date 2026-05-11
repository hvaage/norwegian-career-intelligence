# SSB import validation checklist (MVP)

**Specification only:** operational validation criteria for imported SSB statistical observations in the Norwegian Career Intelligence Dataset and **sokr.online**. This document does not define SQL, migrations, or application code.

**Related:** `docs/statistical-ingestion-pipeline-mvp.md`, `docs/statistical-observation-schema.md`, `docs/ssb-normalization-mvp.md`, `docs/scoring-and-signal-model.md`  
**Implementation references:** `scripts/import_ssb_observations.py`, outputs from `scripts/inspect_ssb_jsonstat.py` (`data/processed/ssb_preview/`)

---

# 1. Purpose of validation

## Why observations must be validated before signal generation

The scoring and signal layer treats **verified statistical** evidence as high-trust input to gaps, overlaps, RAG ranking, and recommendations. If normalized rows are structurally wrong, temporally inconsistent, or not traceable to an authoritative source snapshot, downstream signals inherit false confidence. Validation is the gate between **ingestion** and **interpretation**.

## Why statistical correctness matters

Public statistics encode definitions (populations, units, totals, hierarchies). A correct row count or parse is not enough: the **measured fact** must match the **intended slice** of the source table (correct dimension coordinates, period, and metric). Errors here produce plausible-looking numbers that are statistically wrong for the stated slice.

## Why provenance validation matters

Explainability and auditability require that every persisted observation answer: *which dataset, which file snapshot, which transformation, which batch, which source codes?* Without lineage completeness, replay, dispute resolution, and regulatory-style review cannot be satisfied.

## Why normalization validation matters

Normalization maps heterogeneous JSON-stat2 into a shared observation shape. Deterministic expectations (ordering, period extraction, unit attachment, `ContentsCode` handling) must be verified so that **same raw file + same normalization version** yields comparable, reviewable outputs. Drift without version bumps is a governance failure.

## Pipeline position (conceptual)

```text
raw statistics
  → normalized observations
  → validated observations
  → verified_statistical signals   (later; not in current import scope)
```

Validation covers the transition **normalized → validated** before those rows are treated as production-ready for analytics or product surfaces.

---

# 2. MVP validation scope

## In-scope SSB tables (PxWebApi v2 / JSON-stat2)

| `table_id` | MVP role (high level) |
|------------|------------------------|
| **11615** | Regional / field-of-study / demographic / education-rich labor-market structure |
| **12850** | Industry (`NACE2007`) + education + field + region + demographics |
| **08417** | Employment type (`HeltidDeltid`) + gender + education |
| **09793** | Occupation (`Yrke`) + gender + age |

## Explicit exclusions (MVP)

- Only **imported MVP observations** for the four tables above; no ad-hoc expansion without schema + pipeline updates.
- **No forecasting** and no time-series model validation.
- **No derived signals** yet (no `market_signal` / `trajectory_signal` materialization from these rows in the importer).
- **No recommendation logic** and no RAG chunk generation in the import path.

Pre-import **inspection** remains mandatory: `scripts/inspect_ssb_jsonstat.py` previews and combined CSV are the structural benchmark before DB import approval.

---

# 3. Validation philosophy

## Principles

| Principle | Operational meaning |
|-----------|---------------------|
| **Correctness over volume** | Prefer blocking or quarantining a bad batch over accepting full row volume with silent defects. |
| **Explainability over optimization** | Every check should map to a human-readable reason (what broke, which table, which file). |
| **Reproducibility over speed** | Replays and checksums beat one-off manual fixes; speed targets are secondary to deterministic replay. |
| **Append-oriented validation** | Favor detecting duplicates and versioning conflicts over destructive overwrite of observations. |
| **Deterministic normalization expectations** | Same inputs + declared `normalization_version` / `transformation_version` → same flattened coordinates and values (modulo explicit importer bugfixes with version bumps). |

## Severity classes

| Class | Meaning |
|-------|---------|
| **Hard-fail** | Import or promotion to production must **stop** until resolved (or batch quarantined). |
| **Warning** | May proceed with explicit flags, capped downstream use, or shortened promotion path only if governance allows. |
| **Informational** | Logged for trend monitoring; does not block MVP import by itself. |

---

# 4. Dataset-level validation

## Checks

| Check | Description |
|-------|-------------|
| **Dataset existence** | Canonical statistical dataset row exists for `source_system` + `table_id` (e.g. SSB + `11615`). |
| **Dataset version existence** | Immutable version record exists for the exact raw snapshot when the schema requires it; version links observation rows to that snapshot. |
| **`table_id` correctness** | Stored `table_id` matches SSB table number; no cross-table bleed from file naming mistakes. |
| **Source file traceability** | `source_file` (raw filename) matches an artifact in controlled raw storage; checksum or path policy per ingestion doc. |
| **Metadata completeness** | Minimum metadata present for title/label and dimension discovery; absence of sidecar `*_metadata.json` should be **warning** or **hard-fail** per governance (importer currently warns). |
| **Confidence fields** | `confidence_category` and `confidence_score` within allowed enums/ranges; direct SSB cells default toward `verified_statistical` only when lineage is complete. |
| **Ingestion timestamps** | `ingestion_timestamp` (or DB default) consistent with batch run; distinguish from statistical period. |

## What should block import approval

- Missing or ambiguous **dataset identity** (`table_id`, slug, `external_id`).
- **No traceable raw file** for the batch (filename mismatch, missing object).
- **Structural parse failure** of JSON-stat2 (cannot unwrap `class: dataset`).
- **Confidence invalid** for the claimed tier (e.g. verified without lineage).
- **Critical lineage gap** when policy requires `dataset_version_id` (if schema mandates non-null version, empty version is **hard-fail**).

---

# 5. Dimension validation

## Checks

| Check | Description |
|-------|-------------|
| **Canonical dimension reuse** | Known dimensions (`Tid`, `Kjonn`, `Region`, `Yrke`, `NACE2007`, `UtdNivaa`, `Fagfelt`, `ContentsCode`, `Alder`, `HeltidDeltid`, …) map to stable canonical records without accidental duplicates from spelling drift. |
| **Duplicate dimensions** | No two active DB dimensions represent the same source `dimension` key for the same semantic scope without alias linkage. |
| **Missing labels** | Dimension-level label present where required for human review; missing should trigger warning or fail per table. |
| **Invalid slugs** | Internal slugs safe, unique, and stable (importer uses slug rules for dimensions/values). |
| **Deprecated dimensions** | Deprecated flagged; historical observations still reference correct id; no silent merge across incompatible semantics. |
| **Multilingual labels** | Source label preserved; secondary languages tracked without overwriting authoritative NO label where NO is MVP default. |
| **Hierarchy integrity** | If `parent_value_id` or hierarchy metadata used: no cycles, no dangling parents, totals consistent with documented rollup policy. |

## Examples by table (from normalization MVP)

- **11615:** `Tid`, `Region`, `Alder`, `Fagfelt`, `UtdNivaa`, `Kjonn`, metric dimension — verify expected set and cardinality vs preview.
- **12850:** adds **`NACE2007`** vs 11615; validate industry dimension not mistaken for occupation.
- **08417:** validate **`HeltidDeltid`** presence and codes vs preview.
- **09793:** validate **`Yrke`** + **`Alder`**; occupation semantics must not be assumed for tables without `Yrke`.

---

# 6. Dimension-value validation

## Checks

| Check | Description |
|-------|-------------|
| **Duplicate values** | Same `(dimension_id, value_code)` not inserted as conflicting rows with different semantics. |
| **Missing codes** | Every code appearing in `dimensions_json` / flattening exists in dimension value catalog for that dimension. |
| **Missing labels** | Label map complete or explicitly null with warning; “all labels missing” for a dimension is **hard-fail** for explainability. |
| **Incorrect total flags** | `is_total` (or equivalent) aligned with SSB “total” / “alle” / `TOT`-style codes per `TOTAL_*_HINTS` logic in importer; mis-flagged totals distort aggregates. |
| **Invalid parent-child** | Hierarchy edges match source metadata when present. |
| **Inconsistent labels for same code** | Same code must not flip labels across rows within one file without version narrative; cross-version label evolution allowed with audit. |
| **Orphan values** | Dimension values never referenced by any observation in the batch (informational) vs values referenced but missing from catalog (**hard-fail**). |

## Authority rule

**SSB source codes are authoritative.** Labels may evolve; codes define statistical identity. Normalization must not invent codes or silently remap without documented transformation rules and version bumps.

---

# 7. Observation validation

## Core invariant

**One row = one measured statistical fact** (one JSON-stat2 cell: one Cartesian coordinate + one value).

## Field-level checks (aligned with importer payload)

| Area | Check |
|------|--------|
| **Required fields present** | `statistical_dataset_id`, `table_id`, `source_file`, `value`, `dimensions_json`, ingestion lineage fields as required by schema. |
| **Valid numeric values** | `value` numeric where source is numeric; null handling matches policy (importer skips null cells with counted skips). |
| **Valid periods** | `period` extracted from time dimension; format consistent with table (e.g. calendar year vs quarter). |
| **Valid units** | `unit` populated when JSON-stat2 metric metadata provides it; unknown unit flagged **warning**, not silently guessed. |
| **`dimensions_json` completeness** | Keys cover full `id` order from dataset; codes match flattening index. |
| **`dimension_labels_json` completeness** | Parallel keys to codes; gaps traced to missing SSB labels. |
| **`dimension_value_ids` completeness** | List length matches number of dimensions with resolved FKs; missing ids when map lookup fails → **hard-fail** or quarantine (breaks normalized join path). |
| **`raw_observation_json` presence** | Minimum snapshot of value + codes + labels for replay/debug. |
| **Provenance fields** | `ingestion_batch_id`, `transformation_version`, `normalization_version` set; `source_id` when registry requires. |
| **Confidence fields** | Appropriate for direct measurement (`verified_statistical` + score 1.0 for MVP default is acceptable only if above checks pass). |

## Hard-fail candidates

- Cartesian / length mismatch (fewer or more values than product of category sizes) — caught at flatten time.
- Missing **`dimensions_json`** or **`raw_observation_json`** for stored rows.
- **`dimension_value_ids`** incomplete where schema requires full bridge.
- Wrong **`table_id`** or **`source_file`** for payload.

---

# 8. Observation consistency checks

## Within-table consistency

| Check | Description |
|-------|-------------|
| **Same dimension structure** | All rows from one file share identical dimension key sets (allowing null metric codes only where source allows). |
| **Same unit usage** | Per `ContentsCode` / metric code, unit stable; mixed units for same metric → **warning** or **fail**. |
| **Same period formatting** | Period strings comparable within file (no mixed `2024` vs `2024K1` without documented reason). |
| **No mixed semantics** | No rows from different logical tables in one batch (guarded by `table_id` + file pairing). |
| **Stable dimension ordering** | `metadata_json.dimension_ids` matches source `id` order; stable across importer versions unless `transformation_version` changes. |

## Table-specific notes

| Table | Consistency focus |
|-------|-------------------|
| **11615** | Large cardinality; spot-check region × field × education slices vs preview counts. |
| **12850** | `NACE2007` × `Fagfelt` combinations; industry/education cross-tabs sanity. |
| **08417** | Full-time vs part-time codes only in expected dimension. |
| **09793** | Occupation × age × gender; do not join occupation semantics to other tables without subset checks. |

---

# 9. Duplicate validation

## Checks

| Check | Description |
|-------|-------------|
| **Duplicate observations** | Same statistical identity (dataset + version + period + contents + dimension signature) inserted twice in one batch or across replays. |
| **Duplicate dataset versions** | Same checksum / file snapshot registered as two versions without policy. |
| **Duplicate dimension values** | Same `(dimension_id, value_code)` upsert path should be idempotent, not create parallel rows. |
| **Replay duplicates** | Re-running import with same raw file must not double-insert if idempotency policy applies; if append-only replay, new batch id must not duplicate prior **observation signatures** without explicit supersede flags. |

## Deterministic signatures

Define a signature from stable fields, for example: `table_id`, `source_file`, `period`, `contents_code`, ordered dimension codes, `normalization_version`. Signatures must be **deterministic** (sorted keys, canonical period string).

## Append-oriented philosophy

MVP prefers **detect and flag** over silent overwrite. Destructive overwrite of observations obscures audit trails and breaks explainability; avoid unless governed exception with full audit log.

---

# 10. Temporal validation

## Checks

| Check | Description |
|-------|-------------|
| **Valid periods** | Period in allowed set for table; parse known patterns (`YYYY`, quarterly if present). |
| **Period consistency** | `period_start` / `period_end` when populated align with `period` (importer may only fill for strict `YYYY`). |
| **`observed_at` correctness** | When set, reflects semantic “as of” for the statistic; may be null in MVP — document policy. |
| **Ingestion timestamps** | Always **processing time**, never confused with period. |
| **Stale handling** | `stale_after` when used must be ≥ ingestion for sensible freshness policies. |
| **Future dates** | Periods beyond publication window → **warning** (possible pre-release or labeling error). |
| **Deprecated periods** | Source-retired periods still in old files → allowed with version linkage; do not delete. |

## Three clocks (must remain distinguishable)

| Concept | Meaning |
|---------|---------|
| **Statistical time** | What the measure refers to (`period`, survey year, reference week). |
| **Ingestion time** | When the row was written to the normalized store. |
| **Recommendation time** | When product logic consumed signals (future); not set at import. |

---

# 11. Provenance validation

## Required elements (conceptual checklist)

- Source dataset (statistical dataset + `table_id`)
- Source file (`source_file` + storage path / checksum)
- Dataset version (when enabled)
- **Ingestion batch id** (importer: `ingestion_batch_id` from deterministic seed)
- **Normalization version** (`normalization_version`)
- **Transformation version** (`transformation_version`)
- Source dimension ids and codes (`dimensions_json`, `metadata_json.dimension_ids`)
- Source labels (`dimension_labels_json`, `raw_observation_json`)

## Why lineage completeness is mandatory

Without it, neither **explainability** (“this number came from…”) nor **reproducibility** (“re-run this version”) nor **scoring confidence** (`verified_statistical` requires citable evidence) can be defended. Lineage gaps should **block production promotion** even if rows exist.

---

# 12. Explainability validation

## Checks

| Check | Description |
|-------|-------------|
| **Source traceability** | From observation → table + file + batch + versions. |
| **Dimension explainability** | Codes + labels sufficient to reconstruct human sentence. |
| **Observation reproducibility** | Raw file + versions + transformation ids allow re-flatten to same coordinates. |
| **Confidence explainability** | Category matches evidence tier; no `verified_statistical` without full lineage. |
| **Normalization transparency** | `normalization_version` / `transformation_version` documented in release notes when changed. |

## Example narrative (target state for reviewers)

> This observation originates from **SSB table 11615**, **period 2024**, **region Oslo**, **education level UtdNivaa code X**, **source file `11615_….json`**, **ingestion batch `ssb-import-…`**, **normalization `ssb_norm_v1`**, **transformation `ssb_jsonstat2_flatten_v1`**.

---

# 13. Statistical sanity checks

## Checks (initially **review**, not auto-reject)

| Check | Description |
|-------|-------------|
| **Impossible negatives** | Counts and populations should not be negative unless source metric allows. |
| **Impossible percentages** | Values outside [0, 100] for percent metrics → review (unit confusion vs data error). |
| **Empty populations** | Zero denominators or empty slices where totals expected. |
| **Extreme outliers** | Sudden order-of-magnitude jumps vs adjacent period or vs preview benchmark. |
| **Missing totals** | Expected “total” row missing for a slice where SSB usually provides. |
| **Malformed distributions** | Subgroups do not sum to declared total within tolerance. |

## Policy

MVP treats most sanity failures as **manual review**: SSB can revise history; units differ; tables are large. Auto-rejection risks false negatives. Escalate to human with **pre-calculated deltas vs inspect script aggregates**.

---

# 14. Validation reporting

## Outputs to produce per import run

| Output | Content |
|--------|---------|
| **Pass/fail summary** | Count of hard-fails vs pass. |
| **Warning summary** | Grouped by category (labels, units, metadata missing, …). |
| **Row counts** | Per table, inserted vs skipped nulls vs source `value` length. |
| **Duplicate counts** | Signature collisions. |
| **Orphan counts** | Values without observations; observations without dimension values. |
| **Lineage completeness** | % rows with full required provenance fields. |
| **Validation timestamps** | Start/end UTC, validator version / git ref optional. |

## Human review expectations

A named reviewer signs off when **hard-fail = 0**, **warnings** are within MVP thresholds (section 16), and spot-checks against `ssb_preview` CSVs pass. Store signoff id + date with batch metadata (process definition, not SQL here).

---

# 15. Manual review triggers

## Situations requiring human review

| Trigger | Action |
|---------|--------|
| **Unknown dimensions** | New `id` entry not in prior previews — mapping decision. |
| **Unknown values** | New codes without labels or without codebook entry. |
| **Inconsistent labels** | Same code, multiple labels within snapshot. |
| **Duplicate conflicts** | Signature collision with different `value`. |
| **Malformed metadata** | Missing `dimension`, broken `category.index`. |
| **Unexplained outliers** | Section 13 flags beyond threshold. |
| **Hierarchy conflicts** | Parent/child contradicts SSB documentation. |
| **Replay inconsistencies** | Same file + versions yields different row count or checksum vs benchmark. |

## Escalation path (operational)

1. **Importer operator** collects report + raw file name + batch id.  
2. **Data steward** decides dimension/value governance (reuse vs new vs hold).  
3. **Product/analytics owner** approves promotion to production consumer if warnings acceptable.  
4. **Blocked** items remain in quarantine until resolved or version superseded.

---

# 16. Quality metrics

## Operational metrics

| Metric | Definition | MVP acceptable direction |
|--------|------------|---------------------------|
| **Normalization success rate** | Successful tables / attempted tables | **≥ 4/4** for full MVP run, or explicit skip documented |
| **Lineage completeness** | Rows with all required provenance fields | **≥ 99%** before production; **100%** if `dataset_version_id` mandatory |
| **Duplicate rate** | Duplicate signatures / inserted rows | **Near 0**; any non-zero requires investigation |
| **Missing-label rate** | Observations with any missing label for a non-null code | **Low**; table-specific threshold, trending down |
| **Orphan rate** | Observations referencing missing dimension values | **0** (hard requirement for FK-backed paths) |
| **Replay consistency** | Bit-identical or row-count–identical replay vs fixture | **Match** benchmark fixture after any code change |
| **Validation failure rate** | Hard-fails per run | **0** for production promotion |
| **Dimension reuse rate** | Reused canonical dimensions / total | **Stable or increasing** after initial catalog build |

## MVP threshold stance

Early waves prioritize **trend stability** and **zero hard-fail** over perfect secondary labels. Secondary language gaps may remain **warning** if NO labels are complete.

---

# 17. Approval criteria

| Outcome | When |
|---------|------|
| **Import approved** | Hard-fail = 0; lineage completeness meets section 16; duplicates explained or zero; reviewer signoff; preview parity checks passed for sampled slices. |
| **Import quarantined** | Hard-fail > 0, or lineage completeness below floor, or unexplained duplicate/value collisions. |
| **Replay required** | Normalization or transformation version bump; raw file replaced; nondeterminism detected; prior batch incorrectly attributed. |
| **Batch rejected** | Unparseable file, wrong table in file, catastrophic count mismatch vs `inspect_ssb_jsonstat.py` totals for same file. |

## Hard-fail vs warning

- **Hard-fail** blocks **approval** and production downstream reads.  
- **Warning** blocks approval only if above cumulative thresholds or if governance lists the warning type as blocking (e.g. missing metadata file for 11615).

---

# 18. MVP limitations

- **Only four SSB tables** (`11615`, `12850`, `08417`, `09793`).
- **Limited periods** per raw corpus; not exhaustive historical backfill.
- **No realtime** streaming from SSB; file-based snapshots only.
- **No NAV** labor-market feed data in this validation scope.
- **No forecasting** or nowcast validation.
- **No automatic semantic validation** (e.g. “this occupation must imply this education”) — would be interpretive, not MVP.
- **No causal analysis** or econometric identification checks.
- **No benchmark engine** (percentile vs cohort) in scope.

---

# 19. Future evolution

- **Automated validation** pipeline: CI job comparing import output to frozen preview hashes per table.
- **Statistical anomaly detection** with tunable thresholds after enough history exists.
- **Semantic validation** rules (governed, versioned) for cross-field plausibility.
- **Benchmark validation** once cohort baselines exist.
- **Lineage graphing** (dataset → version → file → batch → observation).
- **Automated replay verification** on every importer PR.
- **Cross-source consistency** (SSB vs NAV vs Studiebarometeret) with explicit conflict rules.

---

# 20. Open questions

1. **Observation uniqueness strategy** — exact unique constraint fields vs soft signature index; how replays interact.  
2. **Outlier thresholds** — per-metric vs global; seasonal adjustment awareness.  
3. **Lineage depth** — how much raw JSON to store vs hash-only external store.  
4. **Hierarchy governance** — who approves parent edits when SSB revises trees.  
5. **Replay guarantees** — idempotent upsert vs append-only with invalidation flags.  
6. **Statistical anomaly policy** — when anomalies auto-block vs only alert.  
7. **Dimension versioning** — cadence for breaking changes in canonical dimensions.  
8. **Aggregation persistence** — which rollups may be precomputed without losing observation-first audit trail.

---

# Summary

**Philosophy:** Treat imported SSB observations as **evidence artifacts**, not interchangeable numbers. Validation exists to ensure **statistical identity**, **temporal honesty**, and **full provenance** before any layer assigns **verified_statistical** trust.

**Operational architecture:** **Inspect** (`inspect_ssb_jsonstat.py` + preview CSVs) → **Import** (`import_ssb_observations.py` with explicit versions + batch id) → **Validate** (checklist sections 4–13) → **Report** (section 14) → **Approve / quarantine / replay** (sections 15–17). Hard-fails protect production; warnings feed governance; explainability and lineage are **mandatory**, not optional columns.

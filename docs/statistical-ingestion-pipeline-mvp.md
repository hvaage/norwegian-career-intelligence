# Statistical ingestion pipeline MVP

**Specification only:** no SQL, no import scripts, no orchestration code, no frontend logic.

This document defines the first operational ingestion pipeline for normalized public statistical data in the Norwegian Career Intelligence Dataset and **sokr.online**.

It defines:

- how raw statistical datasets become normalized observations
- how dimensions and dimension values are managed
- how provenance and lineage are preserved
- how replay/re-import works
- how validation and quality control operate

**Design posture:** MVP, explainability-first, provenance-first, append-oriented, human-debuggable.

**Related:** `docs/statistical-observation-schema.md`, `docs/public-statistical-ingestion-and-normalization-plan.md`, `docs/ssb-normalization-mvp.md`, `docs/minimum-viable-intelligence-schema.md`, `docs/scoring-and-signal-model.md`, `sql/001_intelligence_foundation.sql`, `sql/002_statistical_observations.sql`

---

## 1. Purpose of the ingestion pipeline

Ingestion is intentionally separated from signal generation.

### Why separation matters

- **Ingestion layer** handles source truth, dimensional structure, lineage, and validation.
- **Signal layer** (later) interprets normalized observations into market/risk/trajectory intelligence.

If interpretation starts before normalization, confidence and explainability become unreliable.

### Why provenance preservation is mandatory

Every observation must trace to:

- exact source dataset,
- exact dataset version,
- exact source file,
- exact dimension codes and labels used at ingest time.

### Why replayability matters

Public datasets revise over time. Replay support is required for:

- reproducibility,
- correction workflows,
- version comparisons,
- audit and debugging.

### End-to-end sequencing

`raw statistics`  
`-> normalized observations`  
`-> verified statistical signals`  
`-> intelligence/recommendations`

---

## 2. Pipeline architecture overview

### MVP flow

`raw files`  
`-> dataset registration`  
`-> dimension extraction`  
`-> dimension-value extraction`  
`-> observation flattening`  
`-> normalization`  
`-> validation`  
`-> insert`  
`-> quality checks`

### Architecture properties

| Property | MVP interpretation |
|---|---|
| Append-oriented | Avoid destructive updates to statistical observations |
| Immutable raw | Raw assets are never rewritten |
| Deterministic normalization | Same input + same normalization version => same output rows |
| Human-debuggable | Every step emits reviewable metadata and logs |

### Dataset formats in MVP

- SSB JSON-stat2 (primary now)
- Studiebarometeret XLSX (next, under same pipeline concepts)

---

## 3. Ingest workflow

### Step-by-step workflow

| Step | Inputs | Outputs | Common failure points | Rollback expectation |
|---|---|---|---|---|
| 1. Raw file discovery | `data/raw/...` assets | Candidate file list | Missing files, wrong naming | No DB writes yet |
| 2. Dataset identification | file + metadata | Canonical dataset key (`table_id`, source) | Ambiguous identity | Hold file in review queue |
| 3. Dataset version creation | dataset + file checksum | New immutable version reference | Duplicate version collision | Safe no-op or duplicate flag |
| 4. Metadata extraction | JSON/XLSX metadata sheets | Parsed dimensions + period hints | Unexpected shape | Stop file or mark partial |
| 5. Dimension extraction | parsed metadata | candidate dimensions | Missing/renamed dimension keys | Escalate and hold |
| 6. Dimension value extraction | category index/labels | candidate value rows | Missing labels, duplicate code conflicts | Quarantine value group |
| 7. Observation flattening | dimensions + values array | row-level observations | Cartesian mismatch, value length mismatch | Stop file hard-fail |
| 8. Normalization | flattened rows | canonicalized row payloads | Code/label drift | Warning or fail based on severity |
| 9. Validation | normalized payloads | pass/fail batches | period/unit/confidence violations | Failed batch quarantined |
| 10. Insert batching | validated rows | persisted observations | DB constraints, timeouts | Batch-level retry |
| 11. Post-ingest quality checks | inserted rows | quality report | row-count mismatch | mark ingest degraded |
| 12. Audit logging | full ingest context | immutable audit record | missing trace IDs | mark ingest invalid |

---

## 4. Dataset registration strategy

### Objects

- `statistical_datasets`
- `dataset_versions` (from foundation layer)

### Rules

| Rule | Description |
|---|---|
| Canonical dataset identity | Stable key from source + `table_id` + logical dataset slug |
| Version immutability | New file/checksum -> new version, never overwrite existing |
| Source traceability | Keep links to `sources`, `datasets`, raw file location |
| Ingestion batch linking | Every ingest run gets a batch identifier for audit |
| Metadata preservation | Store source metadata snapshot for replay/debug |

### Examples

- **SSB 11615** -> canonical statistical dataset, new version per fetched file snapshot.
- **Studiebarometeret 2025** -> one canonical dataset family, versioned by release/extract and codebook revision.

---

## 5. Dimension upsert strategy

### Detection and reuse

1. Detect dimensions from source metadata (`id`, `dimension`, codebook headers).
2. Match existing canonical dimensions by strong keys (`dimension_code`, source system, aliases).
3. Reuse canonical dimension when match confidence is high.
4. Create new dimension only when no safe canonical match exists.

### Priority examples

- `Tid`
- `Region`
- `UtdNivaa`
- `Kjonn`
- `Fagfelt`
- `NACE2007`
- `Yrke`

### Operational rules

| Topic | MVP rule |
|---|---|
| Multilingual labels | Keep source label; add translated/alias labels without overwriting source |
| Deprecated dimensions | Mark deprecated, keep historical use |
| Hierarchy support | Flag dimensions with hierarchy potential; do not force hierarchy if absent |
| Normalization boundary | Keep source code semantics; avoid early semantic merging across incompatible datasets |

---

## 6. Dimension-value upsert strategy

Dimension values are reusable but source codes are authoritative.

### Examples

- `Oslo`
- `Kvinner`
- `Menn`
- `2024`
- `20-66`
- `Alle yrker`

### Rules

| Rule | Description |
|---|---|
| Insert vs update | Insert new `(dimension_id, value_code)`; update labels/metadata only with non-destructive version-aware rules |
| Stable code authority | Code identity is primary; labels can evolve |
| Parent-child support | Use `parent_value_id` where hierarchy is explicit |
| Total rows | Mark `is_total=true` for aggregate categories |
| Deprecated values | Keep historical values; mark deprecated, do not delete |
| Aliases | Track aliases in metadata/alias fields for matching and retrieval |

---

## 7. Observation insert strategy

### Core principle

**One row = one measured statistical fact.**

### Insert strategy

- Flatten source payload deterministically.
- Preserve `dimensions_json` and `dimension_labels_json` from source context.
- Include provenance keys (`statistical_dataset_id`, `dataset_version_id`, `source_file`, `table_id`).
- Insert append-style rows with `created_at` and batch/version context.

### Why remain close to source truth

Observation rows should preserve source semantics so later interpretations remain auditable.

### Table examples

| Table | Typical observation shape |
|---|---|
| `11615` | Region + education level + field + demographic + period + metric value |
| `12850` | Industry + education + field + demographic + period + metric value |
| `08417` | Employment type + gender + education + period + value |
| `09793` | Occupation + gender + age + period + value |

---

## 8. Duplicate prevention strategy

### Dataset-level duplicate prevention

- Unique dataset identity by canonical dataset key (`source_system + table_id + dataset scope`).
- Version-level duplicate checks using checksum and source file signature.

### Observation-level duplicate prevention

Use deterministic signature candidates:

- `statistical_dataset_id`
- `dataset_version_id`
- `period`
- `contents_code`
- normalized dimension signature hash

### Principles

| Principle | MVP approach |
|---|---|
| Detect, do not overwrite | Flag duplicates and preserve historical rows |
| Deterministic signatures | Same source row should hash the same way |
| Batch safety | Prevent same batch from re-inserting same observation set |

Destructive overwrite should be avoided in MVP.

---

## 9. Dataset version handling

### Version lifecycle

| Event | Behavior |
|---|---|
| New source snapshot | Create new immutable dataset version |
| Replay same version | Deterministic reprocess; compare outputs |
| Revised source structure | New version + classification/mapping review |
| Stale/deprecated source version | Keep for history, mark lifecycle status |

### Philosophy

- Immutable versioning
- No silent mutation
- Explicit version comparisons for quality and lineage

---

## 10. Lineage strategy

### Required lineage elements

- source dataset
- source file
- dataset version
- extraction batch
- normalization version
- transformation version
- source dimensions
- source labels

### Why this depth is required

| Goal | Lineage value |
|---|---|
| Explainability | Show exact origin for each observation and derived claim |
| Debugging | Reproduce failures and mapping mismatches |
| Reproducibility | Re-run same version and verify deterministic output |
| Auditability | Prove non-destructive, traceable processing history |

---

## 11. Batching strategy

Large tables (especially `11615`) require chunked processing.

### MVP batching guidance

| Concern | Strategy |
|---|---|
| Insert batch size | Use moderate chunk sizes (e.g., 2k-10k rows) tuned by DB limits |
| Memory handling | Stream flattening output or chunk in-memory lists |
| Large dataset handling | Chunk by period and/or dimension slices if needed |
| Retry behavior | Retry failed batch with capped attempts |
| Resumability | Resume from last successful batch marker |
| Progress tracking | Persist/log rows processed, rows inserted, failures per batch |

`11615` is a benchmark for stress-testing chunk behavior and progress tracking.

---

## 12. Error handling strategy

### Error classes

- malformed JSON
- invalid metadata shape
- dimension conflicts
- missing labels
- unknown codes
- duplicate collisions
- batch failures

### Handling model

| Severity | Behavior |
|---|---|
| Hard structural failures | Fail-fast and block ingestion for that file/version |
| Recoverable quality issues | Continue with warning + quarantine/flagged rows |
| DB batch failures | Retry at batch scope; escalate if repeated |

### Operational requirements

- structured logging with batch + dataset version IDs,
- quarantine strategy for invalid rows/files,
- explicit review queues for conflicts needing human judgment.

---

## 13. Replay and re-import strategy

### Replay scenarios

1. Full dataset replay
2. Single version replay
3. Single failed batch replay

### Replay principles

- deterministic normalization (same input -> same normalized output),
- append-only preference for observation persistence,
- rollback by version/batch invalidation flags rather than destructive delete where possible.

### Safety and idempotency

- Replay should not silently duplicate rows.
- Replay runs must be auditable and attributable to a replay reason.

---

## 14. Normalization checkpoints

### Mandatory checkpoints

| Checkpoint | What is checked | Blocks ingest? |
|---|---|---|
| Raw validation | file integrity, parseability, basic schema presence | Yes |
| Metadata validation | dimensions and category structures present | Yes |
| Dimension validation | canonical match/create decisions sane | Yes for unresolved hard conflicts |
| Observation validation | value alignment, period extraction, dimension coordinates | Yes |
| Post-insert validation | row count parity, lineage completeness, key quality metrics | Yes for critical mismatch |

---

## 15. Validation rules

### Rule classes

| Rule area | Hard-fail | Warning |
|---|---|---|
| Periods | missing/invalid period format when required | non-standard but parseable period labels |
| Units | invalid structure for metric-unit pair | missing unit where source omits unit |
| Dimensions | missing mandatory dimension metadata | unknown optional dimension alias |
| Labels | missing all labels for a value code | missing secondary language label |
| Missing values | impossible numeric shape mismatch | sparse null values allowed by source |
| Confidence | invalid confidence category or score bounds | low confidence expected for non-verified mappings |
| Source consistency | dataset/version/source mismatch | minor metadata drift |
| Dataset consistency | row count/cartesian mismatch | label drift without code change |

---

## 16. Quality metrics

### Operational metrics

| Metric | Definition | MVP target direction |
|---|---|---|
| normalization success rate | successful ingested batches / total batches | high |
| duplicate rate | duplicate observations detected per ingest | low and decreasing |
| missing-label rate | observations with unresolved labels | low |
| dimension reuse rate | reused dimensions vs newly created dimensions | stable/high after initial waves |
| orphan observations | observations missing required lineage links | near-zero |
| replay consistency | same input replay output parity | near-perfect |
| lineage completeness | rows with full lineage fields populated | very high |
| validation failure rate | failed checkpoint count per ingest | low |

### Threshold stance

MVP thresholds are directional and reviewed per ingest cycle; trend stability matters more than absolute perfection in early waves.

---

## 17. Explainability strategy

Ingestion decisions must remain explainable at row, batch, and dataset level.

### Explainability examples

- why a dimension was reused: canonical match on code + alias evidence,
- why a value became deprecated: source removed code in new version,
- why a batch failed: cartesian/value mismatch or validation error,
- why an observation was quarantined: missing mandatory period or invalid dimension signature.

### Explainability requirements

- provenance visibility,
- normalization transparency,
- auditable decision logs,
- reproducible transformation context.

---

## 18. MVP limitations

Current limits:

- only 4 SSB tables,
- sample periods only,
- no realtime/streaming ingest,
- no automatic taxonomy mapping at scale,
- no automated statistical interpretation,
- no forecasting.

---

## 19. Future evolution

Likely evolution path:

- NAV statistics ingestion,
- Studiebarometeret structured ingestion under same model,
- OECD/Eurostat expansions,
- automated lineage enrichment,
- statistical benchmarking layers,
- graph relationships for dimensions/mappings,
- embeddings/vector retrieval for statistical evidence retrieval,
- transition modeling extensions.

Humans remain authoritative for critical normalization and confidence decisions in early phases.

---

## 20. Open questions

Unresolved decisions:

1. Observation uniqueness strategy (signature fields and constraints).  
2. Lineage depth vs storage overhead.  
3. Dimension governance ownership and SLA.  
4. Batch replay policy granularity and retention.  
5. Storage optimization and compaction strategy.  
6. Partitioning strategy for large observation volumes.  
7. Aggregation persistence vs compute-on-read boundaries.  
8. Normalization versioning lifecycle policy.  
9. Dimension hierarchy policy when source hierarchies conflict.

---

## Summary

The proposed MVP ingestion architecture is a deterministic, append-oriented pipeline that transforms raw public statistics into normalized, lineage-complete observations before any interpretive signal generation.

It is built on:

- immutable raw inputs,
- canonical dataset/version registration,
- reusable dimensions and values,
- robust flattening and validation checkpoints,
- batch-safe inserts and replay support,
- provenance-first explainability.

This creates a reliable statistical foundation for later verified statistical signals, gap/overlap logic, and recommendation systems without sacrificing auditability or semantic integrity.


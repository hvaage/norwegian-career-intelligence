# Statistical observation schema

**Specification only:** no SQL migrations, no import scripts, no Supabase writes.  
This document defines the first operational **statistical observation foundation layer** for the Norwegian Career Intelligence Dataset and **sokr.online**.

It is the bridge between:

- raw public statistical datasets
- normalized observations
- verified statistical signals (later)
- gap/overlap/recommendation logic (later)

This is **not** signal generation yet.  
This is the normalized statistical foundation layer.

**Related:** `docs/public-statistical-ingestion-and-normalization-plan.md`, `docs/ssb-normalization-mvp.md`, `docs/minimum-viable-intelligence-schema.md`, `docs/scoring-and-signal-model.md`, `docs/career-taxonomy-design.md`

---

## 1. Purpose of the statistical observation layer

### Why observations are the canonical statistical unit

A public statistical table is a container. A dimension is a structure. A signal is an interpretation.  
The canonical unit in the middle is the **observation**: one measured fact tied to a period and a set of dimension values.

### Distinction of layers

| Layer | Meaning |
|---|---|
| Raw datasets | Original source payloads (JSON-stat2, XLSX, PDF, metadata) |
| Dimensions | Reusable categorical structures (e.g., region, age, industry) |
| Observations | Atomic measured facts (`value` + period + dimension coordinates) |
| Derived signals | Interpreted outputs over observations (trends, risk, transitions, etc.) |

### Why this layer must exist first

Without a stable observation layer, later outputs become opaque and fragile:

- verified statistical signals cannot be audited,
- recommendations cannot cite exact measured facts,
- forecasting pipelines cannot reproduce baselines,
- trajectory analysis cannot be period-stable,
- benchmarking becomes inconsistent,
- RAG cannot retrieve citable statistical evidence safely.

### One observation = one measured statistical fact

Examples:

- employment count
- salary level
- employment share
- unemployment rate
- transition probability

---

## 2. Shared observation model

The MVP shared model is validated by the four tested SSB tables (`11615`, `12850`, `08417`, `09793`) through the flattening inspection workflow.

### Required observation fields

| Field | Purpose |
|---|---|
| `observation_id` | Stable internal identity for one observation record |
| `dataset_id` | Logical dataset anchor |
| `dataset_version_id` | Exact source version provenance |
| `table_id` | Source table identifier (e.g., SSB table number) |
| `source_name` | Human-readable source (e.g., SSB) |
| `period` | Statistical reporting period (e.g., 2024, 2025K4) |
| `value` | Measured numerical value |
| `unit` | Unit where available (e.g., persons, 1 000 persons, percent) |
| `dimensions_json` | Dimension code coordinates for this observation |
| `dimension_labels_json` | Human labels aligned to dimension codes |
| `metadata_json` | Additional context (notes, flags, quality metadata) |
| `confidence_category` | Observation confidence class (typically `verified_statistical` for direct source values) |
| `observed_at` | When the statistical fact applies or is represented |
| `valid_from` | Validity start for interpretation/use |
| `valid_to` | Validity end (if superseded/deprecated) |
| `ingestion_timestamp` | When this normalized row was generated |

### Data philosophy

- **Immutable observation-first**: treat normalized observation rows as append-friendly records.  
- **Append-only preference**: revisions should add new rows/version references, not overwrite old facts.  
- **Provenance preservation**: every observation must link back to exact dataset version and dimension coordinates.

---

## 3. Statistical dataset model

Define logical dataset objects and immutable versions:

- `statistical_datasets`
- `statistical_dataset_versions`

### Core principles

| Principle | Description |
|---|---|
| One dataset, many versions | One logical dataset may publish repeated periods/revisions |
| Version immutability | Metadata and payload references for a version should not be rewritten |
| Source traceability | Every version links to source + fetch metadata + raw file pointers |
| Dataset classification | `statistical_dataset`, `metadata_dataset`, `codebook`, etc. |

### Scope examples

- **SSB** (current MVP source)
- **Studiebarometeret** (2025 files/codebook/questionnaire)
- **Future NAV statistics** (aggregated/statistical endpoints if added)
- **Future OECD/Eurostat** (cross-country but not in MVP)

---

## 4. Dimension model

Define reusable dimension structures:

- `statistical_dimensions`
- `statistical_dimension_values`

Dimensions are semantic structures reused across datasets where possible.

### Typical dimensions

| Dimension | Examples |
|---|---|
| Education level | `UtdNivaa`, degree level |
| Occupation | `Yrke`, occupation group |
| Industry | `NACE2007`, sector groups |
| Region | national/municipality/county codes |
| Gender | `Kjonn` |
| Age | `Alder` groups |
| Field of study | `Fagfelt` |
| Employment type | full-time/part-time |
| Sector | public/private where available |

### Dimension requirements

- support hierarchy (parent-child rollups),
- preserve source code systems,
- preserve original labels,
- allow aliases and multilingual labels,
- support deprecated values without deletion.

---

## 5. Observation structure

### Representation

One observation row combines:

1. source identity (`dataset_version_id`, `table_id`, `source_file`),
2. dimension coordinates (`dimensions_json`),
3. dimension labels (`dimension_labels_json`),
4. measured value (`value`, `unit`),
5. temporal context (`period`, `observed_at`, validity).

### Why both normalized and partially denormalized dimensions

| Storage form | Why needed in MVP |
|---|---|
| Normalized dimensions | Reuse, consistent joins, taxonomy mapping foundation |
| JSON denormalized coordinates | Fast debugging, explainability, flexible ingestion of heterogeneous tables, easier RAG citation context |

This mixed strategy is intentional for MVP quality and inspection speed.

### Table examples

- `11615`: region + education + field + demographics + metric + time
- `12850`: industry + education + field + region + demographics + metric + time
- `08417`: employment type + gender + education + metric + time
- `09793`: occupation + gender + age + metric + time

---

## 6. Temporal strategy

### Core fields

| Field | Meaning |
|---|---|
| `period` | Statistical period in source taxonomy |
| `observed_at` | Observation context time marker |
| `valid_from` | Start of validity for normalized interpretation |
| `valid_to` | End of validity (revision/deprecation) |
| `ingestion_timestamp` | Time of processing into normalized layer |
| `stale_after` | Optional freshness threshold for downstream use |

### Temporal distinctions

Statistical time is not the same as operational time:

- **statistical time**: when the measured fact belongs (period/year/quarter),
- **ingestion time**: when we processed the file,
- **extraction time**: when interpreted signals are generated later,
- **recommendation time**: when product logic consumed derived outputs.

All four must remain distinguishable.

### Revisions and reproducibility

- New source revisions should create new version-linked observation rows.
- Old observations remain queryable for historical reproducibility.
- Classification changes should not overwrite historical semantics.

---

## 7. Provenance strategy

Each observation requires provenance to:

- source dataset
- dataset version
- source file
- original dimension codes
- original labels
- transformation version
- extraction batch
- normalization version

### Why

| Goal | Provenance role |
|---|---|
| Explainability | Show exact source for any statistical fact |
| Auditability | Reconstruct transformations and reviewer decisions |
| Reproducibility | Re-run normalization and compare outputs |

---

## 8. Statistical confidence strategy

Direct SSB observation values should generally be categorized as:

- `verified_statistical`

But separate confidence must exist for:

- mappings
- interpretations
- aggregations
- derived trend logic

### Confidence layers

| Layer | Meaning |
|---|---|
| Observation confidence | Trust in direct measured fact from authoritative source |
| Mapping confidence | Trust in dimensional/taxonomy crosswalk |
| Derived confidence | Trust in interpreted outputs over observations |

### Guardrails

- No hallucinated statistics.
- No inferred values labeled as measured observations.
- No automatic inheritance of `verified_statistical` from raw value to downstream interpretation.

---

## 9. Mapping strategy

Observations later map to:

- role taxonomy
- industry taxonomy
- competency taxonomy
- trajectory taxonomy
- recommendation engine inputs

### Example mappings

| Mapping direction | Example |
|---|---|
| education -> role families | `UtdNivaa` + `Fagfelt` informs role-family relevance distributions |
| occupation -> trajectories | `Yrke` groups linked to progression paths |
| industry -> market demand | `NACE2007` used for structural demand/risk context |
| demographics -> risk signals | age/gender/region disparities as risk context signals |

Important: mappings are **interpretive layers**, not observations.

---

## 10. Statistical signal bridge

Normalized observations can later generate:

- `verified_statistical` signals
- `market_signal`
- `trajectory_signal`
- `risk_signal`
- `transition_signal`

### Sequencing rule

1. Observations first  
2. Signals second

### Example bridges

| Observation pattern | Potential later signal |
|---|---|
| unemployment increases over periods | risk signal |
| declining role participation share | market/trajectory signal |
| industry concentration by education | market signal |
| persistent gender imbalance in occupation group | risk/transition signal |
| measured transition rates across categories | transition signal |

No bridge should skip observation-level provenance.

---

## 11. Storage strategy

### Raw storage (`raw/`)

- exact source files
- immutable snapshots
- metadata responses

### Normalized storage (`normalized/` or database normalized layer)

- normalized dimensions
- normalized observations
- mapping stubs/references

### Processed storage (`processed/`)

- inspection previews
- temporary flattening outputs
- QA extracts

### Database layer

- persistent normalized observation foundation
- versioned dataset references
- dimension/value records

### Future vector layer

- not part of MVP
- later retrieval-indexed derived summaries and cited evidence chunks

---

## 12. Explainability strategy

Statistical facts must remain explainable end-to-end.

Example explanation:

“This recommendation is partially based on:

- SSB table 11615
- period 2024
- region Oslo
- education field engineering
- employment share increase +12%”

### Required explainability elements

- source visibility (table + dataset version),
- confidence visibility (observation vs derived),
- traceability (dimension coordinates + transformation references),
- reproducibility (rebuild from same raw snapshot).

---

## 13. MVP limitations

Current limits:

- only 4 SSB tables,
- only sample periods currently inspected (2023–2025 snapshots in local raw set),
- no realtime updates,
- no forecasting,
- no automated mapping at scale,
- no benchmark engine,
- no causal inference.

---

## 14. Future evolution

Likely expansion path:

- NAV statistics integration,
- OECD/Eurostat extension,
- forecasting layers,
- embeddings-aware retrieval,
- graph relationships for dimensions/mappings,
- statistical benchmarking,
- transition prediction,
- labor-market simulation.

These are explicitly outside current MVP.

---

## 15. Open questions

Unresolved implementation decisions:

1. Observation granularity (atomic cell only vs pre-aggregated variants).  
2. Partial denormalization depth in persisted model.  
3. Hierarchy strategy (materialized paths vs parent-child only).  
4. Revision handling policy (supersede flags vs pure append).  
5. Derived metric persistence (store or compute-on-read).  
6. Aggregation strategy boundaries (what can be precomputed safely).  
7. Statistical lineage storage shape (JSON vs normalized lineage tables).  
8. Mapping governance ownership and approval workflow.  
9. Dimension versioning cadence and compatibility policy.

---

## Summary

The proposed statistical observation architecture establishes observations as the canonical statistical fact layer between raw public data and later intelligence logic.

It is built around:

- immutable, version-linked observations,
- reusable but evolution-friendly dimensions,
- strict provenance and temporal integrity,
- confidence separation between measured facts and interpretation,
- controlled bridge to later signals, gaps, overlaps, and recommendations.

This creates a reliable foundation for explainable labor-market intelligence before scaling to forecasting, automation, and advanced retrieval layers.


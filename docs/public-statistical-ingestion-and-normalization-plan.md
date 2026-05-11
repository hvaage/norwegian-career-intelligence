# Public statistical ingestion and normalization plan

**Specification only:** no SQL migrations, no import scripts, no frontend, no orchestration code.

This document defines the ingestion and normalization strategy for public statistical datasets used in the Norwegian Career Intelligence Dataset and **sokr.online**.

Primary focus:

- SSB datasets
- Studiebarometeret 2025

This plan describes how public statistical data becomes:

- structured intelligence
- explainable signals
- normalized dimensions
- comparable observations
- traceable evidence
- future-ready labor market intelligence

**Related:** `docs/education-demand-intelligence-design.md`, `docs/career-taxonomy-design.md`, `docs/scoring-and-signal-model.md`, `docs/minimum-viable-intelligence-schema.md`, `docs/first-intelligence-extraction-mvp.md`

---

## 1. Purpose

The statistical ingestion layer transforms public datasets into reusable intelligence components for:

- labor market analysis
- candidate guidance
- role trajectory analysis
- market intelligence
- gap analysis
- overlap analysis
- recommendation systems
- RAG retrieval

The ingestion layer preserves:

- provenance
- explainability
- temporal integrity
- statistical meaning
- dataset lineage

The system distinguishes between:

- raw statistics
- normalized dimensions
- interpreted signals
- inferred intelligence

Public datasets are treated as:

- authoritative inputs
- not final truth
- not direct recommendations

Interpretation always requires separate confidence handling.

---

## 2. Initial scope (MVP)

### Included datasets

#### SSB

- `11615`
- `12850`
- `08417`
- `09793`

#### Studiebarometeret

- 2025 program dataset
- 2025 codebook
- 2025 questionnaire PDF

### Excluded from MVP

- Full historical ingestion
- Real-time updates
- Forecasting
- Automated trend prediction
- Full NAV ingestion
- Full educational institution ingestion
- Automated inference pipelines
- Embedding pipelines
- Vector retrieval
- Cross-country datasets

---

## 3. Raw layer strategy

The raw layer stores original files exactly as received.  
No normalization occurs in this layer.

Purpose:

- reproducibility
- auditability
- explainability
- rollback capability
- future reprocessing

### Raw file types

| Type | Examples |
|---|---|
| JSON-stat2 | SSB API responses |
| XLSX | Studiebarometeret program files |
| PDF | Questionnaires and methodology documents |
| Metadata JSON | API metadata responses |
| Codebooks | Variable mappings and labels |

---

## 4. Raw storage structure

### Recommended structure

```text
data/raw/
  ssb/
  studiebarometeret/
```

Example files:

- `data/raw/ssb/11615_20260511-101619.json`
- `data/raw/ssb/11615_metadata.json`
- `data/raw/studiebarometeret/programfil_SB2025_portal.xlsx`

---

## 5. Raw layer requirements

Each raw asset must preserve:

| Requirement | Purpose |
|---|---|
| original filename | traceability |
| source URL | provenance |
| fetch timestamp | temporal tracking |
| dataset identifier | reproducibility |
| checksum/hash | integrity |
| source classification | routing |
| version label | change tracking |
| metadata snapshot | future reprocessing |

No raw files should be overwritten.  
All updates become new versions.

---

## 6. Dataset classification model

Datasets must be classified before normalization.

### Dataset types

| Dataset type | Description |
|---|---|
| `statistical_dataset` | numerical observations |
| `metadata_dataset` | dataset structure information |
| `codebook` | variable/value explanations |
| `survey_dataset` | survey-based responses |
| `dimensional_dataset` | dimensions/categories/hierarchies |
| `lookup_dataset` | mappings/reference values |

---

## 7. Statistical dataset characteristics (`statistical_dataset`)

Examples:

- SSB `11615`
- SSB `08417`

Characteristics:

- dimensional
- temporal
- quantitative
- aggregatable
- authoritative

Primary output:

- normalized observations
- verified statistical signals

---

## 8. Metadata dataset characteristics (`metadata_dataset`)

Examples:

- SSB metadata endpoints
- dimension metadata
- variable metadata

Purpose:

- dimension interpretation
- normalization guidance
- explainability
- temporal interpretation

---

## 9. Codebook strategy (`codebook`)

Examples:

- Studiebarometeret codebook
- survey variable mappings

Purpose:

- variable understanding
- categorical interpretation
- semantic normalization
- signal interpretation

Codebooks are critical for:

- confidence assignment
- explainability
- correct mappings

---

## 10. Normalization strategy

Public datasets pass through a staged normalization flow:

`raw JSON-stat2/XLSX/PDF`  
`→ parsed dimensions`  
`→ canonical dimensions`  
`→ normalized observations`  
`→ statistical signals`  
`→ derived intelligence`

---

## 11. Canonical normalization principles

Normalization must:

- preserve original labels
- preserve original values
- preserve dimension hierarchy
- preserve statistical meaning
- avoid destructive transformations

Normalized layers may:

- add aliases
- add taxonomy mappings
- add canonical labels
- add translated labels

But must:

- never remove original meaning

---

## 12. Dimension extraction strategy

Dimensions should become reusable entities.

### Target dimensions

| Dimension | Examples |
|---|---|
| education | field, degree, level |
| occupation | yrke, role family |
| gender | male/female/other categories in source |
| age | age groups |
| industry | næring |
| sector | public/private |
| region | geography |
| employment type | full-time/part-time |
| time | year/period |

---

## 13. Hierarchical dimensions

Many public datasets contain hierarchy, for example:

- occupation groups
- education levels
- industries
- sectors

The system must support:

- parent-child relationships
- aggregation
- rollups
- subgroup analysis

Example:

```text
engineering
  -> software engineering
  -> electrical engineering
  -> mechanical engineering
```

---

## 14. Temporal strategy

Statistical data is time-sensitive. Preserve:

| Field | Purpose |
|---|---|
| `observed_at` | when the observation applies |
| `fetched_at` | when data was retrieved |
| `valid_from` | validity start |
| `valid_to` | validity end |
| `dataset_period` | reporting period |
| `classification_version` | taxonomy/mapping version |

---

## 15. Handling classification changes

Public datasets evolve over time. Changes may include:

- renamed dimensions
- new categories
- removed categories
- changed hierarchy
- revised methodologies

The system must:

- preserve old mappings
- avoid destructive rewrites
- support deprecated categories
- support versioned mappings

---

## 16. Deprecated dimensions

Deprecated categories must remain historically queryable.

Examples:

- old education categories
- retired occupation groups

Deprecated does **not** mean deleted. It means:

- inactive for future mapping
- preserved for historical explainability

---

## 17. Statistical signal strategy

Statistical datasets generate structured signals.

### Primary signal types

| Signal | Purpose |
|---|---|
| `verified_statistical` | authoritative quantitative signal |
| `market_signal` | labor market pattern |
| `trajectory_signal` | career movement indicator |
| `risk_signal` | elevated employment risk pattern |
| `transition_signal` | movement between education/role/industry |

---

## 18. Verified statistical signals

SSB-originated observations may produce `verified_statistical` when they are:

- directly supported by published statistics
- correctly mapped
- temporally valid
- not interpreted beyond evidence

Examples:

- unemployment rate
- transition rate
- employment share
- industry participation

---

## 19. Derived signal strategy

Derived signals require separate confidence handling.

Examples:

- trend interpretation
- role risk estimation
- trajectory attractiveness
- industry transition potential

These are analytical outputs, not direct statistical facts.

---

## 20. Confidence strategy

### Allowed confidence categories

| Confidence | Meaning |
|---|---|
| `verified_statistical` | direct published statistics |
| `explicit_requirement` | explicit source statement |
| `inferred_pattern` | repeated but inferred |
| `llm_extracted` | AI-derived |
| `candidate_claim` | self-reported |

---

## 21. Confidence rules for public statistics

SSB observations can receive `verified_statistical`.

However:

- mappings,
- interpretations,
- trajectories,
- trend conclusions

must receive independent confidence scoring.

No derived interpretation should inherit full statistical certainty automatically.

---

## 22. Mapping strategy

Mappings connect source systems to canonical taxonomies.

| Source | Target |
|---|---|
| SSB education dimensions | role/education taxonomy |
| Studiebarometeret programs | education taxonomy |
| NAV occupations (later) | role families |
| industries | industry taxonomy |
| occupations | trajectory taxonomy |

---

## 23. Mapping principles

Mappings must support:

- many-to-many relationships
- partial overlap
- confidence scoring
- explainability
- temporal validity

Mappings must never appear deterministic unless explicitly verified.

---

## 24. Storage strategy

### Raw layer

Stores:

- original files
- original metadata
- snapshots

### Normalized layer

Stores:

- dimensions
- observations
- canonical mappings

### Aggregated layer

Stores:

- rollups
- summaries
- grouped metrics

### Derived layer

Stores:

- signals
- gaps
- overlaps
- recommendations
- trajectories

---

## 25. Explainability requirements

Every derived insight must trace back to:

- source dataset
- dataset version
- dimension values
- observation period
- transformation logic
- confidence category

No statistical recommendation should exist without traceability.

---

## 26. Quality evaluation

The ingestion layer should be evaluated on:

| Metric | Goal |
|---|---|
| dimension correctness | high |
| mapping accuracy | high |
| temporal consistency | high |
| explainability completeness | high |
| provenance completeness | high |
| duplicate prevention | high |

---

## 27. MVP operational scope

### MVP includes

- four SSB tables
- Studiebarometeret 2025 assets
- manual review
- small-scale normalization
- canonical mappings
- statistical signals
- explainability

### MVP excludes

- forecasting
- automation at scale
- embeddings/vector retrieval
- realtime sync
- full historical ingestion
- autonomous inference

---

## 28. Future expansion

Future versions may support:

- historical trend ingestion
- Nordic datasets
- forecasting
- embeddings
- graph relationships
- trajectory prediction
- automated reclassification
- labor market simulation

These are not part of MVP.

---

## 29. Summary

The public statistical ingestion layer transforms authoritative datasets into explainable intelligence infrastructure.

The architecture prioritizes:

- provenance
- explainability
- temporal integrity
- controlled normalization
- confidence-aware interpretation

The MVP focuses on:

- four SSB datasets
- Studiebarometeret 2025
- reusable dimensions
- verified statistical signals
- traceable mappings

before scaling toward:

- forecasting
- automation
- embeddings
- advanced labor market intelligence


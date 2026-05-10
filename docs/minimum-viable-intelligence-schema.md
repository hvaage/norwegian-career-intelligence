# Minimum Viable Intelligence Schema (MVP)

**Schema specification only:** no SQL migrations, no import scripts, no application code changes in this deliverable. This document defines the **first operational database layout** for the Norwegian Career Intelligence Dataset and **sokr.online**.

**Related documents:** [Dataset design](education-demand-intelligence-design.md) · [Career taxonomy](career-taxonomy-design.md) · [Scoring and signal model](scoring-and-signal-model.md)

---

## 1. Purpose of the MVP schema

### Why this is an MVP schema

The goal is a **first database shape** that teams can **populate, query, and test** quickly—not a final enterprise model. The MVP must support **ingestion**, light **normalization**, **signal extraction**, **gap/overlap analysis**, **recommendations**, and **early RAG** without waiting for perfect domain analysis.

### Why over-normalization is avoided initially

Too many narrow tables slow iteration, obscure joins for analytics, and freeze decisions before real Norwegian data proves which grains matter. The MVP favors **wider rows**, **JSON payloads where useful**, and **denormalized snapshots** on analytic tables so one query answers “why did we recommend this?”

### Why rapid iteration matters

Taxonomy, scoring rules, and source quality will shift after the first NAV import and first user tests. The schema should allow **adding columns**, **versioning rules**, and **parallel experiments** without multi-week migrations.

### Why explainability matters

Product and compliance need **traceability**: recommendation → gap → signals → dataset row. The MVP stores **foreign keys and version ids** explicitly rather than hiding logic only in application code.

### What the schema must support

| Need | MVP approach |
|------|----------------|
| **Ingestion** | Source layer with raw pointers and versions |
| **Normalization** | Taxonomy + typed requirement rows; not full 3NF org graphs |
| **Scoring** | Signals + analysis tables carrying scoring_model_version |
| **Recommendations** | Rows linked to gap/overlap ids and rule ids |
| **RAG retrieval** | Chunk tables with metadata JSON and confidence |
| **Debugging** | Preserved raw paths, signal_sources junction, explain_json fields |
| **Experimentation** | Optional experiment_id on signals/recommendations |

### MVP vs future enterprise schema

| Aspect | MVP | Future enterprise (directional) |
|--------|-----|--------------------------------|
| Normalization | Pragmatic; some duplication OK | Split dimensions, history tables, stricter FKs |
| Identity | Single org table optional later | Party/role split, graph edges |
| Search | Postgres text + filters | Vectors, graph DB, streaming |
| Governance | Columns + docs | Full data catalog, lineage tools |

### Expected evolution

After **usage patterns** and **data volumes** validate:

- Split hot JSON into child tables where query cost hurts.  
- Add **embedding** columns or external vector store.  
- Introduce **ontology** edges without throwing away stable taxonomy **IDs**.

**Principle stated clearly:** this schema is **intentionally pragmatic and incomplete**. Normalization can **increase later** once real data and queries justify the cost.

---

## 2. Design principles

| Principle | Operational meaning |
|-----------|---------------------|
| **Readable over perfect** | Table and column names match product language (gap, overlap, signal). |
| **Explicit over clever** | Store `confidence_category` as text/enum column; avoid magic inference in DB only. |
| **Flexible over rigid** | JSONB for variable source shapes; optional columns nullable. |
| **Evidence-first modeling** | Evidence rows and market requirements point to `dataset_version_id` or raw URI. |
| **Confidence-aware records** | Signals, gaps, chunks carry confidence fields aligned with scoring spec. |
| **Temporal awareness** | `observed_at`, `valid_from`, `valid_to`, market period on facts. |
| **Source traceability** | Every interpreted row can reach `sources` / `datasets` / `dataset_versions`. |
| **Explainable scoring** | `explain_json` or contributor breakdown on gaps and readiness. |
| **Partial data** | Nullable FKs; `profile_completeness` on candidates. |
| **Iterative taxonomy** | Stable `slug`/`code`; `replaced_by_id`, `taxonomy_version` on taxonomy rows. |

---

## 3. Source layer

Registers **where data came from**, **which extract**, and **how to audit** it.

### `sources`

| Aspect | Content |
|--------|---------|
| **Purpose** | Canonical registry of origins (API, file, scrape target). |
| **Why it exists** | Licensing, refresh policy, and intelligence layer (Spor 1, Spor 2, NAV) attach here once. |
| **Suggested core fields** | `id`, `slug`, `name`, `kind` (api, file, scrape, survey), `intelligence_layer` (spor1, spor2, nav), `base_url`, `owner_team`, `license_notes`, `default_refresh_frequency`, `reliability_score`, `is_active`, `created_at`, `updated_at` |
| **Relationships** | 1→many `datasets` |
| **Expected usage** | UI filters, RAG source weighting, compliance checks |

### `datasets`

| Aspect | Content |
|--------|---------|
| **Purpose** | Logical dataset (e.g. “SSB table 06913”, “Studiebarometeret 2024 wave”, “NAV feed snapshot”). |
| **Why it exists** | Groups files/APIs and variables under one human concept. |
| **Suggested core fields** | `id`, `source_id`, `external_id`, `title`, `description`, `access_method`, `created_at` |
| **Relationships** | → `sources`; 1→many `dataset_versions` |
| **Expected usage** | Ingestion jobs, documentation links |

### `dataset_versions`

| Aspect | Content |
|--------|---------|
| **Purpose** | Immutable **version** of a fetch/extract (checksum, storage pointer, period). |
| **Why it exists** | Reproducibility: “this signal came from version V.” |
| **Suggested core fields** | `id`, `dataset_id`, `version_label`, `fetched_at`, `observed_at`, `period_start`, `period_end`, `storage_uri`, `checksum`, `row_count_estimate`, `ingestion_status`, `error_log_ref`, `schema_snapshot_ref` (optional JSON describing columns) |
| **Relationships** | → `datasets`; referenced by `market_requirements`, `retrieval_chunks`, `signals` (via `signal_sources`) |
| **Expected usage** | Debugging, rollback, RAG citation |

### Cross-cutting: ownership, refresh, provenance, licensing, ingestion, versions

| Topic | Handling |
|-------|----------|
| **Ownership** | `owner_team` on `sources`; escalation contact in metadata JSON if needed. |
| **Refresh** | `default_refresh_frequency` on source; actual `fetched_at` on `dataset_versions`. |
| **Provenance** | All downstream facts prefer `dataset_version_id`. |
| **Licensing** | `license_notes` + optional `allowed_use_flags` (e.g. internal_only). |
| **Ingestion** | `ingestion_status`, `error_log_ref` on version row. |
| **Version handling** | Never overwrite a released `dataset_version`; append new row. |

### Examples (rows live in `sources` + `datasets` + `dataset_versions`)

| Example | `sources.kind` | Notes |
|---------|----------------|--------|
| **NIFU** | file / api | One dataset per report or table product; version per download. |
| **Studiebarometeret** | file | Version per wave file set. |
| **SSB** | api | Version per StatBank extract timestamp. |
| **NAV** | api | Version per paginated crawl or daily snapshot. |
| **Trainee program pages** | scrape | Version per crawl; store HTML/JSON URI. |
| **Glassdoor** | scrape / vendor | Separate source; legal flags on `sources`. |

---

## 4. Taxonomy layer

Shared **vocabulary** for roles, skills, industries, evidence, selection, and recommendation types. Aligns with [Career taxonomy design](career-taxonomy-design.md); IDs here are **stable contracts** for signals and RAG.

### Tables (MVP)

| Table | Purpose | Suggested fields (core) | Examples | Normalization notes | Synonyms | Confidence / time |
|-------|---------|-------------------------|----------|---------------------|----------|-------------------|
| **role_families** | Top-level and sub-roles | `id`, `slug`, `parent_id`, `label_nb`, `label_en`, `description`, `taxonomy_version`, `status` (active, deprecated), `replaced_by_id`, `valid_from`, `valid_to` | `consulting`, `technology` | Tree via `parent_id`; avoid deep splits in MVP | `aliases_json` or child `role_family_aliases` table later | `valid_*` for mapping lifespan |
| **competencies** | Skill nodes | `id`, `slug`, `category` (hard_skills, …), `label_nb`, `parent_id`, `taxonomy_version`, `status`, `replaced_by_id`, `valid_from`, `valid_to` | `python`, `stakeholder_management` | Single table MVP; `related_competency_ids` JSON optional | `synonyms_json` array MVP | Same |
| **industries** | Norway-oriented sectors | `id`, `slug`, `label_nb`, `parent_id`, `nace_code` (nullable), `taxonomy_version`, `valid_from`, `valid_to` | `fintech`, `public_sector` | Flat-ish tree | Synonyms JSON MVP | Same |
| **employer_types** | Startup, enterprise, public, … | `id`, `slug`, `label_nb`, `description`, `taxonomy_version` | `scaleup`, `municipality` | Small enum-like table | Rarely needed | Stable |
| **evidence_types** | Proof categories | `id`, `slug`, `label_nb`, `default_weight`, `taxonomy_version` | `certification`, `measurable_result` | Maps to scoring doc weights | — | `default_weight` for scoring |
| **selection_methods** | Hiring steps | `id`, `slug`, `label_nb`, `description`, `taxonomy_version` | `case_interview`, `technical_assignment` | Small set | — | Stable |
| **recommendation_types** | Product action types | `id`, `slug`, `label_nb`, `description`, `taxonomy_version` | `improve_evidence`, `gain_certification` | Drives UI templates | — | Stable |

### Why taxonomy is shared infrastructure

Signals, market requirements, candidate evidence, gaps, and RAG chunks all reference the **same** `competency_id` / `role_family_id`. Without that, scores are incomparable.

### Stable IDs

Use **opaque UUIDs** or **immutable surrogate keys**; human meaning lives in `slug` + labels. **Never recycle** an id for a different meaning; use `replaced_by_id` + `status=deprecated`.

### Taxonomy evolution

- Bump `taxonomy_version` on the bundle (string e.g. `2026.1`).  
- New codes append; old codes deprecate with `replaced_by_id`.  
- Signals store `taxonomy_version` used at extraction time for replay.

---

## 5. Signal layer

Atomic **interpretations** feeding scoring (see [Scoring and signal model](scoring-and-signal-model.md)).

### `signals`

| Aspect | Content |
|--------|---------|
| **Purpose** | One row = one interpreted fact (possibly derived). |
| **Suggested fields** | `id`, `signal_type` (hard_signal, market_signal, …), `subject_type` (market, candidate, employer, role_profile), `subject_id` (polymorphic), `payload_json` (flexible: competency ids, numbers, text spans), `strength` (1–5), `confidence_category`, `confidence_score` (optional float 0–1), `extraction_method`, `is_derived`, `parent_signal_id` (nullable), `taxonomy_version`, `observed_at`, `valid_from`, `valid_to`, `scoring_model_version`, `experiment_id` (nullable), `explain_json` (optional contributors) |
| **Typing** | `signal_type` enum/string from scoring spec. |
| **Temporal** | `valid_*`, `observed_at`. |
| **Traceability** | Use `signal_sources` for many-to-many to `dataset_version` or raw fragment. |

### `signal_sources`

| Aspect | Content |
|--------|---------|
| **Purpose** | Link a signal to **one or more** provenance rows (direct evidence). |
| **Suggested fields** | `id`, `signal_id`, `dataset_version_id` (nullable), `source_uri` (nullable), `row_pointer` (JSON: row id, line range), `weight` (0–1 contribution), `created_at` |

### `signal_relationships`

| Aspect | Content |
|--------|---------|
| **Purpose** | Graph-lite: derived, contradicts, reinforces, cascades_from. |
| **Suggested fields** | `id`, `from_signal_id`, `to_signal_id`, `relationship_type` (derived_from, contradicts, reinforces, supersedes), `note`, `created_at` |

### Concepts

| Concept | MVP handling |
|---------|--------------|
| **Direct vs derived** | `is_derived` + `parent_signal_id` / `signal_relationships`. |
| **Inferred** | Lower `strength` + `confidence_category=inferred_pattern` or `llm_extracted`. |
| **Cascading** | Chain in `signal_relationships` or single `parent_signal_id`. |
| **Contradictory** | `contradicts` edge; UI/scoring resolver picks or shows both. |
| **Weak vs strong** | `strength` + `confidence_category`. |

### Examples (conceptual)

| Example | signal_type | strength / confidence |
|---------|-------------|------------------------|
| Explicit employer requirement | hard_signal | 4–5 / explicit_requirement |
| Inferred leadership | soft_signal | 2 / inferred_pattern |
| Repeated market demand | market_signal | 3–4 / inferred_pattern or verified_statistical if aggregated |
| Weak network signal | network_signal | 1–2 / weak_signal |
| Trajectory blocker | trajectory_signal | varies / explicit or inferred |

---

## 6. Candidate layer

Supports **profiles**, **evidence items**, and **materialized** or **computed** candidate signals for fast queries.

### `candidate_profiles`

| Aspect | Content |
|--------|---------|
| **Purpose** | Persona or user profile container for MVP testing. |
| **Suggested fields** | `id`, `external_user_id` (nullable, encrypted ref later), `display_name` (nullable), `target_role_family_id` (nullable), `target_industry_id` (nullable), `career_stage` (enum), `profile_json` (headline, summary, skills list), `profile_completeness` (0–1), `explicit_vs_inferred_flags` (JSON), `created_at`, `updated_at`, `consent_flags_json` |
| **Privacy** | Minimize PII; prefer internal test UUIDs early; document GDPR purpose. |
| **Usage** | Gap/overlap target side; readiness input |

### `candidate_evidence`

| Aspect | Content |
|--------|---------|
| **Purpose** | Structured proof rows (CV bullet, cert, link). |
| **Suggested fields** | `id`, `candidate_profile_id`, `evidence_type_id`, `title`, `description`, `url` (nullable), `occurred_at`, `valid_from`, `valid_to`, `verification_status` (self_reported, verified, disputed), `confidence_category`, `payload_json`, `source_note` |
| **Relationships** | → `evidence_types`; can link to `dataset_version_id` if imported from survey |

### `candidate_signals`

| Aspect | Content |
|--------|---------|
| **Purpose** | Cached signals for a candidate (optional denorm for speed). |
| **Suggested fields** | `id`, `candidate_profile_id`, `signal_id` (FK to `signals` where `subject_type=candidate`), or duplicate slim columns mirroring `signals` for hot read path — **MVP pick one**: prefer FK to `signals` for single source of truth. |

### Modeling notes

| Topic | Approach |
|-------|----------|
| **Explicit vs inferred** | Inferred rows get lower confidence; optional `provenance_signal_id`. |
| **Completeness** | `profile_completeness` heuristic from filled fields + evidence count. |
| **Verification** | `verification_status` on evidence; never upgrade to “verified” without process. |

### Examples

| Profile type | Characteristics |
|--------------|-----------------|
| **Graduate** | Few `candidate_evidence`; many inferred gaps |
| **Career switcher** | Rich evidence but wrong industry/role_family |
| **Executive** | Leadership evidence; trajectory signals dense |
| **Incomplete** | Low `profile_completeness`; recommendations favor “improve evidence” |

---

## 7. Market layer

Captures **what the market asks for** at role, industry, or employer grain—mostly from NAV + Spor 2 + aggregates.

### `market_requirements`

| Aspect | Content |
|--------|---------|
| **Purpose** | Generic market fact (often aggregated). |
| **Suggested fields** | `id`, `competency_id` (nullable), `role_family_id` (nullable), `industry_id` (nullable), `requirement_kind` (frequency, median_years_experience, explicit_text), `value_json`, `market_period_start`, `market_period_end`, `confidence_category`, `strength`, `dataset_version_id`, `is_inferred`, `created_at` |

### `role_requirements`

| Aspect | Content |
|--------|---------|
| **Purpose** | Requirement bundle scoped to **role_family** (profile of role). |
| **Suggested fields** | `id`, `role_family_id`, `competency_id`, `importance_weight`, `requirement_level` (nice_to_have, common, critical), `evidence_type_preference` (nullable), `market_period_start`, `market_period_end`, `dataset_version_id`, `confidence_category` |

### `employer_requirements`

| Aspect | Content |
|--------|---------|
| **Purpose** | Employer-specific criteria (trainee pages, job ads tied to employer). |
| **Suggested fields** | `id`, `employer_id` (nullable until org resolution exists), `employer_name_raw`, `source_dataset_version_id`, `text_excerpt`, `structured_json` (GPA rules, language), `role_family_id` (nullable), `competency_id` (nullable), `valid_from`, `valid_to`, `confidence_category` |

### Behavior

| Topic | MVP rule |
|-------|----------|
| **Explicit vs inferred** | `is_inferred` / `confidence_category` on `market_requirements`. |
| **Employer-specific** | `employer_requirements`; may duplicate text from NAV until dedupe matures. |
| **Industry-specific** | Filter `market_requirements` by `industry_id`. |
| **Changing requirements** | New rows per `market_period_*`; do not overwrite history. |

### Examples

| Domain | Stored as |
|--------|-----------|
| Consulting recruitment | High `case_interview` selection_method signals + `role_requirements` for analytical competencies |
| Public-sector hiring | `employer_types` public + formal degree `employer_requirements` |
| SaaS PM | Mixed `role_requirements` + NAV-derived `market_requirements` |
| Engineering | Strong `competency_id` rows with tool stack in `value_json` |

---

## 8. Analysis layer

Outputs of **gap/overlap engines** and **readiness**; tuned to [Scoring and signal model](scoring-and-signal-model.md).

### `gaps`

| Aspect | Content |
|--------|---------|
| **Purpose** | One row per detected gap for a subject (usually candidate + target context). |
| **Suggested fields** | `id`, `gap_type` (role_gap, evidence_gap, …), `subject_type`, `subject_id`, `target_context_json` (role_family_id, industry_id, employer_id), `severity` (0–1 or 1–5), `confidence_category`, `contributing_signal_ids` (array UUID), `explain_json`, `scoring_model_version`, `market_period`, `created_at` |

### `overlaps`

| Aspect | Content |
|--------|---------|
| **Purpose** | Match scores between candidate (or entity A) and target. |
| **Suggested fields** | `id`, `overlap_type`, `subject_type`, `subject_id`, `target_context_json`, `score`, `score_band_low`, `score_band_high`, `confidence_category`, `contributing_signal_ids`, `explain_json`, `scoring_model_version`, `market_period`, `created_at` |

### `recommendations`

| Aspect | Content |
|--------|---------|
| **Purpose** | Actionable items for product / RAG. |
| **Suggested fields** | `id`, `candidate_profile_id`, `recommendation_type_id`, `priority_class` (e.g. high_impact_low_effort), `urgency`, `effort_estimate`, `impact_estimate`, `title`, `body`, `trigger_gap_ids` (array), `trigger_overlap_ids` (array), `status` (active, dismissed, done), `confidence_floor`, `scoring_model_version`, `created_at`, `valid_until` |

### `readiness_scores`

| Aspect | Content |
|--------|---------|
| **Purpose** | Snapshot of readiness stage + numeric sub-scores. |
| **Suggested fields** | `id`, `candidate_profile_id`, `readiness_stage` (exploring, …), `score_json` (dimensions), `overall_score` (optional), `explain_json`, `target_context_json`, `scoring_model_version`, `computed_at` |

### Explainability requirements (see also §12)

- `explain_json` lists **top contributors** (signal ids, requirement ids).  
- `severity` / `score` never standalone without **confidence_category**.

### Examples

| Output | Example |
|--------|---------|
| Evidence gap | High severity, contributors = missing `measurable_result` for commercial competency |
| Trajectory gap | Next-step competency bundle short by one node |
| Strong industry overlap | score 0.82, confidence explicit + statistical |
| Interview readiness | readiness_stage = interview_ready, sub-scores in `score_json` |

---

## 9. RAG layer

Retrieval-first tables; **embeddings optional later** (extra column or external store).

### `retrieval_chunks`

| Aspect | Content |
|--------|---------|
| **Purpose** | Text unit for embedding/search. |
| **Suggested fields** | `id`, `dataset_version_id` (nullable), `source_table` (e.g. employer_requirements), `source_row_id`, `chunk_index`, `text`, `language` (nb, en, mixed), `token_count`, `chunk_hash`, `taxonomy_version`, `created_at` |

### `retrieval_metadata`

| Aspect | Content |
|--------|---------|
| **Purpose** | One row per chunk (or JSON on chunk—MVP: separate table if cleaner ACLs). |
| **Suggested fields** | `id`, `retrieval_chunk_id`, `role_family_id` (nullable), `industry_id` (nullable), `competency_ids` (array), `market_period_start`, `market_period_end`, `source_title`, `citation_label`, `norwegian_market` (bool), `freshness_date` |

### `retrieval_confidence`

| Aspect | Content |
|--------|---------|
| **Purpose** | Confidence and reliability for ranking (denormalized for fast filters). |
| **Suggested fields** | `id`, `retrieval_chunk_id`, `confidence_category`, `confidence_score`, `source_reliability_score`, `is_review_based`, `is_statistical`, `retrieval_rank_boost` (optional manual), `updated_at` |

### Chunking strategy (MVP)

- Prefer **paragraph- or section-level** chunks (500–1.5k tokens target).  
- Statistical tables: one chunk per **indicator + period** with human-readable sentence prefix.  
- Preserve **citation_label** = “SSB 06913 2023” style strings.

### Retrieval behavior

| Topic | Rule |
|-------|------|
| **Explainable** | Every chunk has `dataset_version_id` or explicit `source_row_id`. |
| **Freshness** | Sort key includes `freshness_date` / period end. |
| **Confidence-aware** | Join `retrieval_confidence`; filter out `weak_signal` for default user mode. |
| **Norwegian market** | `norwegian_market=true` default filter for nb-first product. |

### Examples

| Chunk type | Source |
|------------|--------|
| SSB statistics | `dataset_version` for SSB + generated sentence + table snippet |
| Trainee criteria | `employer_requirements` row text |
| Interview review insight | Aggregated text chunk with `is_review_based=true` |
| Recommendation rule | Static template chunk with `recommendation_types` reference |

---

## 10. Relationships between layers

### Flow (high level)

```text
sources → datasets → dataset_versions → (raw storage)
                     ↓
              taxonomy tables (stable IDs)
                     ↓
         market_requirements / role_requirements / employer_requirements
                     ↓
                    signals (+ signal_sources)
                     ↓
              gaps / overlaps / readiness_scores
                     ↓
                 recommendations
                     ↓
         retrieval_chunks (+ metadata + confidence)
```

### Taxonomy → signals

Normalization jobs emit `signals` with `competency_id` / `role_family_id` in `payload_json` and link `signal_sources` to `dataset_version_id`.

### Signals → gaps / overlaps

Scoring job reads candidate signals + market requirements → writes `gaps` / `overlaps` with `contributing_signal_ids`.

### Gaps / overlaps → recommendations

Rule engine maps (gap_type, severity, priority_class) → `recommendations` rows with `trigger_gap_ids`.

### Recommendations ↔ RAG

RAG retrieves chunks filtered by gap/recommendation context; optional static chunks explain **recommendation_types**.

### Partial denormalization (why)

| Location | Denorm | Future split |
|----------|--------|--------------|
| `gaps.explain_json` | Embeds labels | Pull contributor rows if DB size hurts |
| `employer_requirements.employer_name_raw` | Until `employers` table mature | FK to employers |
| `retrieval_metadata` separate | Easier to rebuild | Could merge into chunks |

### Example flows (short)

1. **Candidate:** profile + evidence → signals → gaps/overlaps → recommendations → RAG cites trainee + SSB chunks.  
2. **Market ingestion:** NAV version → employer_requirements + market_requirements → signals → role_requirements aggregate job.  
3. **Recommendation generation:** evidence_gap severity > τ → `recommendation_types.improve_evidence` row with trace ids.

---

## 11. Confidence and temporal handling

### Fields (reuse across tables)

| Field | Role |
|-------|------|
| `observed_at` | When the system recorded the fact |
| `valid_from` / `valid_to` | Business validity of mapping or requirement |
| `dataset_versions.period_*` | What period the **data** describes |
| `market_period` on gaps/overlaps | Window used for scoring |
| `stale_after` | Policy per source type (app config or `sources` JSON); not always a column—can be computed |
| `confidence_score` | Optional numeric helper |
| `confidence_category` | Enum aligned with scoring spec |

### Inheritance and decay

- Child signal **confidence** ≤ min(parent, source reliability cap).  
- **Decay jobs** lower `confidence_score` or flag `is_stale` when `now > valid_to` or period superseded.

### Why nearly all layers need this

Without time and confidence, **RAG** mixes eras; **recommendations** fight each other; **debugging** cannot reproduce scores. MVP stores these fields **even when nullable** to force pipeline discipline.

---

## 12. Explainability requirements

| Artifact | Expectation |
|----------|----------------|
| **Recommendation** | `trigger_gap_ids`, `trigger_overlap_ids`, `recommendation_type_id`, `scoring_model_version` |
| **Score / readiness** | `explain_json` with ordered contributors |
| **Gap** | `contributing_signal_ids` + human-readable template in `explain_json` |
| **Overlap** | Identify **sources** via signals → `signal_sources` |
| **RAG answer** | UI cites chunk → `retrieval_metadata.citation_label` + `confidence_category` |

### Examples (user-facing “why”)

| Question | Answer backed by |
|----------|-------------------|
| Why this recommendation? | `recommendations.trigger_gap_ids` → `gaps.explain_json` |
| Why readiness dropped? | New `dataset_version` or decay job; compare `scoring_model_version` |
| Why strong overlap? | `overlaps.contributing_signal_ids` + high-weight evidence |
| Why market mismatch? | Low `role_requirements` match vs candidate `candidate_signals` |

---

## 13. MVP limitations

| Intentionally simplified / omitted | Why delay |
|-----------------------------------|-----------|
| **No graph database** | Postgres FKs + `signal_relationships` enough to learn patterns first |
| **No embeddings yet** | Add column or Pinecone/pgvector after chunk quality validated |
| **No advanced ontology** | Taxonomy tables + JSON edges suffice |
| **No realtime streaming** | Batch NAV + periodic SSB first |
| **No autonomous agents** | Product-controlled jobs + human review |
| **No heavy optimization** | No sharding/partitioning until size proven |
| **No fully normalized enterprise schema** | Faster learning; merge/split tables after queries stabilize |
| **Single-region Norway focus** | Reduces columns; add country later |

---

## 14. Future evolution

| Addition | Prerequisite |
|----------|----------------|
| **Embeddings / vector search** | Stable `retrieval_chunks` + evaluation harness |
| **Ontology graph** | Stable competency/role IDs + use-cases for traversals |
| **Advanced / predictive scoring** | Historical outcome data + governance |
| **Recruiter calibration** | Legal agreements + anonymized labels |
| **Transition prediction** | Clean trajectory signals + privacy review |
| **Labor-market forecasting** | Time series at stable taxonomy grain |
| **Automated extraction pipelines** | Confidence metrics + human QA queues |
| **Multilingual** | Label columns + synonym tables proven |

**What must stabilize first:** **taxonomy IDs**, **confidence enums**, and **dataset_version** discipline.

---

## 15. First implementation priorities

| Phase | Goals | Expected outputs | Validation | Common risks |
|-------|-------|------------------|--------------|--------------|
| **1. Taxonomy foundation** | Load v0 codes | Populated taxonomy tables | Spot-check coverage for pilot roles | Scope creep on competency depth |
| **2. Source metadata** | Register Spor 1/2/NAV | `sources`, `datasets`, empty or stub `dataset_versions` | Every future row has a home | Wrong owner/contact |
| **3. First raw imports** | Land SSB + one NAV snapshot | `dataset_versions` with URIs | Checksums, row counts | Schema drift in raw JSON |
| **4. Normalization** | employer_requirements + market_requirements pilot | Typed rows linked to taxonomy | Manual audit sample | Over-mapping to wrong competency |
| **5. Signal extraction** | Parser/rules MVP | `signals` + `signal_sources` | Precision/recall on sample | LLM-only without caps |
| **6. Overlap/gap engine** | First scores | `gaps`, `overlaps`, `readiness_scores` | Compare to expert rubric | Opaque explain_json |
| **7. Recommendation engine** | Rule-based priorities | `recommendations` | User comprehension tests | Too many items |
| **8. RAG prototype** | Retrieve with filters | `retrieval_chunks` + metadata | Citation accuracy | Stale chunks without period |

---

## 16. Open questions

1. **UUID strategy:** database-generated vs client UUIDs for offline pipelines?  
2. **Taxonomy ownership:** who approves `slug` changes?  
3. **Confidence calibration:** global thresholds vs per industry in config tables?  
4. **Review-source legality:** which rows enter `retrieval_chunks`?  
5. **Candidate privacy:** separate `candidate_profiles` DB/schema for prod PII?  
6. **Explainability thresholds:** minimum `explain_json` fields for ship?  
7. **Role-title normalization:** dedicated `title_synonyms` table timing?  
8. **Multilingual:** add `label_en` everywhere vs separate translation table?  
9. **Scoring recalculation:** nightly full recompute vs incremental per `dataset_version`?  
10. **Polymorphic `subject_type`:** tolerate vs enforce separate tables per subject later?

---

### Architecture summary

The **MVP schema** is a **layered, Postgres-friendly** layout: **sources** (with **dataset_versions** for immutable extracts), **shared taxonomy** tables with stable IDs, **signals** (with **signal_sources** and optional **signal_relationships**), **candidate** profile/evidence/(signal links), **market** requirement tables at multiple grains, **analysis** outputs (**gaps**, **overlaps**, **recommendations**, **readiness_scores**), and a **RAG** triple (**chunks**, **metadata**, **confidence**). It is **denormalized where explainability and speed need it**, **JSON-tolerant** for variable sources, and **confidence- and time-aware** end-to-end. It deliberately **defers** vectors, graphs, streaming, and deep normalization until **taxonomy**, **versioning**, and **scoring behavior** are validated on real Norwegian labor-market data.

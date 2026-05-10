# Norwegian Career Intelligence Dataset — Design Specification

This document specifies the **Norwegian Career Intelligence Dataset**: a structured research and analysis foundation for **sokr.online**. It covers purpose and scope, data layers, **confidence and evidence**, **time**, **career trajectories**, core entities, supply/demand/job models, gap/overlap/white spots, RAG usage, **taxonomy dependencies**, a conceptual Supabase-oriented layout, import strategy, roadmap, and open questions.

**Related:** [Career taxonomy design](career-taxonomy-design.md) · [Scoring and signal model](scoring-and-signal-model.md) (signal types, strength, gap/overlap scoring, RAG retrieval priority) · [MVP intelligence schema](minimum-viable-intelligence-schema.md) (first operational tables, layers, implementation order).

**Naming (used consistently below)**

| Name | Meaning |
|------|---------|
| **Spor 1** | Education Supply Intelligence — **source map** (where supply-side data lives and how it is accessed). |
| **Spor 2** | Employer Demand Intelligence — **source map** (where demand-side data lives and how it is accessed). |
| **NAV** | Job Market Intelligence — **source** for vacancy data (e.g. pam-stilling-feed when a token is available). |

The three intelligence layers combine: **Education Supply** (Spor 1 sources), **Employer Demand** (Spor 2 sources), **Job Market** (NAV and any future ad sources).

**Out of scope for this document:** SQL DDL, import scripts, and changes to existing repository scripts.

---

## 1. Purpose and scope

### Purpose

Build a **structured research and analysis database** that helps sokr.online reason about:

| Question cluster | Intent |
|------------------|--------|
| What higher education produces | Supply-side programs, fields, outcomes, reputation signals |
| What employers expect | Programs, selection design, criteria, preference rankings |
| What job ads demand | Market-visible requirements from vacancies (NAV when available) |
| What candidates lack | Gap signals vs market and vs role |
| Where candidates have strong overlap | Overlap signals vs programs, roles, and ads |
| What to emphasize | CV, applications, LinkedIn, interviews, networking — actionable recommendations |

### Geographic scope

- **Norway only** for the first implementation cycle.  
- External comparators (e.g. Nordic/EU benchmarks) may be noted in the source registry but are not in scope for v1 ingestion.

### Education scope

- **Higher education** (universities, university colleges, accredited programs).  
- **Continuing education** (EVU, micro-credentials, professional diplomas) where sources support stable identifiers and metadata.

### Candidate scope

- **Primary:** students, graduates, and **early-career** candidates.  
- **Model requirement:** entity and relationship design must allow **experienced candidates** later (seniority, career transitions, lateral moves) without schema rewrites — e.g. career stages, role families, trajectory constructs, and gap types that generalize beyond “first job” (see §5).

---

## 2. Data layers

The system is organized as stacked layers. Upper layers depend on lower layers; each layer has a clear responsibility.

| Layer | Purpose | Typical contents |
|-------|---------|------------------|
| **Source registry** | Authoritative list of origins, access method, legal/technical constraints, refresh cadence, contact | URLs, API keys (references only in app config), license notes, mapping to **Spor 1** / **Spor 2** / **NAV** where applicable |
| **Raw data layer** | Immutable payloads as received (files, API JSON, CSV exports) | Blob storage or raw tables with checksums, fetch timestamps, source version |
| **Normalized data layer** | Typed records aligned to core entities | Institutions, programs, employers, ads, survey rows — deduplicated and keyed |
| **Taxonomy layer** | Controlled vocabularies and crosswalks | Competency frameworks, ISCED/NUS mappings, industry codes, role family trees (see §12) |
| **Analysis layer** | Derived metrics and joins across supply/demand/market | Scores, aggregates, gap/overlap/white-spot records — always with **confidence and evidence** metadata (§3) |
| **Recommendation layer** | User- or segment-facing suggestion objects | Ranked actions, emphasis areas, course hints (not necessarily ML-only) |
| **RAG layer (sokr.online)** | Chunked, cited text + metadata for retrieval | Embeddings optional; **provenance, time period, and confidence** on every retrievable unit (§3, §4, §11) |

**Design principle:** raw is never silently overwritten; normalization is **versioned** or **append-incremental** where the source publishes revisions (e.g. job ads, rankings). **Time** and **staleness** are first-class (§4).

---

## 3. Confidence and evidence model

### Why confidence and evidence strength matter

Downstream uses (gaps, overlaps, recommendations, RAG, CV generation) compound error if weak or ambiguous signals are treated like facts. The dataset must distinguish:

- **How strong the underlying evidence is** (sample size, direct quote, official statistic).  
- **How confident we are in the mapping** (exact taxonomy hit vs inferred from text vs model extraction).

Without this, users see overconfident advice; governance and debugging become impossible. Every **signal** (parsed requirement, gap, overlap, recommendation rule match) should carry **evidence** metadata and a **confidence category** suitable for filtering and UI disclosure.

### Core concepts

| Concept | Role |
|---------|------|
| **evidence_strength** | Objective support for the claim: e.g. n of observations, whether the text is a direct employer statement, official table cell. Drives minimum bar for use in high-stakes outputs. |
| **evidence_confidence** | Subjective or model-estimated certainty that the **interpretation** is correct (e.g. correct competency tag for a phrase). May differ from evidence_strength. |
| **evidence_source_type** | Class of origin: statistical_official, employer_document, job_ad_text, survey_aggregate, review_aggregate, internal_curation, etc. |
| **signal_origin** | Pipeline stage that produced the signal: raw_import, rule_engine, human_curation, llm_extraction, statistical_model. |
| **extraction_method** | How text/structure became a field: regex, parser, manual_entry, embedding_classifier, etc. |
| **human_verified** | Boolean or enum (none / spot_check / full_review) for curated rows. |
| **source_reliability_score** | Optional normalized score for the **source** (not per row): stability, methodology transparency, update frequency. Feeds ranking in RAG and default weights in analytics. |

These attach to normalized facts, parsed requirements, gap/overlap rows, recommendation triggers, and RAG chunk metadata.

### Suggested confidence categories

Use categories as **tags** for filtering and for user-facing disclosure (“inferred from job ads 2024 Q1” vs “official SSB statistic”).

| Category | Typical use |
|----------|-------------|
| **verified_statistical** | Official tables, published aggregates with known methodology |
| **explicit_requirement** | Stated in job ad or employer doc with clear wording |
| **explicit_selection_criterion** | Stated hiring rule (e.g. GPA floor, test type) from employer or program page |
| **inferred_pattern** | Derived from repeated co-occurrence or heuristics across ads/processes |
| **candidate_self_reported** | Survey or profile data about perceptions or behavior |
| **llm_extracted** | Model-labeled fields; always lower default trust unless validated |
| **scraped_review_signal** | Themes from reviews/aggregates; legal and bias constraints apply |
| **weak_signal** | Thin n, old data, or ambiguous text — usable for exploration, not primary claims |

### Downstream effects

| Consumer | Effect of confidence model |
|----------|----------------------------|
| **Gap scoring** | Weight components by category; cap scores when evidence is `weak_signal` or `evidence_gap`; surface uncertainty in UI. |
| **Overlap scoring** | Same; prefer `verified_statistical` + `explicit_*` for default “strong match” badges. |
| **Candidate recommendations** | Never promote `llm_extracted` or `weak_signal` as sole rationale without corroboration; prefer human-verified or explicit sources for “must do” actions. |
| **RAG answers** | Retrieve with metadata; prompt or template requires **citations including confidence category** (§11); refuse or soften when only weak signals exist. |
| **CV generation** | Bullet provenance: statistical vs inferred vs review-based; optional wording strength (“commonly requested” vs “explicitly required in similar roles”). |
| **Package builder** | Bundle items with a **minimum confidence tier** per artifact type (e.g. interview prep packs pull `explicit_selection_criterion` first). |

---

## 4. Temporal model

Labor-market intelligence **changes over time**: ads expire, curricula shift, rankings update, certifications gain or lose salience. The system must record **when** something was true and **for which period** aggregates apply, so comparisons and RAG do not silently mix eras.

### Concepts

| Concept | Meaning |
|---------|---------|
| **observed_at** | When the system saw the fact (fetch, scrape, import timestamp). |
| **valid_from** / **valid_to** | Business validity of a normalized fact or taxonomy mapping (inclusive/exclusive rules to be fixed at implementation). |
| **dataset_period** | Period the **dataset extract** describes (e.g. SSB reference year, survey wave). |
| **market_period** | Period for **market-facing** aggregates (e.g. ad counts by quarter). |
| **trend_window** | Rolling span used for trend features (e.g. 4 quarters, 3 years). |
| **source_refresh_frequency** | Expected cadence per source (registry field). |
| **staleness** | Derived state when `now` exceeds **stale_after** policy for that use case (e.g. NAV ads for “current market” stale after days; SSB annual tables stale after next release). |

`stale_after` can be policy per **source + consumer** (RAG vs dashboard vs recommendations).

### Why time matters (examples)

| Domain | Risk if time is ignored |
|--------|-------------------------|
| **NAV job ads** | Expired ads treated as current demand; wrong seasonality. |
| **Trainee programs** | Intake rules and deadlines change year to year. |
| **Certification trends** | Vendor certs and regulatory reqs spike or fade. |
| **Employer preferences** | Ranking surveys are year-specific. |
| **Emerging / declining skills** | Keyword frequency shifts; old ads mislead trajectory advice. |
| **Salary and employment outcomes** | SSB and graduate surveys are cohort- and year-specific. |

**Trajectory modeling (§5)** and **gap/overlap** metrics should use aligned `market_period` / `dataset_period` windows to avoid comparing 2018 supply to 2025 ads without explicit labeling.

---

## 5. Career trajectory model

The system should model **career progression**, not only static “does this profile match this role.” Progression informs what to learn next, how to phrase LinkedIn headlines, which gaps are **stage-specific**, and which interview stories to prepare.

### Entities / concepts

| Concept | Description |
|---------|-------------|
| **career_paths** | Named or inferred progression lines (e.g. “consulting track”, “PMO track”) linking stages and role families. |
| **career_path_steps** | Ordered nodes on a path: role family, typical seniority, optional employer context. |
| **career_transition_signals** | Evidence-backed moves: frequency of A→B in data, employer program targets, ad-based title transitions (each with confidence). |
| **next_role_probability** | Optional probabilistic edge between steps (survey-, ad-, or curated-derived); always versioned by period. |
| **career_stage_requirements** | Typical expectations at a step (competencies, education, certifications) — may differ from “generic role family” averages. |
| **trajectory_gaps** | Missing enablers for **next** step (not only current-role fit). |
| **typical_time_to_next_stage** | Distributions or medians from outcomes data; low confidence if evidence thin. |
| **alternative_transition_paths** | Multiple graphs between same start/end (e.g. specialist vs manager track). |

### Example paths (illustrative)

- Student → Graduate → Junior Project Manager → Project Manager → Program Manager  
- Consultant → Senior Consultant → Manager → Director  
- Specialist → Senior Specialist → Lead → Head of Function  
- Manager → Executive → Board candidate  

Real paths in the database should be **grounded** in data (ads, employer programs, outcome statistics) or marked as **curated exemplars** with `human_verified` and explicit `confidence` category.

### Product support

| Product area | Trajectory use |
|--------------|----------------|
| **Career planning** | Show next plausible steps, prerequisites, and trajectory_gaps. |
| **Package builder** | Assemble materials per **target step** on a path, not only current role. |
| **LinkedIn optimization** | Headline/summary aligned to **next** step signals from market + employer demand. |
| **Learning recommendations** | Close trajectory_gaps and stage requirements for the next hop. |
| **Networking strategy** | Who/which contexts matter for transitions (weak signals allowed with disclosure). |
| **CV generation** | Narrative arc: past step evidence + readiness for next step. |
| **Interview preparation** | Stories that demonstrate **transition** competencies, not only static job fit. |

---

## 6. Core entities

Below are **proposed** entities (logical model). Names are indicative; physical table names may differ in Supabase. **Signals** (`gap_signals`, `overlap_signals`, parsed requirements, RAG chunks) should reference §3 and §4 metadata.

### Registry and catalog

| Entity | Description | Key relationships |
|--------|-------------|-------------------|
| **sources** | A human or machine origin (NIFU, SSB, NAV API, employer site) | → datasets |
| **datasets** | A concrete extract or API surface | → source, → variables; **dataset_period**, fetch time |
| **variables** | Column/field definitions within a dataset | → dataset; maps to taxonomy codes where applicable |

### Institutions and education

| Entity | Description | Key relationships |
|--------|-------------|-------------------|
| **institutions** | Universities, colleges, providers | → programs; external IDs (NSD, orgnr where relevant) |
| **education_programs** | Degree programs, EVU offerings | → institution, → fields_of_study, NUS/ISCED |
| **fields_of_study** | Standardized field / subject area | Many-to-many with programs; crosswalks to competencies |

### Employers and demand

| Entity | Description | Key relationships |
|--------|-------------|-------------------|
| **employers** | Companies, agencies, public bodies | → employer_programs, → job_ads |
| **employer_programs** | Trainee programs, graduate schemes, talent pipelines | → employer, → selection_processes |
| **selection_processes** | Stages, tests, cases, interviews | → employer_programs; links to competency/requirement signals |

### Market (job ads)

| Entity | Description | Key relationships |
|--------|-------------|-------------------|
| **job_ads** | Vacancy records from **NAV** (Job Market Intelligence) when ingested | → employer (resolved or unknown), → role_families, → industries; temporal fields |

### Skills, requirements, and market structure

| Entity | Description | Key relationships |
|--------|-------------|-------------------|
| **competencies** | Skills, behaviors, tools (taxonomy-backed) | Linked to ads, programs, selection steps, gaps |
| **certifications** | Named credentials | → job_ads, programs, gap signals |
| **education_requirements** | Explicit degree/field/level expectations | → job_ads, employer_programs |
| **role_families** | Job families / archetypes | → job_ads, overlap/gap signals, career_path_steps |
| **industries** | NACE or simplified industry | → employers, ads |

### Candidate and analytics

| Entity | Description | Key relationships |
|--------|-------------|-------------------|
| **candidate_stages** | Lifecycle dimension | Scopes recommendations, norms, and trajectory steps |
| **career_paths** / **career_path_steps** | Progression graphs | §5 |
| **gap_signals** | Typed mismatch | Evidence + confidence (§3); period (§4) |
| **overlap_signals** | Strength of match | Same |
| **white_spots** | Niche findings | Rule-based; references underlying signals |
| **recommendations** | Structured advice objects | Channel, priority, rationale refs, confidence floor |

**Cross-cutting:** every analytic entity references **evidence** (dataset row, ad id, survey wave) and carries **confidence / temporal** metadata per §3–§4.

---

## 7. Supply-side model (Education Supply Intelligence — Spor 1 sources)

Structure ingestion and normalization so **NIFU**, **Studiebarometeret**, **SSB**, and **university career services** (all mapped under **Spor 1**) land in comparable dimensions.

### NIFU

- Each **publication / data product** → `dataset` under source NIFU.  
- Preserve dimensions (field, institution type, year) as `variables` or dimension tables.  
- Map to `institutions` where codes exist; else reporting-level keys until crosswalk is curated.

### Studiebarometeret

- Wave- and institution-level files → `datasets` with **wave_id**, **institution**; **dataset_period** explicit.  
- Student-reported metrics → fact rows; link to `education_programs` when codes align, else deferred fuzzy match.

### SSB tables

- Each StatBank extract → `dataset` with table id, **period**, geography Norway.  
- Crosswalk education codes via taxonomy layer.

### University career services

- HTML/PDF/scrapes → raw layer with **observed_at**.  
- Subtype distinguishes **career_service_marketing** from **student_survey** (Studiebarometeret).

**Unified view:** program × **period** × metric for dashboards and gaps vs demand.

---

## 8. Demand-side model (Employer Demand Intelligence — Spor 2 sources)

Structure **employer-authored** and **third-party ranking/review** inputs (mapped under **Spor 2**) so legal and quality metadata stay clear.

### Trainee programs, criteria, cases, rankings, reviews

- Same structural ideas as before: `employer_programs`, selection criteria rows, `selection_processes` / steps, ranking `datasets` with **ranking_year**, review aggregates with **legal_use** and **evidence_source_type**.

**Unified view:** employer × program type × competency emphasis, **time-stamped**, for overlap with supply and NAV.

---

## 9. Job ad model (Job Market Intelligence — NAV)

When **NAV** pam-stilling-feed (or successor APIs) is available, `job_ads` anchor market-visible demand. Connect ads to role requirements, competencies, certifications, education expectations, seniority, employer type, B2B/B2C/B2G, sector, technical level, and industry — each association carrying **confidence** (§3) and **time** (§4).

**Integration:** shared `competencies` and `role_families` triangulate **Spor 2** (employer pages, selection) with **NAV** (ads) and **Spor 1** (education outcomes).

---

## 10. Gap, overlap, and white spot model

Store typed **gap_signals**, **overlap_signals**, and **white_spots** with **metric**, **period**, **evidence references**, and **confidence category** (§3). Do not collapse distinct gap types into a single score without preserving components.

### Gap types (detail)

| Gap type | Definition | Typical evidence |
|----------|------------|-------------------|
| **Supply–demand gap** | Graduate / program output vs market pull (ads, hiring volumes, employer programs) | SSB + NAV counts by field/period; confidence often `verified_statistical` where both sides are official |
| **Candidate–role gap** | Persona or user profile vs role or **next trajectory step** requirements | Aggregated explicit requirements + inferred patterns; mixed confidence |
| **Evidence gap** | Too little data to conclude | Low n, missing wave, stale NAV window; drives “we don’t know” UX |
| **Certification gap** | Market or employers emphasize cert C; education or CV corpus rarely shows C | Ad frequency vs program/CV signals |
| **Education gap** | Expected level/field vs held qualifications | Ads + employer programs vs supply |
| **Market expectation gap** | **Spor 2** messaging (career pages, rankings) vs **NAV** explicit requirements | Compare same employer or sector across sources; time-aligned |
| **Process gap** | Selection process tests X (e.g. case type); preparation content rarely covers X | Spor 2 process data vs career-service / course catalogs |
| **Network gap** | Transitions that rely on networks but supply-side or self-report data under-describes network building | Often `candidate_self_reported` or curated; use carefully |

### Overlap categories

| Overlap type | Meaning |
|----------------|---------|
| **Education overlap** | Shared level/field/NUS between profile and role path |
| **Competence overlap** | Intersection of competency sets (ads, programs, user) |
| **Industry overlap** | Same or adjacent NACE / industry cluster |
| **Role-family overlap** | Adjacent role families on a path or skill graph |
| **Evidence overlap** | Multiple independent sources agree (boost confidence) |
| **Selection-process overlap** | Same step types (case, test) across target employers |
| **Network overlap** | Weak signal: alumni, events, geography — only with explicit category and ethics review |

### White spot categories

| White spot type | Pattern |
|-----------------|---------|
| **Employer asks, education rarely trains** | High frequency in **explicit_requirement** / ads or Spor 2; low in curriculum or learning outcomes signals |
| **Ads mention often, candidates rarely document** | NAV term frequency vs anonymized CV corpus or survey “did you highlight X” |
| **Trainee programs test it, career services barely prepare** | Spor 2 process vs Spor 1 career-service content gap |
| **Important for hiring, absent from CVs/applications** | Combination of employer signals + (optional) application corpus; high risk of bias — requires governance |

**Implementation:** `white_spots` reference **rule_set_id**, supporting **gap**/**overlap** rows, and **valid** periods. Thresholds are **open governance** (see §16).

---

## 11. RAG usage in sokr.online

RAG is one consumer of the dataset; it must retrieve **typed, time-bound, confidence-labeled** knowledge — not only prose chunks.

### What RAG should retrieve

- **Source-backed facts** (quotes, bullets) with pointers to `sources` / `datasets`.  
- **Statistical findings** (precomputed aggregates, SSB/NIFU-backed statements).  
- **Employer selection signals** (Spor 2: criteria, steps, case types).  
- **Role expectations** (role family + stage requirements, trajectory §5).  
- **Education-to-work outcomes** (Spor 1 + outcomes-linked datasets).  
- **Gap and overlap analyses** (structured summaries with evidence lists).  
- **Recommendation rules** (which actions trigger for which gap type and confidence floor).

### Citation and disclosure requirements

Generated answers should cite, at minimum:

1. **Source** (name / kind).  
2. **Dataset** (id or title + extract).  
3. **Time period** (`dataset_period` / `market_period` or `valid_from`–`valid_to`).  
4. **Confidence level** (category from §3, e.g. `verified_statistical`, `explicit_requirement`, `inferred_pattern`, `scraped_review_signal`).  
5. **Signal nature** — whether the claim is **statistical**, **explicit** (employer/ad), **inferred**, or **review-based**.

Prefer **deterministic filters** (stage, industry, period) before semantic search. Down-rank or exclude **weak_signal** and unverified **llm_extracted** chunks for high-stakes defaults.

| Use case | Retrieval emphasis |
|----------|---------------------|
| General career advice | Statistical + curated trajectory; avoid stale aggregates |
| Job-specific advice | NAV chunks + explicit requirements + employer Spor 2 |
| Application strategy | Explicit selection + ranking data + white spots (labeled) |
| LinkedIn optimization | Overlap categories + next-step trajectory signals |
| CV generation | Explicit + statistical; inferred only with wording discipline |
| Package builder | Minimum confidence tier per module (§3) |
| Interview preparation | Selection-process overlap + case formats; review-based clearly tagged |
| Learning recommendations | Education gaps + trajectory_gaps; time-aligned market |

---

## 12. Taxonomy dependency

**SQL implementation should wait** until a **first-version taxonomy** is agreed: tables and foreign keys depend on stable codes for roles, competencies, industries, evidence types, and selection methods. Premature DDL forces expensive migrations.

### Required taxonomy documents (v1 scope)

| Taxonomy | Purpose |
|-----------|---------|
| **Role taxonomy** | Role families, seniority, mapping to ads and trajectory steps |
| **Competency taxonomy** | Shared skill nodes across Spor 1, Spor 2, NAV |
| **Industry taxonomy** | NACE subset / internal simplification for Norway |
| **Employer taxonomy** | Sector, public/private, B2B/B2C/B2G conventions |
| **Career stage taxonomy** | Stages for candidates and for path steps |
| **Evidence taxonomy** | evidence_source_type, signal_origin, confidence categories (alignment with §3) |
| **Selection-method taxonomy** | Case types, test types, interview formats |
| **Recommendation taxonomy** | Channels, action types, priority rules |

**Recommendation:** define these in a dedicated follow-up specification:

**[career-taxonomy-design.md](career-taxonomy-design.md)** — owned jointly by data and product; referenced by this dataset spec and by sokr.online RAG prompts.

---

## 13. Proposed Supabase schema (conceptual)

**No SQL below** — table-level intent and key fields only. **Do not implement until §12 taxonomies exist in draft form.**

| Table (proposed) | Purpose | Key fields (indicative) |
|------------------|---------|-------------------------|
| `sources` | Registry | `id`, `name`, `kind`, `base_url`, `access_notes`, `legal_notes`, `intelligence_layer` (spor1/spor2/nav), `source_reliability_score`, `source_refresh_frequency` |
| `datasets` | Imports | `source_id`, `external_id`, `title`, `fetched_at`, `dataset_period`, `storage_uri`, `checksum` |
| `dataset_variables` | Column dictionary | `dataset_id`, `code`, `label`, `data_type`, `taxonomy_code_id` |
| `institutions` | HE providers | `id`, `name_nb`, `orgnr`, `country_code` |
| `education_programs` | Programs | `institution_id`, `name`, `nus_code`, `level`, `duration_years` |
| `fields_of_study` | Fields | `code`, `name_nb`, `parent_id` |
| `program_field_map` | M:N | `program_id`, `field_id`, `weight` |
| `employers` | Organizations | `name`, `orgnr`, `sector`, `b2x`, `industry_id` |
| `employer_programs` | Trainee / grad programs | `employer_id`, `name`, `intake_pattern`, `source_dataset_id`, `valid_from`, `valid_to` |
| `selection_processes` / `selection_steps` | Hiring design | Step types from selection-method taxonomy |
| `job_ads` | NAV vacancies | `external_id`, `employer_id`, `title`, `published_at`, `expires_at`, `raw_ref` |
| `job_ad_requirements` | Parsed requirements | `job_ad_id`, `requirement_type`, `value`, `confidence_category`, `extraction_method`, `human_verified` |
| `competencies`, `certifications`, `education_requirements`, `role_families`, `industries` | Structure | Taxonomy-linked |
| `career_paths`, `career_path_steps`, `career_transition_signals` | Trajectories | Period + confidence on edges |
| `candidate_stages` | Reference dimension | |
| `gap_signals` / `overlap_signals` | Analytics | `gap_type` / `overlap_type`, entities, `metric`, `value`, `market_period`, `evidence_json`, `confidence_category`, `evidence_strength` |
| `white_spots` | Niches | `rule_set_id`, `white_spot_category`, `summary`, `supporting_signal_ids`, `valid_from`, `valid_to` |
| `recommendations` | Product-facing | `audience_stage`, `channel`, `priority`, `body`, `evidence_refs`, `min_confidence_tier` |
| `rag_documents` | Chunk index | `chunk_text`, `embedding` (optional), `metadata_json` (source, dataset, period, confidence, signal_nature) |

**Storage:** large raw files in Supabase Storage or external object store; tables hold pointers and metadata.

---

## 14. Import strategy (phased)

| Phase | Action |
|-------|--------|
| **Source maps** | Import **Spor 1** (Education Supply Intelligence) and **Spor 2** (Employer Demand Intelligence) source maps into `sources` / `datasets` metadata. |
| **Studiebarometeret** | Fetch survey files; register `datasets`; raw layer; **dataset_period** set. |
| **SSB** | StatBank/API extracts; version by extract date. |
| **NAV** | Wait for token; pam-stilling-feed pagination; raw JSON; **observed_at** per fetch. |
| **NAV import** | Bulk into `job_ads` / raw refs; does not block Spor 1 / Spor 2. |
| **Normalize** | Crosswalks after taxonomy v0 (§12). |
| **First analysis outputs** | Pilot gaps/overlaps with confidence + time metadata. |

**Principle:** Spor 1 and Spor 2 can advance while **NAV** (Job Market Intelligence) is pending; market slice activates when NAV data exists.

---

## 15. First implementation roadmap

| Phase | Name | Outcomes |
|-------|------|----------|
| **1** | Source map metadata import | Spor 1 + Spor 2 registry complete; NAV placeholder sources optional |
| **2** | Raw data fetch | Studiebarometeret, SSB, optional career pages; NAV when token ready |
| **3** | Normalization | Core entities + taxonomy v0 from [career-taxonomy-design.md](career-taxonomy-design.md) |
| **4** | Gap / overlap / white spot analysis | Typed signals with §3–§4 metadata |
| **5** | RAG-ready knowledge base | Chunks with mandatory citation metadata (§11) |
| **6** | sokr.online integration | API, flags, evaluation |

---

## 16. Open questions (before SQL)

1. **Identifier strategy:** NSD vs orgnr vs both; NUS revision handling.  
2. **NAV scope:** pam-stilling-feed only vs additional Arbeidsplassen surfaces.  
3. **PII and reviews:** in-scope sources; retention; aggregate-only policy.  
4. **Taxonomy authority:** internal vs ESCO/O*NET subset — resolved in [career-taxonomy-design.md](career-taxonomy-design.md).  
5. **Candidate model:** personas vs authenticated profiles — storage for candidate–role gaps.  
6. **Language:** Norwegian-only vs bilingual RAG labels.  
7. **Refresh cadence:** independent per source vs unified orchestration.  
8. **White spot rules:** ownership of thresholds and sector exceptions.  
9. **sokr.online contract:** read-only vs write-back user goals.  
10. **Hosting:** Supabase prod/staging; Storage location.  
11. **Confidence governance:** who may promote `inferred_pattern` / `llm_extracted` to default user-facing tier.  
12. **Trajectory data ethics:** which transition probabilities may be shown at individual level vs aggregate only.

---

*Document version: refined specification. Next: finalize [career-taxonomy-design.md](career-taxonomy-design.md); finalize Spor 1 / Spor 2 maps and NAV access.*

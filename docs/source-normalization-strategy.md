# Source normalization strategy

**Specification only:** no SQL, no import scripts. This document defines how **imported source-map rows** (Spor 1 / Spor 2 and similar) become **canonical intelligence entities** in the Norwegian Career Intelligence Dataset and how they relate to **signals**, **taxonomy**, and **RAG**.

**Related:** [Dataset design](education-demand-intelligence-design.md) · [MVP schema](minimum-viable-intelligence-schema.md) · [Scoring and signal model](scoring-and-signal-model.md) · [Career taxonomy](career-taxonomy-design.md)

---

## Goals and scope

Imported spreadsheets mix **real data origins** (APIs, registers, surveys) with **notes**, **section headings**, **examples**, and **empty** rows. Normalization must:

- **Classify** each row before it earns a durable `sources` / `datasets` identity.  
- **Deduplicate** the same real-world origin appearing under different names or sheets.  
- Assign **confidence**, **quality**, and **type** so downstream scoring and RAG do not over-trust weak rows.  
- Preserve **provenance** from workbook → canonical row → future **signals**.  
- Support **temporal validity** and **staleness** without losing audit history.  
- Leave room for **manual review** and **explainability** (why this row became this source).

---

## 1. Source classification model

Every imported row passes through a **classification** step before promotion to a canonical entity.

| Class | Definition | Typical spreadsheet pattern | Default action |
|-------|------------|------------------------------|----------------|
| **primary_source** | A concrete origin of data (API, file product, register, recurring survey) | Named provider + access path or stable product id | Eligible for `sources` + `datasets` |
| **aggregate_pointer** | Refers to a portal or family of tables without one stable extract | “SSB StatBank” without table id | Create or link to **parent** source + child dataset when table id appears |
| **note** | Free text, comment, or instruction | Merged cells, “TODO”, “vurder” | Store in **review queue** or `metadata` only; **no** new canonical source |
| **heading** | Section title, no URL or product | Bold row, single cell | Skip or attach to **parent section** in metadata |
| **example** | Illustrative, not authoritative | “Eksempel: …” | Tag `example=true`; do not merge into production sources without review |
| **duplicate_candidate** | Same real origin as another row (different wording) | Same URL, same orgnr, same API base | Route to **deduplication** (§4) |
| **unknown** | Insufficient fields to classify | Empty key columns | **Hold** for manual classification or reject |

**Output of classification:** `row_class`, `classification_confidence`, `review_required` (boolean), and optional `linked_canonical_source_id` after merge.

---

## 2. Canonical source rules

A **canonical source** is the stable registry entry representing one real-world origin.

### Promotion criteria (all must be satisfiable for auto-promotion)

1. **Identity:** at least one strong key — e.g. canonical URL, official API base URL, orgnr + product code, or agreed internal `source_key` from the map.  
2. **Intent:** row classified as `primary_source` or resolvable `aggregate_pointer` with a child identifier.  
3. **Layer:** `intelligence_layer` set (`education_supply`, `employer_demand`, `job_market`, …) consistent with Spor 1 / Spor 2 / NAV rules.  
4. **No contradiction:** not flagged as `example` + production at the same time without explicit `include_in_production` flag after review.

### Canonical slug / id rules

- **Stable slug:** derived from normalized URL host + path, or from `provider + product_id`, never from volatile row order.  
- **Collision:** if slug collides, **merge** candidates (§4) or append a **versioned suffix** only after human or rule confirms they are distinct origins.  
- **IDs:** database UUIDs remain internal; **canonical business key** (slug or `external_registry_id`) is what humans and RAG cite for “same source over time.”

### Headings and notes

- **Do not** create a `sources` row for a heading alone.  
- Optionally store headings as **breadcrumb** in `metadata.section_path[]` on the **next** valid data row in the same sheet.

---

## 3. Dataset normalization

A **dataset** is a **specific extractable** or **versioned product** under a source (one SSB table, one Studiebarometeret wave file, one NAV feed product).

| Concept | Rule |
|---------|------|
| **One source, many datasets** | e.g. SSB = one `source`; each StatBank table = one `dataset` with stable `external_id` (table id). |
| **One row → source only** | If the map row has no sub-product, create a **default dataset** (e.g. `{slug}::primary`) with `ingestion_status` pending until a real extract exists. |
| **Titles** | `dataset.title` = human label from map; must not duplicate a different `external_id` under the same source. |
| **Access** | `access_method` from map (`api`, `file`, `scrape`, `survey`) aligned with `sources.kind` where possible. |
| **Raw vs normalized** | Raw workbook row lives in `metadata.raw_row` on the first import; **normalized** fields (`title`, `external_id`, `access_method`) are the maintained contract for pipelines. |

---

## 4. Deduplication strategy

Duplicates arise from **same URL**, **same API**, **same Norwegian name + org**, or **copy-paste** across sheets.

| Stage | Method |
|-------|--------|
| **Blocking keys** | Normalize URL (strip tracking params, lower host), normalize orgnr (9 digits), normalize whitespace in titles. |
| **Fuzzy match** | Optional: string similarity on title within same `intelligence_layer` and same `kind` — only produces **duplicate_candidate**, not auto-merge, above a threshold. |
| **Merge rule** | If two rows map to same canonical key: **one** `sources` row; merge `metadata.import_history[]` (list of workbook, sheet, row, import batch id); keep **oldest** `created_at` semantics via audit table or metadata. |
| **Split rule** | Same org but **different** products (different table ids) → **two** datasets under one source, never two sources unless products are independently governed. |

**Explainability:** merged rows record `merge_decision` (`auto_key_match` | `human_merge`) and contributing row ids in metadata.

---

## 5. Alias strategy

| Alias type | Handling |
|------------|----------|
| **Name variants** | “NIFU”, “Norsk institutt for studier av innovasjon…” → single canonical source with `aliases[]` in metadata or a future `source_aliases` table. |
| **URL variants** | `http` vs `https`, `www` vs bare host → normalized URL as primary key for matching; store raw URL in metadata. |
| **Language** | Norwegian primary label; English secondary for RAG and international tools. |

Aliases affect **matching** and **RAG retrieval** (synonym expansion), not the **internal UUID** of the canonical row once created.

---

## 6. Confidence assignment

Source-level confidence is **orthogonal** to row-level spreadsheet noise. Align categories with [Scoring and signal model](scoring-and-signal-model.md) where helpful.

| Level | When to assign |
|-------|----------------|
| **High** | Official URL, government API, documented product id in map |
| **Medium** | Secondary documentation, aggregator link without stable API |
| **Low** | Heuristic classification, inferred from context, or incomplete URL |
| **Review** | `duplicate_candidate`, fuzzy match, or conflicting fields |

**Source reliability score** (0–1 when used): combine **publisher trust** (registry tier), **access stability** (HTTPS, auth documented), and **maintenance** (update frequency known). Initial values may be **manual defaults per publisher type**, then refined.

**Dataset / version confidence:** first import from map only → `pending` / low; rises when a successful **fetch** validates schema and checksum.

---

## 7. Temporal handling

| Field / concept | Use |
|-----------------|-----|
| **`valid_from` / `valid_to`** | When this source or dataset **describes** valid use (e.g. API version sunset). |
| **`dataset_versions.period_*`** | What period the **data** covers, not when the row was typed. |
| **`observed_at` / `fetched_at`** | When the system last saw metadata or payload. |
| **`stale_after`** | Policy: e.g. NAV-related pointers stale quickly; NIFU report URLs slower. |

**Stale sources:** do not delete; mark `is_active=false` or lower reliability; emit **signals** with `confidence_category=weak_signal` when downstream uses stale metadata.

---

## 8. Human review workflow

| Queue | Trigger |
|-------|---------|
| **Classification** | `unknown`, `heading` misclassified as source, mixed signals in one row |
| **Dedup** | `duplicate_candidate` with fuzzy score in band |
| **Quality** | `example` row mistakenly in production sheet |
| **Legal** | License ambiguous, scraping restricted |

**Workflow steps:** (1) row appears in **review UI** or export with `review_required=true`; (2) reviewer sets **decision** (`approve` | `merge_into` | `reject` | `reclassify`); (3) system writes **audit** entry (who, when, from_state, to_state); (4) canonical rows updated idempotently.

**SLA:** MVP can be **async** (weekly batch); production may need **SLA by layer** (job market changes faster).

---

## 9. Provenance requirements

Every canonical **source** and **dataset** must be able to answer:

1. **Which workbook** (filename, version, checksum)?  
2. **Which sheet and row** (or cell range)?  
3. **Which import batch** (`import_batch_id`, timestamp, tool version)?  
4. **What was the raw row** (`raw_row` in JSONB)?  
5. **What normalization rules** produced the current fields (`normalization_rule_version`)?

**Immutable raw:** keep workbook file in object storage or versioned archive; DB holds pointer + checksum. **Normalized row** may be updated; **audit log** or `metadata.import_history` preserves prior states for explainability.

---

## 10. Relationship to signals and taxonomy

| Link | Description |
|------|-------------|
| **Signals** | After fetch/parsing, **signals** attach to `dataset_version_id` and optionally `subject_type=source`. Source metadata **does not** replace signal extraction; it **scopes** confidence. |
| **Taxonomy** | Sources do not replace role/competency taxonomy; **publisher type** and **industry hints** from the map may tag **metadata** used later to weight signals (e.g. employer-type priors). |
| **Gaps / overlaps** | Market gaps use **dataset_versions** with good temporal alignment; source quality caps contribution. |

**Pipeline order:** import row → classify → canonical source/dataset → (later) fetch → `dataset_versions` → **signals** → gaps/overlaps.

---

## 11. RAG implications

| Topic | Guidance |
|-------|----------|
| **Retrieval** | Prefer chunks tied to **high-confidence** sources and **recent** `dataset_versions`. |
| **Citations** | Answers cite **canonical source name + slug** and **import provenance** when the claim comes from the map itself vs from fetched data. |
| **Examples** | Rows tagged `example` must be **excluded** from default RAG corpus or clearly labeled in chunk metadata. |
| **Headings** | Never surface as factual “sources”; may appear only as navigational context if stored at all. |

---

## 12. Future automation strategy

| Phase | Automation |
|-------|------------|
| **Now (MVP)** | Rule-based classification + stable keys + review queue; manual merge for dupes. |
| **Next** | LLM-assisted **classification only** (propose `row_class`), always with **human approval** for production promotion. |
| **Later** | Learned duplicate detection from merge history; auto-suggest merges above precision threshold. |
| **Continuous** | Scheduled **re-validation** of URLs (HEAD/GET), **staleness** jobs, and **diff** of new workbook versions against prior import. |

**Guardrails:** automation never removes provenance; it never lowers **confidence** without audit; it never merges across **intelligence_layer** without explicit rule.

---

## Summary

Imported source-map rows are **classified** before they become canonical **`sources`** and **`datasets`**. **Canonical identity** rests on stable keys (URL, product id, orgnr), not on row order or headings. **Deduplication** merges same-origin rows while preserving **import history**; **aliases** support matching and RAG. **Confidence** and **quality** reflect evidence strength and publisher trust; **time** fields separate data period from observation time. **Manual review** handles unknowns, fuzzy dupes, and legal ambiguity. **Provenance** (workbook, sheet, row, batch, raw row, rule version) is mandatory for explainability and for linking to **signals** and later **RAG** without conflating **raw map text** with **verified fetches**.

# Canonical source review workflow

**Specification only:** no SQL, no frontend, no import scripts. This document defines the **first operational review workflow** for turning **imported source-map rows** into **canonical intelligence sources and datasets** (Spor 1 / Spor 2 and equivalent imports).

**Related:** [Source normalization strategy](source-normalization-strategy.md) · [Dataset design](education-demand-intelligence-design.md) · [MVP schema](minimum-viable-intelligence-schema.md) · [Scoring and signal model](scoring-and-signal-model.md)

---

## 1. Review goals

| Goal | Operational meaning |
|------|---------------------|
| **Correctness** | Only real, actionable data origins become production `sources` / `datasets`. |
| **Safety** | Headings, notes, and examples do not pollute canonical registries or downstream RAG. |
| **Uniqueness** | Duplicates are merged or explicitly split with documented rationale. |
| **Trust** | Confidence and source quality reflect reviewer judgment and evidence, not only spreadsheet layout. |
| **Auditability** | Every promotion, rejection, merge, and confidence change is reconstructable for compliance and debugging. |
| **Explainability** | Product and data teams can answer “why is this source in the catalog?” from review artifacts. |
| **Throughput** | Queues and states make it clear what to do next without ad-hoc spreadsheets replacing the system of record. |

---

## 2. Reviewer workflow

**Actors:** *Reviewer* (domain/data steward), *Lead reviewer* (escalation / merge across layers), optional *Legal* for restricted sources.

**High-level steps**

1. **Pick work** from a **review queue** (filtered by `intelligence_layer`, age, or priority).  
2. **Open item** — see raw row, sheet context, auto-classification, and suggested canonical match (if any).  
3. **Decide** — approve, reject, reclassify, merge, split, or escalate (see §3, §4–§7).  
4. **Adjust** — optional confidence and quality fields (§8–§9).  
5. **Submit** — system applies canonical changes, **preserves provenance** (§10), and moves item to terminal state.  
6. **Monitor** — periodic sweep of **stale** or **deprecated** items (§13 in spirit; see normalization doc for time fields).

**Cadence (MVP):** batch review (e.g. weekly); **production** may add SLAs per queue.

---

## 3. Review states

States apply to a **review item** (one imported row or one **duplicate cluster**). Names are logical; implementation may map to enums or tables later.

| State | Meaning |
|-------|---------|
| `pending_classification` | Import succeeded; automation could not auto-promote. |
| `pending_review` | In queue for human decision. |
| `in_review` | Checked out by a reviewer (optional lock to avoid double work). |
| `approved` | Promoted to canonical source/dataset (or linked to existing). |
| `rejected` | Not a canonical source; heading/note/example/trash. |
| `merged` | Combined into another canonical record; original row retained in provenance only. |
| `split_required` | One row must become multiple datasets/sources; follow-up items spawned. |
| `escalated` | Awaiting lead or legal. |
| `deferred` | Legitimate but not now; revisit date set. |

**Terminal states:** `approved`, `rejected`, `merged` (with merge target resolved), or `deferred` with explicit policy.

---

## 4. Approval criteria

Approve as **canonical** (new or linked existing) when **all** applicable checks pass:

1. **Identity:** stable key present (normalized URL, official API base + product id, register id, or agreed internal key).  
2. **Purpose:** row describes an actual **data origin** used or planned for intelligence (not-only narrative).  
3. **Layer:** `education_supply` / `employer_demand` / `job_market` (etc.) is correct for Spor 1 / 2 / NAV alignment.  
4. **Legal / access:** no red flags in `license_notes` / access method; scraping or ToS issues resolved or documented as internal-only.  
5. **Duplicates:** either no duplicate candidate, or merge decision completed with target chosen.  
6. **Provenance:** workbook id, sheet, row, import batch id captured (cannot approve without trace).

**Fast-track approve:** high-confidence auto-classification + strong key (e.g. known `ssb.no` table URL) may skip full queue via policy — still logged as `auto_approved_with_review_sampled` if used.

---

## 5. Rejection criteria

Reject (no canonical source, or remove from production consideration) when **any** applies:

| Criterion | Examples |
|-----------|----------|
| **Structural** | Section heading, empty row, merged title only. |
| **Instructional** | “Se NIFU-rapport”, internal TODO, methodology note without a fetchable product. |
| **Example** | Explicitly marked example or illustrative URL not intended for production. |
| **Duplicate absorbed** | Row merged into another canonical source; this row is “rejected as duplicate” with **merge target** recorded (distinct from trash rejection). |
| **Out of scope** | Non-Norwegian or wrong program scope per project rules. |
| **Unrecoverable** | No way to obtain data legally/technically after due diligence. |

**Rejection must record:** reason code, free-text note, reviewer id, timestamp — **never** delete raw import row from cold storage; only mark review state and optionally hide from default catalog UI.

---

## 6. Merge workflow

**When:** duplicate detection or reviewer identifies same origin under two rows/slugs.

**Steps**

1. **Open duplicate cluster** (side-by-side rows + auto diff of URL, title, org).  
2. **Choose survivor:** canonical `source` (and primary `dataset` if applicable) that remains the **merge target**.  
3. **Merge action:**  
   - Re-point or alias secondary identifiers to survivor.  
   - Append to survivor’s **import history** / provenance list (all workbook rows + batches).  
   - Mark non-survivor review items `merged` with `merge_target_id`.  
4. **Datasets:** if both rows implied different **products** (e.g. two SSB tables), **do not** merge sources — split datasets under one source instead (escalate if unclear).  
5. **Signals / versions:** existing `dataset_versions` tied to merged-away ids — policy: **relink** to survivor’s dataset where safe; else **freeze** with deprecation note (no silent orphan).

**Explainability:** UI or export shows “Merged A → B because: same API base + same table id.”

---

## 7. Duplicate workflow

| Stage | Action |
|-------|--------|
| **Detection** | Automated blocking keys (normalized URL, orgnr, product id); fuzzy suggestions for human only. |
| **Triage** | Reviewer confirms `duplicate_of` or `not_duplicate` (false positive). |
| **Resolution** | Merge (§6) or **keep separate** with mandatory note (“different API keys / different legal agreements”). |
| **Prevention** | After resolution, **alias** or **matching rule** stored so next import auto-links to canonical row. |

**False negative:** later import creates new duplicate — periodic **dedup job** reopens cluster.

---

## 8. Confidence adjustment workflow

| Trigger | Allowed adjustment |
|---------|---------------------|
| **Approve high-trust origin** | Raise `confidence_category` / reliability toward documented ceiling for that publisher tier. |
| **Incomplete map row** | Keep medium/low until fetch validates; document in review note. |
| **Post-fetch validation** | Separate pipeline may raise dataset-level confidence; reviewer can **confirm** or **dispute**. |
| **Downgrade** | Misidentified row, broken URL, deprecated API — lower confidence or mark `stale` / `deprecated` (see normalization strategy for temporal fields). |

**Rules:** Reviewers cannot set “verified_statistical” without actual statistical extract; they can set **source-level** trust for **registry** and **official** publishers per policy table.

**Audit:** old and new confidence values, reason code, reviewer, time.

---

## 9. Source quality grading

**Quality grade** (orthogonal to confidence) captures **maintenance**, **documentation**, and **fit for automated ingestion**.

| Grade | Meaning | Typical effect |
|-------|---------|----------------|
| **A** | Official API/docs, stable ids, clear license | Full automation, high RAG rank. |
| **B** | Good but manual steps or occasional format drift | Automated with monitoring. |
| **C** | Scraping, HTML-only, or fragile | Human-in-loop fetch; lower RAG default rank. |
| **D** | Deprecated, legal risk, or one-off export | Block from default pipelines; archive only. |

**Assignment:** reviewer at approval time; **re-grade** on incident (site redesign, 403 spike) via queue or job.

---

## 10. Audit trail requirements

Every **state transition** and **canonical mutation** must log:

| Field | Required |
|-------|----------|
| **review_item_id** | Stable id for this review unit. |
| **from_state** / **to_state** | State machine. |
| **action** | approve \| reject \| merge \| split \| escalate \| confidence_change \| quality_change \| reclassify. |
| **actor** | User id or `system` + rule id for automation. |
| **timestamp** | UTC. |
| **payload** | JSON: merge target, reason codes, old/new confidence, snapshot ids to raw row. |
| **immutable raw reference** | Pointer to stored workbook version + row coordinates. |

**Retention:** align with org policy; minimum **life of dataset** plus N years for career intelligence use cases.

---

## 11. Human vs AI responsibilities

| Responsibility | Human | AI (future) |
|------------------|-------|-------------|
| **Legal / ToS** | Owns decision | May flag clauses; never auto-approve restricted scraping. |
| **Merge / split** | Owns final merge target and split boundaries | May suggest candidates with similarity scores. |
| **Classification** | Resolves `unknown`, ambiguous headings | May propose `row_class` with confidence. |
| **Confidence / quality** | Sets policy exceptions and publisher tiers | May propose defaults from publisher type. |
| **Escalation** | Lead/legal decides | Routes queue by rules only. |
| **Provenance** | Ensures business sign-off on “what we ship” | Appends technical metadata only. |

**Principle:** AI **proposes**; humans **dispose** for anything that affects production catalog or public-facing RAG until policy explicitly allows bounded auto-merge (see §12).

---

## 12. Future automation roadmap

| Phase | Scope |
|-------|--------|
| **Phase 0 (now)** | Manual queues; auto-classification to `pending_review` only. |
| **Phase 1** | AI-suggested labels + duplicate clusters; single-click accept/reject in UI. |
| **Phase 2** | Auto-approve only for **strict** key matches (e.g. exact normalized URL + known publisher allowlist) with **100%** audit log and **sampled** human audit. |
| **Phase 3** | Scheduled **staleness** and **re-review** jobs; auto-deprecate with human notification. |
| **Phase 4** | Cross-project learning (merge patterns) — **privacy and governance** review first. |

**Non-goals until governance exists:** silent merge of fuzzy duplicates; AI-only rejection of borderline rows without human appeal path.

---

## Escalation rules (operational)

| Condition | Route to |
|-----------|----------|
| Cross-layer ambiguity (Spor 1 vs Spor 2) | Lead reviewer. |
| Legal / personal data in source description | Legal + data protection. |
| Technical blocker (auth, contract) | Product + data engineering. |
| Disagreement between two reviewers | Lead reviewer; third opinion if needed. |
| SLA breach (e.g. NAV-dependent release) | PM + lead — may allow **time-boxed** provisional approve with `quality=C` and visible banner in catalog. |

---

## Stale and deprecated handling (review lens)

| Situation | Reviewer action |
|-----------|-----------------|
| **URL dead** | Mark source or dataset **deprecated**; set `valid_to`; open task for replacement URL. |
| **Product retired** | Same; link **successor** source/dataset if known. |
| **Re-import shows drift** | Diff review item: approve update to metadata or merge with new row. |

**Provenance:** deprecation never removes historical audit or raw files.

---

## Explainability (product-facing)

For any approved canonical source, an internal or admin view should show:

- Original **raw row** (redacted if PII).  
- **Classification path** (auto + human edits).  
- **Merge/reject** history.  
- **Confidence and quality** timeline.  
- **Reviewer identities** (or service accounts) for accountability.

This mirrors explainability expectations for **signals** and **RAG** in the broader architecture.

---

## Summary

The first operational workflow centers on **queues**, **clear states**, and **reviewer actions** (approve, reject, merge, split, escalate, adjust confidence/quality) with **strict provenance** and an **append-only audit trail**. **Headings and notes** are rejected or reclassified, not promoted. **Duplicates** flow through detection → triage → merge or documented separation. **Confidence** and **quality** are human-governed with policy ceilings; **stale** and **deprecated** sources are handled without erasing history. **AI** assists with suggestions only until later phases allow **bounded** automation with full auditability.

# First manual extraction playbook

**Specification only:** no SQL, no frontend, no automation scripts. This is an operational intelligence playbook for the first human-in-the-loop extraction wave in the Norwegian Career Intelligence Dataset and **sokr.online**.

**Priority order for this playbook:** explainability, semantic consistency, provenance, confidence accuracy, reviewer trust, extraction quality, repeatability, auditability.  
**Not optimized for this phase:** speed, scale, automation, throughput.

**Related:** [First intelligence extraction MVP](first-intelligence-extraction-mvp.md) · [Source normalization strategy](source-normalization-strategy.md) · [Canonical source review workflow](canonical-source-review-workflow.md) · [Career taxonomy](career-taxonomy-design.md) · [Scoring and signal model](scoring-and-signal-model.md) · [MVP schema](minimum-viable-intelligence-schema.md)

---

## 1. Purpose of the playbook

Manual extraction exists because the MVP needs **correct semantic decisions**, not just text parsing. Early intelligence output must be trusted by reviewers before any broad automation is introduced.

### Why human review is critical in MVP

- Source-map rows mix true sources and noise (headings, notes, examples).  
- The same employer concept appears under multiple labels/languages.  
- Confidence mistakes create false certainty and bad recommendations.  
- Explainability requires reviewer-readable rationale, not model-only outputs.

### Why semantic consistency and explainability matter

The same phrase must map consistently to taxonomy and signal types, and each mapping must be traceable to source text and review decisions. If two reviewers cannot explain a row the same way, the system is not ready to scale.

### Why controlled extraction over mass ingestion

Controlled extraction reveals failure modes early:

- taxonomy drift,  
- over-tagging,  
- confidence inflation,  
- contradictory signals,  
- noisy recommendations.

### What this playbook operationalizes

- taxonomy usage,  
- signal extraction and lineage,  
- confidence assignment,  
- gap / overlap evaluation,  
- recommendation drafting,  
- explainability writing,  
- canonical source decisions.

---

## 2. Scope of the first manual extraction wave

### Included source types

| Source type | Included in wave 1 | Why |
|-------------|--------------------|-----|
| Trainee programs | Yes | Explicit criteria and selection language |
| Employer criteria pages | Yes | High-value requirement signals |
| NIFU findings | Yes (selected) | Structured, high-trust supply-side indicators |
| Studiebarometeret | Yes (selected) | Student perspective and program signals |
| Selected university career services | Yes | Career preparation language for gap discovery |

### Excluded source types

| Source type | Excluded now | Why excluded in MVP |
|-------------|--------------|---------------------|
| NAV bulk ads | Yes | Volume + token + parsing complexity before quality loop stabilizes |
| Large-scale scraping | Yes | Legal/quality burden too high for first wave |
| User-generated content | Yes | Reliability and moderation risk |
| Generalized web search | Yes | Weak provenance and unstable reproducibility |
| Embeddings / vector systems | Yes | Retrieval quality depends on stable manual semantics first |

---

## 3. Extraction workflow overview

### End-to-end stages

| Stage | Required input | Expected output | Reviewer responsibility | Audit requirement |
|-------|----------------|-----------------|-------------------------|-------------------|
| 1. Source selection | Approved wave scope | Source shortlist | Choose in-scope items only | Selection rationale |
| 2. Canonical source review | Raw source row | Canonical source decision | Approve/reject/merge/split | Decision + reason code |
| 3. Extraction preparation | Source page/file snapshot | Ready extraction packet | Verify snapshot/date/version | Snapshot reference |
| 4. Entity extraction | Prepared packet | Entity candidates | Extract atomic entities | Source excerpt links |
| 5. Signal extraction | Entity candidates + text | Signal candidates | Create explicit/inferred signals | signal provenance |
| 6. Taxonomy mapping | Candidates + taxonomy | Mapped taxonomy ids | Resolve synonyms/ambiguity | Mapping rationale |
| 7. Confidence assignment | Signals + provenance | confidence_category/score | Apply rules + caps | Confidence decision log |
| 8. Gap/overlap evaluation | Candidate + target context | Gap/overlap outputs | Score with constraints | Contributing signals list |
| 9. Recommendation drafting | Gaps/overlaps | Recommendation draft | Actionable, non-generic advice | Trigger references |
| 10. Explainability review | Draft outputs | Explainability notes | Validate traceability wording | Explanation chain |
| 11. Human QA | Full extraction case | QA pass/fail | Spot-check consistency | QA checklist + issues |
| 12. Approval/escalation | QA outcome | Published or escalated case | Final decision authority | State transition record |

---

## 4. Reviewer roles and responsibilities

| Role | Core responsibilities | Approval authority | Allowed modifications | Escalation rules |
|------|------------------------|--------------------|-----------------------|------------------|
| **Extractor** | Parse source, draft entities/signals, draft confidence and recommendations | None (draft only) | Add/edit draft records and notes | Escalate ambiguity/conflicts |
| **Reviewer** | Validate extraction correctness and provenance | Approve standard items | Adjust mappings, confidence within policy | Escalate taxonomy/legal conflicts |
| **Taxonomy reviewer** | Resolve role/competency/industry mapping disputes | Approve taxonomy decisions | Add alias, choose canonical node (per policy) | Escalate if taxonomy lacks node |
| **Confidence reviewer** | Validate confidence category/score and caps | Approve confidence upgrades | Downgrade or hold inferred states | Escalate if evidence insufficient |
| **Escalation reviewer (lead)** | Resolve unresolved conflicts, merge decisions, edge cases | Final decision on escalated items | Override with documented rationale | Route legal/compliance issues |

### Governance rules

- Extraction and approval should be separated for critical cases.  
- Reviewer independence: second reviewer for high-impact outputs.  
- Every role action must be attributable (`reviewer_id`, timestamp, before/after state).

---

## 5. Source preparation workflow

### Steps

1. Intake source candidate.  
2. Classify source row (`primary_source`, `heading`, `note`, `example`, etc.).  
3. Canonical lookup and duplicate check.  
4. Register provenance (source row, sheet, snapshot).  
5. Assign initial source quality grade.  
6. Decide: proceed, defer, reject, or escalate.

### Required metadata

- source label and canonical slug candidate,  
- source URL or file pointer,  
- intelligence layer,  
- collection date,  
- snapshot reference,  
- raw source-map row reference,  
- reviewer notes.

### Source-quality rubric (MVP)

| Grade | Meaning | Typical action |
|-------|---------|----------------|
| A | Official structured source, stable and well-documented | Preferred for core extraction |
| B | Good source with minor instability | Allowed with routine checks |
| C | Fragile/semi-structured source | Allowed with stricter review |
| D | High risk, unclear legality, or poor reliability | Do not use in wave 1 |

### Stale/unsupported handling

- **Stale source:** keep provenance, mark stale, reduce confidence for derived signals.  
- **Unsupported source type:** reject from MVP wave and document reason.

---

## 6. Entity extraction workflow

### Target entities for manual extraction

- role families  
- competencies  
- industries  
- employer types  
- evidence types  
- selection methods  
- recommendation types  
- trajectory indicators

### Operational rules by entity

| Entity | Extraction rule | Normalization rule | Ambiguity handling | Multilingual/synonyms |
|--------|------------------|--------------------|--------------------|-----------------------|
| Role family | Map title/process context to top-level family | Use canonical `role_family` id | Multi-label allowed; mark primary | NO/EN aliases in notes |
| Competency | Extract atomic capability phrases | Map to canonical competency node | If uncertain, mark `inferred` + review | Map “stakeholder mgmt” variants together |
| Industry | Use explicit employer context first | Canonical industry id | Unknown if no clear context | Norwegian label primary |
| Employer type | Infer from organization profile + explicit claims | Choose from controlled taxonomy | Escalate if mixed profile | Alias allowed, not new type |
| Evidence type | Identify proof pattern in text/profile | Use controlled evidence taxonomy | Do not invent evidence categories | Keep examples bilingual if needed |
| Selection method | Extract explicit hiring steps/tests | Map to selection-method taxonomy | Distinguish explicit vs implied | English interview terms allowed |
| Recommendation type | Map action to predefined categories | Use recommendation taxonomy | Reject generic advice | Synonyms in UX copy only |
| Trajectory indicators | Extract progression hints | Link to trajectory signal type | Keep inferred if evidence thin | NO/EN role title variants normalized |

### Examples

- “Assessment center + case” -> selection methods: `assessment_center`, `case_interview`.  
- “Samarbeid på tvers av funksjoner” -> competency: cross-functional collaboration node.  
- “Graduate program for consulting track” -> role family + trajectory indicator.

---

## 7. Signal extraction workflow

### What qualifies as a signal

- Explicit requirement or selection criterion.  
- Explicit preference tied to a role or employer.  
- Repeated pattern observed across multiple approved sources.  
- Statistical statement from trusted structured source (NIFU/Studiebarometeret where applicable).

### What must NOT become a signal

- Headings, notes, examples.  
- Generic marketing fluff without actionable semantics.  
- Unverifiable opinion text in MVP.  
- AI guess without source excerpt and human review.

### Signal categories in practice

| Category | Typical extraction decision |
|----------|-----------------------------|
| Explicit | Direct quote or structured bullet -> high confidence category |
| Inferred | Contextual interpretation -> capped confidence |
| Repeated pattern | Multiple weak/medium signals converge -> stronger but still bounded |
| Statistical | Verified aggregate with period and provenance |

### Examples

| Text | Signal decision |
|------|------------------|
| “Quantified results required” | Explicit evidence signal requirement |
| “Case interview emphasized” | Selection signal (explicit or inferred based on wording) |
| “Stakeholder management important” | Competency signal; may remain inferred if vague |
| “Cross-functional collaboration valued” | Soft/competency signal with medium confidence cap |

### Weak, repeated, contradictory

- Weak signals can be stored but should not trigger critical recommendations alone.  
- Repeated weak signals can be upgraded to repeated-pattern class if independent and consistent.  
- Contradictory signals must be linked and reviewed, not silently averaged away.

---

## 8. Required fields per extracted signal

### Minimum field set

| Field | Mandatory | Why it exists |
|-------|-----------|---------------|
| `signal_type` | Yes | Core signal semantics |
| `signal_label` | Yes | Human-readable extraction summary |
| `source_id` | Yes | Canonical source trace |
| `dataset_id` | Yes | Dataset-level provenance |
| `extraction_method` | Yes | Manual rule, template, assisted extraction, etc. |
| `confidence_category` | Yes | Trust class |
| `confidence_score` | Yes (MVP default allowed) | Relative ranking and caps |
| `evidence_strength` | Yes | Support strength for scoring |
| `observed_at` | Yes | Temporal anchor |
| `valid_from` | Optional | Known validity start |
| `valid_to` | Optional | Known validity end |
| `explainability_note` | Yes | Short “why this signal exists” |
| `reviewer_id` | Yes | Accountability |
| `review_status` | Yes | Draft/reviewed/approved/escalated |
| `contributing_text` | Yes | Source excerpt used for extraction |
| `taxonomy_links` | Yes | Linked taxonomy ids/nodes |

### Auditability expectations

- Mandatory fields must exist before approval.  
- Optional temporal fields become mandatory when source explicitly includes dates.  
- `contributing_text` must be precise enough for independent verification.

---

## 9. Confidence assignment workflow

### Allowed categories (MVP)

- `verified_statistical`  
- `explicit_requirement`  
- `explicit_selection_criterion`  
- `inferred_pattern`  
- `candidate_claim`  
- `weak_signal`  
- restricted use: `llm_extracted` (requires human confirmation to promote)

### Rules

| Rule type | Workflow rule |
|-----------|----------------|
| Downgrade | Missing provenance, stale source, contradictory evidence, vague wording |
| Escalation | Potential overclaim, new taxonomy mapping, legal-sensitive source |
| Caps | Inferred/weak signals cannot be sole basis for critical blockers |
| Conflicts | Keep both with notes + escalation if decision impacts recommendations |

### Anti-patterns

- Assigning explicit confidence from implied wording.  
- Inflating score because source “sounds trustworthy” without evidence link.  
- Upgrading inferred signal to explicit to satisfy expected recommendation.

### No-fake-certainty rules

- If evidence is ambiguous, keep `inferred_pattern` or `weak_signal`.  
- If reviewer confidence disagrees with extractor and no decisive proof exists, escalate and retain lower category.  
- Never publish “required” language without explicit textual support.

---

## 10. Overlap evaluation workflow

### Overlap categories

- competency overlap  
- role overlap  
- industry overlap  
- employer overlap  
- trajectory overlap

### Evaluation approach

| Concept | Reviewer guidance |
|--------|--------------------|
| Partial overlap | Accept partial match with transparent score band |
| Asymmetrical overlap | Candidate->target can differ from target->candidate fit |
| Transferable competencies | Allow mapped transfer edges only if taxonomy supports it |
| Weak overlaps | Flag as exploratory; do not over-prioritize |
| False overlaps | Remove if match is superficial keyword coincidence |

### Examples

- Finance analyst -> fintech product analyst: medium competency overlap, moderate industry overlap, trajectory dependent.  
- PM -> operations manager: high process overlap, medium role overlap, evidence asymmetry common.

---

## 11. Gap evaluation workflow

### Gap categories and constraints

| Gap | Required evidence | Acceptable inference | Scoring constraints | Confidence handling | Explainability requirement |
|-----|-------------------|----------------------|---------------------|---------------------|----------------------------|
| Competency gap | Target competency + candidate signals | Yes, limited | No critical flag from inferred-only target | Cap by confidence | Missing competencies + source references |
| Evidence gap | Required evidence type absent | Yes | Prefer explicit employer needs | Confidence from requirement side | Specify missing evidence type |
| Certification gap | Explicit cert mention | Minimal inference | High only when explicit | Strong confidence if explicit | Quote cert requirement |
| Process gap | Selection method vs prep evidence | Some | Must reference process source | inferred process capped | Show missing step prep |
| Positioning gap | Narrative mismatch | Yes (reviewed) | Qualitative band only in MVP | medium/low default | Plain-language rationale |
| Trajectory gap | Next-step indicators | Yes, usually inferred | Soft recommendation bias | generally capped | Mark as trajectory assumption |
| Network gap | Sector referral pattern | Limited | Never sole hard blocker | weak/medium max | disclose uncertainty |
| Industry gap | Industry signal mismatch | Yes | Use coarse taxonomy only | capped when low n | Show target industry basis |

### Examples

- “Case interview expected, no case evidence” -> process gap.  
- “Leadership target role, no team-impact evidence” -> evidence + trajectory gap.  
- “Public-sector profile targeting startup GTM role” -> positioning + industry gap with moderate confidence.

---

## 12. Recommendation drafting workflow

### Creation steps

1. Start from approved gaps/overlaps.  
2. Map to recommendation type taxonomy.  
3. Draft action in concrete, testable terms.  
4. Attach trace links to trigger signals/gaps.  
5. Assign recommendation confidence and priority class.  
6. Submit for recommendation review.

### Constraints

- Must map to at least one gap or overlap rationale.  
- Must be specific enough to execute (what, how, expected outcome).  
- Must not be generic advice detached from extracted evidence.

### Examples

| Recommendation | Valid trigger basis |
|----------------|---------------------|
| Improve quantified evidence | Evidence gap + explicit employer/results language |
| Improve role alignment | Positioning gap + low role overlap |
| Strengthen leadership evidence | Trajectory/competency gap toward leadership role |
| Improve interview preparation | Process gap for case/panel/technical assignment |
| Improve networking strategy | Network gap with sector-specific support |

### Traceability requirement

Each recommendation includes:

- `trigger_gap_ids` / `trigger_overlap_ids`,  
- confidence statement,  
- short explainability note linked to source-derived signals.

---

## 13. Explainability-writing workflow

### Required explainability components

| Component | Requirement |
|-----------|-------------|
| Provenance reference | Name source + dataset/version reference |
| Confidence wording | Plain-language reason for confidence level |
| Signal lineage | Show upstream signals and key links |
| Uncertainty communication | Explicitly state assumptions/inferred parts |
| Evidence references | Include the specific text excerpt basis |

### Good vs poor explainability

| Type | Example |
|------|---------|
| Good | “Recommendation is based on employer criteria page (dataset v2026-05), which explicitly lists case interview and quantitative problem-solving. Candidate profile lacks case evidence.” |
| Poor | “You should practice interviews because it seems important.” |

### Tone and constraints

- Tone: factual, clear, non-accusatory.  
- No fabricated certainty.  
- No implied source claims not present in extracted evidence.  
- If unknown, say unknown and route to review.

### Anti-hallucination rules

- Never infer missing source text.  
- Never convert probable into required wording.  
- Never hide low confidence behind confident language.

---

## 14. Canonical merge workflow

### Steps

1. Detect duplicate candidates.  
2. Compare canonical keys (URL/product identifiers).  
3. Select survivor source/dataset.  
4. Retain aliases for merged items.  
5. Preserve full provenance from all merged rows.  
6. Record merge rationale and approval.

### Merge approval requirements

- Reviewer + escalation reviewer approval for ambiguous merges.  
- Merge rationale note required.  
- No cross-layer merge (e.g., education_supply vs employer_demand) without escalation.

### Rollback and audit

- Merge decisions must be reversible through audit trail and alias history.  
- Rollback requires explicit reason and reviewer signoff.

---

## 15. Conflict handling and escalation

| Conflict type | Escalation path | Resolution requirement | Documentation |
|---------------|------------------|------------------------|---------------|
| Taxonomy conflict | Taxonomy reviewer -> escalation reviewer | Canonical node decision or new-node request | Mapping notes + decision id |
| Confidence conflict | Confidence reviewer -> escalation reviewer | Agreed category/score or “inferred” hold | Before/after confidence |
| Contradictory sources | Escalation reviewer (+ legal if needed) | Keep both with context or choose preferred source policy | Contradiction log |
| Conflicting signals | Reviewer -> confidence reviewer | Link with `contradicts` + scoring cap | Signal relationship rationale |
| Reviewer disagreement | Escalation reviewer | Final decision with rationale | Reviewer comments retained |
| Unresolved ambiguity | Escalate or mark unknown | Prefer `unknown` if evidence insufficient | Unknown reason code |

### Unknown is sometimes correct

If evidence is insufficient, “unknown” is better than a wrong explicit assignment. Ambiguity can remain unresolved until new evidence arrives.

---

## 16. Quality assurance workflow

### QA structure

- reviewer QA pass before approval,  
- random audit samples (weekly),  
- calibration sessions between reviewers,  
- consistency checks across taxonomy and confidence decisions,  
- stale review checks.

### QA checks

| Check | Purpose |
|------|---------|
| Extraction consistency | Same source patterns map similarly |
| Taxonomy consistency | Equivalent phrases map to same node |
| Confidence consistency | Similar evidence -> similar confidence |
| Explainability completeness | Trace chain present and understandable |
| Stale checks | Outdated signals downgraded or flagged |

### Error handling

- Track false positives and false negatives.  
- Support rollback of approved outputs via correction workflow.  
- Re-run impacted downstream recommendations after correction.

---

## 17. Operational metrics

| Metric | Definition | MVP threshold direction |
|--------|------------|-------------------------|
| Extraction precision | % extracted signals judged correct in audit sample | Increase over time |
| Reviewer agreement | Agreement on mapped entities/confidence | Stable high band |
| Signal acceptance rate | Approved / drafted signals | Balanced (not maxed) |
| Escalation rate | Escalated cases / reviewed cases | Moderate; spikes indicate policy gaps |
| Merge accuracy | Correct merge decisions in audit | High |
| Recommendation usefulness | Reviewer/pilot usefulness score | Improve each cycle |
| Explainability quality | % outputs traceable end-to-end | Near-complete |
| Confidence consistency | Variance on similar cases | Low drift |
| Stale-signal rate | % active signals past stale policy | Decreasing |

### Metric review cadence

- Weekly operational review for queue health and QA defects.  
- Monthly calibration for taxonomy/confidence drift.  
- Thresholds are directional in MVP; emphasis is trend and defect learning, not rigid SLA.

---

## 18. MVP limitations

- Manual bottlenecks limit throughput.  
- Source coverage is intentionally narrow.  
- Taxonomy maturity is partial and evolving.  
- Confidence remains uncertain for inferred signals.  
- Automation is intentionally limited.  
- Reviewer subjectivity cannot be fully eliminated yet.  
- Labor-market coverage is incomplete and not nationally representative.

---

## 19. Future operational evolution

Likely evolution after this playbook is stable:

- semi-automated extraction suggestions,  
- AI-assisted reviewer hints for taxonomy/confidence,  
- confidence calibration studies,  
- benchmark datasets for extraction regression tests,  
- dedicated reviewer tooling,  
- first extraction UI,  
- candidate-profile extraction workflows,  
- NAV pilot ingestion under same explainability constraints.

**Core principle remains:** humans are authoritative in early phases; automation proposes, humans approve.

---

## 20. Final summary

This playbook defines a **human-in-the-loop intelligence operating model** where every extracted entity, signal, gap, overlap, and recommendation is traceable to source evidence and review decisions.

It prioritizes:

- explainability over speed,  
- provenance over convenience,  
- semantic consistency over volume,  
- confidence accuracy over optimistic scoring.

This controlled extraction discipline is foundational infrastructure for future AI assistance. Without it, later automation would scale inconsistency and reduce trust. With it, sokr.online can evolve from careful manual intelligence to reliable assisted intelligence while preserving auditability and reviewer confidence.

# First intelligence extraction MVP

**Specification only:** no SQL, no import scripts, no frontend. This document defines the **first operational end-to-end intelligence extraction MVP** for the Norwegian Career Intelligence Dataset and **sokr.online**.

**Design choice (explicit):** we optimize for a **high-quality, explainable intelligence system**, not a **mass ingestion platform**. Semantic quality, provenance, and controlled extraction beat scale for this phase.

**Related:** [Dataset design](education-demand-intelligence-design.md) · [Career taxonomy](career-taxonomy-design.md) · [Scoring and signal model](scoring-and-signal-model.md) · [MVP schema](minimum-viable-intelligence-schema.md) · [Source normalization](source-normalization-strategy.md) · [Canonical source review](canonical-source-review-workflow.md)

---

## 1. Purpose of the MVP

### Why this MVP exists

End-to-end validation is impossible without a **thin slice** of real Norwegian supply/demand data flowing through **taxonomy → signals → gaps/overlaps → recommendations → human judgment**. This MVP builds that slice deliberately small so the team can **see** failures (wrong competency tag, overconfident gap, opaque recommendation) before scaling.

### What it is validating

| Area | Validation question |
|------|---------------------|
| **Taxonomy usefulness** | Do role/competency/evidence codes support real Spor 1/2 text without constant fighting the model? |
| **Signal quality** | Are extracted statements faithful, typed, and useful for scoring? |
| **Confidence model** | Do categories and caps stop fake certainty? |
| **Gap / overlap scoring** | Do outputs match expert intuition on pilot personas? |
| **Canonicalization** | Do sources/datasets survive review and dedup rules? |
| **Explainability** | Can a reviewer trace signal → source row → recommendation in minutes? |
| **Recommendation quality** | Are suggestions actionable and appropriately hedged? |
| **Review workflows** | Are queues and decisions sustainable at MVP volume? |

### Why small / high-quality scope is intentional

Mass ingestion hides **systematic** errors (duplicates, taxonomy drift, confidence inflation). A **bounded** corpus forces **precision**, **manual QA**, and **instrumentation** (lineage, confidence, audit) to harden before NAV-scale noise arrives.

### Why explainability beats scale initially

sokr.online advice touches careers and liability. **If we cannot explain it, we should not ship it at scale.** Early users and reviewers are the **test harness** for explainability; volume without traceability would destroy trust and prevent debugging.

---

## 2. Scope of the MVP

### In scope (initial recommendation)

| Layer | Sources (examples) | Rationale |
|-------|---------------------|-----------|
| **Spor 1 (education supply)** | **NIFU** (selected tables/products), **Studiebarometeret** (one or few waves/institutions), **selected university career services** (HTML/PDF pages agreed in source map) | Rich supply semantics; bounded public or licensed data |
| **Spor 2 (employer demand)** | **Trainee programs** (official pages), **selected employer criteria** (published requirements), **Universum metadata only** (rankings/labels as provided by license or partner extract — no scraping beyond agreed use) | Explicit employer language; good for selection signals |

### Explicitly NOT in MVP yet

| Excluded | Why |
|----------|-----|
| **NAV bulk ads** | Needs token, volume, and parsing discipline; would drown manual QA before taxonomy/signals stabilize |
| **Glassdoor / Indeed scraping** | Legal, bias, and PII risk; review-heavy |
| **Realtime ingestion** | Ops complexity before quality loop is proven |
| **Embeddings / vector search** | Retrieval quality depends on chunking + citations first |
| **Automated LLM extraction at scale** | Hallucination and confidence risk; MVP allows **assisted** extraction only under human gates (see §10) |

**Principle:** every new source type waits until the **review + explainability** pattern works on the in-scope set.

---

## 3. Intelligence entities to extract (first canonical set)

Each row below is a **first-class output** of the MVP pipeline (some already exist as tables; others are **logical** targets for extraction outputs).

| Entity | Purpose | Source examples (MVP) | Normalization expectations | Explainability expectations |
|--------|---------|------------------------|------------------------------|------------------------------|
| **Canonical sources** | Registry of origins | NIFU product, Studiebarometeret file, uni career URL | After [source normalization](source-normalization-strategy.md) + [review](canonical-source-review-workflow.md) | Show workbook row id, URL, approval state |
| **Datasets** | Extractable unit under a source | One SSB/NIFU table id, one survey wave, one scraped page set | Stable `external_id`, clear `title` | Link to `dataset_version` when fetched |
| **Role families** | Aggregate job space | From ads text later; MVP: **seed list** + manual mapping from employer pages | Slug stable; synonyms documented | Every role tag cites mapping rule or reviewer |
| **Competencies** | Shared skill nodes | Manual + controlled keyword rules on trainee text | v0 tree; aliases for NO/EN titles | No silent LLM-only competency without review in MVP |
| **Industries** | Sector context | Employer self-report + manual NACE hint | Shallow tree OK | Source snippet for industry tag |
| **Employer types** | Hiring pattern priors | Derived from employer pages + reviewer confirmation | Small enum | Document heuristic or human pick |
| **Evidence types** | Proof categories for candidates | From scoring spec; examples in copy | Stable slugs | CV bullets map to evidence_type with reason |
| **Selection methods** | Hiring steps | Trainee “process” pages, Universum process metadata | Match [taxonomy](career-taxonomy-design.md) | Quote or structured field reference |
| **Recommendation types** | Product action taxonomy | Static catalog | No change without governance | Trigger links to gap ids |
| **Employer requirements** | Explicit criteria rows | GPA, language, test type from trainee PDFs/HTML | One row per atomic requirement | `dataset_version_id` + excerpt |
| **Role requirements** | Profile of role family demand | Aggregated from small hand-curated employer set in MVP | Low **n** — label as statistical weakness | Show n and period in UI |
| **Candidate signals** | Interpreted profile facts | Labeled pilot profiles (consent) | High explicit, low inferred | Profile line → signal ids |
| **Market signals** | Aggregates over small corpus | Frequency of “case” in curated trainee pages | Only with disclosed **n** and window | Never imply national labor market |
| **Trajectory signals** | Next-step readiness hints | Curated ladder + pilot narrative | Mark `inferred` unless outcome data exists | “Illustrative path” vs “data-backed” |

---

## 4. Signal extraction model

### What becomes a signal

- **Explicit** employer or program statements (quoted or structured): requirements, steps, tools, degrees.  
- **Aggregated** patterns over the **MVP corpus** with documented **n** and **period** (market_signal class).  
- **Pilot candidate** facts entered with evidence references (evidence_signal).

### What does NOT become a signal (MVP)

- Raw heading lines, internal TODOs, **examples** not promoted by review.  
- **Single** anecdotal phrases without second source (no “market says” from n=1).  
- **LLM-only** extractions without human spot-check in MVP.

### Dimensions

| Dimension | MVP rule |
|-----------|----------|
| **Explicit vs inferred** | Explicit → `explicit_requirement` / `explicit_selection_criterion` when text supports; else `inferred_pattern` with **lower cap** on downstream use. |
| **Provenance** | Every signal has `signal_sources` → `dataset_version` (or approved manual entry id stored analogously in metadata). |
| **Confidence** | Assigned per [scoring model](scoring-and-signal-model.md); never above allowed ceiling without evidence type. |
| **Temporal** | `valid_from` / `valid_to` / `market_period` on aggregates; ad-hoc text gets `observed_at` of extraction. |
| **Relationships** | `derived_from`, `reinforces`, `contradicts` used sparingly and logged. |

### Examples

| Text / situation | Signal sketch | Typical confidence |
|------------------|----------------|----------------------|
| “Case interview required” | `selection_signal` + `selection_method=case_interview` | explicit_selection_criterion |
| “Leadership emphasized” | `soft_signal` + competency leadership node | inferred_pattern unless quoted |
| “Project experience preferred” | `evidence_signal` preference for `project` evidence type | explicit or inferred by wording |
| “Quantified results expected” | `hard_signal` / market aggregate on bullet patterns | explicit in criteria; inferred if from tone only → cap |

### Weak / repeated / statistical / inferred / extracted

| Type | MVP use |
|------|---------|
| **Weak signals** | Allowed in **internal** dashboards only unless upgraded. |
| **Repeated patterns** | Need **n≥2** independent sources in MVP corpus OR one official doc + one corroboration — policy table defines minimum. |
| **Statistical** | Only with **verified_statistical** when from official tables; else `inferred_pattern` + low severity. |
| **Extracted statements** | Stored as signal with **span** or field id in `payload_json` for audit. |

---

## 5. Taxonomy usage (MVP maturity)

| Taxonomy | Required maturity | Allowed ambiguity | Manual review | Normalization rules |
|-----------|-------------------|-------------------|----------------|----------------------|
| **Role** | v0 seed + **manual** mapping for pilot employers | Multi-label OK with primary/secondary | Reviewer approves first 50 mappings | Synonym table; no orphan slugs in production signals |
| **Competency** | v0 shallow + **curated** edges for pilot | Overlap allowed; declare primary | New nodes go through taxonomy queue | NO/EN title variants in alias list |
| **Evidence** | Stable enum from spec | Low | Spot-check | Map CV bullets to types with reviewer samples |
| **Industry** | Coarse sectors | Adjacency allowed | Employer self-report vs reviewer override | Document override reason |
| **Employer type** | Small set | Medium | Review if heuristic | org size + public/private hints |
| **Selection method** | Match published steps | One step → multi tags rare | Review ambiguous pages | Prefer employer order as listed |
| **Recommendation** | Fixed catalog | Low | Product approves copy templates | Triggers reference `gap_type` + `recommendation_type` |

---

## 6. Confidence model for MVP

### Allowed categories (primary use)

`verified_statistical` · `explicit_requirement` · `explicit_selection_criterion` · `inferred_pattern` · `candidate_claim` · `weak_signal`  

**Restricted in MVP:** `llm_extracted` — only with **human_verify** flag or capped to non-user-facing sandboxes.  
**Review-based:** not in MVP corpus for production signals (Universum is metadata, not Glassdoor-style reviews).

### Rules

| Rule | Content |
|------|---------|
| **Downgrade** | Missing provenance link, stale `valid_to`, conflicting sibling signal → downgrade one step. |
| **Escalate** | Borderline legal text, ambiguous “leadership”, first occurrence of new competency phrase → review queue. |
| **Caps** | `inferred_pattern` cannot drive **critical** gap or **must-do** recommendation alone. |
| **Stay inferred** | Until second independent source or human promotion — especially leadership, culture, “commercial acumen”. |

### Principles

- **No fake certainty** — UI shows category + “what would upgrade this.”  
- **No hallucinated requirements** — LLM assists tagging only on **stored** text spans reviewed by human in MVP.  
- **Provenance mandatory** — missing `dataset_version_id` / approved manual ref → signal blocked from production consumers.

### Examples

| Situation | Confidence |
|-----------|------------|
| SSB table cell on employment | verified_statistical |
| Trainee PDF bullet “Master required” | explicit_requirement |
| “We value initiative” marketing line | inferred_pattern or weak_signal |
| Reviewer confirms mapping | explicit_* or upgraded inferred with audit |

---

## 7. Gap scoring for MVP

| Gap type | Inputs required | Acceptable evidence | Scoring constraints | Confidence impact | Explainability |
|----------|-----------------|----------------------|---------------------|-------------------|----------------|
| **Competency gap** | Target role profile + candidate competency set | Explicit job/program text + candidate evidence | Severity capped if target profile is `inferred` | Low target profile → cap severity | List missing competency ids + source quotes |
| **Evidence gap** | Required evidence types for target | Trainee page + pilot rubric | Cannot be “critical” on LLM-only | Thin evidence lowers ceiling | Show which evidence_type missing |
| **Certification gap** | Explicit cert in criteria | Named cert only | No gap if not explicit in MVP corpus | explicit → can be high | Cite page + excerpt |
| **Process gap** | Selection steps vs prep evidence | Case/calendar from employer | High if explicit step | inferred process → medium max | Map step → selection_method |
| **Trajectory gap** | Next-step model + profile | Curated ladder + reviewer | Mostly inferred in MVP — **soft** recommendations | Keep low unless human validates path | Label “illustrative trajectory” |
| **Industry gap** | Target industry profile | Hand-curated small set | Low n → force “exploratory” | statistical weakness | Show n |
| **Positioning gap** | Narrative vs market keywords | Pilot + reviewer judgment | Qualitative score band | mixed | Written rationale required |
| **Network gap** | Sector referral importance flag | Metadata + expert flag only in MVP | Never sole **critical** blocker | weak by default | Disclose evidence weakness |

---

## 8. Overlap scoring for MVP

| Overlap type | MVP definition notes |
|----------------|----------------------|
| **Competency** | Weighted Jaccard on curated nodes; **partial** credit via `related_to` edges (documented). |
| **Role** | Graph distance on small seed graph; **transition** overlap flagged separately. |
| **Industry** | Tree distance + **adjacency** list; coarse. |
| **Evidence** | Intersection of satisfied evidence types; **asymmetric** if employer demands > candidate shows. |
| **Employer** | Type + industry bucket match; weak signal unless explicit employment history. |
| **Trajectory** | Overlap with **next-step** competency bundle; mostly illustrative in MVP. |

### Concepts

| Concept | Handling |
|---------|----------|
| **Transferability** | Explicit “adjacent role” list reviewed; partial overlap boost with cap. |
| **Partial overlap** | Report score **band** when confidence mixed. |
| **Asymmetrical overlap** | Employer needs 10 skills; candidate has 7 **core** → higher than 10 peripheral. |
| **Transition overlap** | Candidate closer to **step k+1** on path than to current step — explain as trajectory signal, not hire guarantee. |

### Examples

| Pair | Narrative |
|------|-----------|
| Consultant ↔ strategy | High analytical + communication overlap; industry varies |
| PM ↔ operations | Delivery + stakeholder overlap; leadership evidence asymmetry common |

---

## 9. Recommendation generation (MVP)

### Triggers

- **Gap severity** over threshold **and** confidence floor met.  
- **Readiness stage** transition (e.g. application_ready → interview_ready) with selection_gap.  
- **Evidence gap** blocking credibility for declared target role.

### Priority

Follow [scoring priority classes](scoring-and-signal-model.md): favor **critical_missing_evidence**, **selection_blocker**, **high_impact_low_effort** before visibility polish.

### Explainability & confidence

- Each recommendation lists **`trigger_gap_ids`** (and overlap ids if used).  
- **Recommendation confidence** ≤ min(confidence of contributing gaps/signals).  
- **Limits:** max **N** active urgent items (e.g. 3); no duplicate category spam after dismiss.

### Examples

| Recommendation | Backing |
|------------------|---------|
| Strengthen quantified results | evidence_gap + employer explicit numeracy |
| Improve interview readiness | process_gap + selection_method case |
| Improve role alignment | positioning_gap + role_overlap band |
| Improve evidence quality | evidence_gap |
| Improve LinkedIn positioning | positioning_gap + market_signal (low weight) |
| Improve network reach | network_gap — **never** sole critical without sector flag |

### Hard rules (MVP)

- **Evidence-backed:** must cite at least one **signal id** or **employer_requirement id** in explain payload.  
- **Reference gaps:** `trigger_gap_ids` non-empty for automated suggestions (templates may be exception if product-approved static).

---

## 10. Human review workflow in MVP

| Checkpoint | Requirement |
|------------|-------------|
| **Source / dataset** | [Canonical source review](canonical-source-review-workflow.md) before production catalog |
| **Taxonomy mapping** | First N employer pages + first N profile examples **double-reviewed** |
| **Signal promotion** | Inferred signals above severity S → second reviewer or lead |
| **Gap / overlap** | Pilot outputs sampled weekly |
| **Recommendation copy** | Product + domain sign-off on templates |
| **Merge** | Lead reviewer for cross-layer merges |
| **Confidence** | Any upgrade to explicit_* from inferred → reviewer |
| **Escalation** | Legal for scraping; lead for taxonomy conflict |

### AI assistance vs human approval (MVP)

| AI may | Human must |
|--------|------------|
| Propose row classification, duplicate clusters, competency tags | Approve production catalog, merges, confidence upgrades |
| Draft explanation text from structured ids | Validate wording especially for inferred |
| Summarize diff between two source versions | Approve deprecation / successor links |

---

## 11. Explainability model

### What we explain

| Question | Answer artifact |
|----------|-----------------|
| Where did a signal come from? | `signal_sources` → `dataset_version` → storage URI / excerpt id |
| Why this recommendation? | `trigger_gap_ids` → gap `explain_json` → contributing `signal_ids` |
| Why this gap? | Missing competencies/evidence vs target profile with **citations** |
| Why low/high confidence? | Category + rules applied + “would upgrade if …” |
| Which sources contributed? | Ordered list with role (primary, corroborating) |

### Visibility principles

- **Provenance** always reachable in one hop from user-facing summary (admin depth ok).  
- **Traceability** from recommendation → gap → signal → version → raw file.  
- **Confidence display** default: category + short plain language; optional detail panel.  
- **Lineage** for merges and confidence changes preserved in audit metadata.

### Example chain (logical)

`Recommendation` “Practice case interviews” → `gap` process_gap (severity 0.8) → `signals` [S1: explicit_selection_criterion from trainee page v3, S2: selection_method case from taxonomy mapping] → `dataset_version` for crawl 2026-05-01 → PDF excerpt ref #12.

---

## 12. Quality evaluation

| Dimension | How to measure (MVP) |
|-----------|----------------------|
| **Signal precision** | Sample reviewed signals vs source text; target error budget set by team |
| **Taxonomy consistency** | Inter-reviewer agreement on same texts |
| **Confidence consistency** | Audit upgrades/downgrades vs rules |
| **Recommendation usefulness** | Structured reviewer rubric + optional pilot user feedback |
| **Overlap usefulness** | Expert “does this feel right?” on 10–20 pairs |
| **Explainability quality** | Time-to-trace audit; failed traces logged as bugs |
| **Reviewer agreement** | Cohen’s kappa or simple % agreement on pilot set |
| **False positives** | Count of reversed decisions after second review |
| **Stale signals** | Flag signals past `stale_after` still shown to users |

**Mix:** qualitative narrative (reviewer trust) **plus** quantitative thresholds on pilot samples.

---

## 13. MVP success criteria

### Success looks like

- **Reviewers trust outputs** enough to show pilot users without apology disclaimers dominating the UI.  
- **Signals are explainable** in trace time under agreed SLA (e.g. <5 minutes for expert).  
- **Recommendations feel actionable** and tied to gaps, not generic blog text.  
- **Taxonomy works operationally** for pilot employers and roles without daily emergency patches.  
- **Gaps/overlaps feel useful** on curated personas (graduate, switcher, public→private).  
- **Provenance preserved** for every production signal path tested.  
- **Confidence behaves** — caps prevent inferred-only “must haves.”  
- **Manual review remains manageable** at chosen corpus size (queue depth stable week over week).

### Explicitly NOT required for MVP success

| Non-goal |
|----------|
| Full automation of ingestion or tagging |
| Nationwide scale or full NAV coverage |
| Realtime refresh |
| Embeddings / vector RAG in production |
| Recruiter-facing tools |
| Advanced labor-market forecasting |
| Production-grade personalization engine |

---

## 14. MVP limitations

| Limitation | Consequence |
|------------|-------------|
| **Limited coverage** | Findings are **not** national labor market truth statements. |
| **Incomplete taxonomy** | Unknown labels land in review queue; some edges missing. |
| **Partial confidence** | Many signals stay inferred or medium. |
| **Manual bottlenecks** | Throughput capped by reviewer headcount. |
| **No large-scale automation** | Cost per row higher; acceptable by design. |
| **No generalized AI reasoning** | System composes **evidence + rules**, not open-ended career oracle. |
| **No production-grade recommendation engine** | Rules + small models / heuristics only. |

---

## 15. Next phase after MVP

Likely sequence (adjust with learnings):

1. **Canonical source expansion** — more Spor 1/2 rows through hardened review pipeline.  
2. **NAV ingestion pilot** — small window, explicit confidence rules, side-by-side with employer text.  
3. **First structured extraction pipelines** — repeatable jobs with QA metrics, still human-gated.  
4. **First benchmark datasets** — frozen persona sets + expected gaps for regression testing.  
5. **First candidate-profile mapping** — consenting users or synthetic personas with PII controls.  
6. **First RAG retrieval tests** — citation accuracy, confidence filters, Norwegian-first ranking.  
7. **First dedicated review tooling** — beyond spreadsheets: queues, diff, merge UI.  
8. **Confidence calibration studies** — correlate confidence with outcomes / reviewer outcomes.

---

## 16. Final summary

**Philosophy:** ship **intelligence infrastructure** that is **honest about what it knows** — provenance-first, confidence-aware, and review-gated — rather than a volume-first matcher.

**Operational scope:** a **small, curated** mix of Spor 1 and Spor 2 sources, manual and semi-automated extraction, v0 taxonomies, signals with mandatory lineage, bounded gap/overlap scores, and evidence-linked recommendations.

**Explainability-first:** every meaningful output should trace to **sources and reviewer-approved interpretations**; scale waits until that chain is trustworthy.

**Why not “job matching”:** matching without explainable evidence is a commodity; this MVP builds the **substrate** (taxonomy + signals + gaps + recommendations + audit) that sokr.online can use for **defensible** career guidance later — **intelligence**, not just **scores**.

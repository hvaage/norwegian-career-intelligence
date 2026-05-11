# Persistent verified statistical signal model

**Specification only:** this document defines the first **operational persistence model** for **approved** `verified_statistical` signals in the Norwegian Career Intelligence Dataset and **sokr.online**. It does **not** define SQL migrations, persistence scripts, Supabase writes, product UI, or downstream recommendation logic.

**Purpose:** define the **canonical bridge** between:

- preview statistical signals (local CSV/JSON, pilot generator),
- reviewed and governance-approved interpretations,
- **persistent** verified statistical signals (durable storage, when implemented),
- future intelligence layers (gaps, overlaps, recommendations, RAG) that **consume** but do **not** define this layer.

**Explicit non-goals (this layer must not become):**

- recommendations, candidate matching, overlap/gap logic,
- embeddings or vector search,
- forecasting or labor-market prediction,
- AI-generated interpretation or causal inference.

**Related documents:**

- `docs/verified-statistical-signal-generation-mvp.md`
- `docs/verified-statistical-signal-extraction-pilot.md`
- `docs/verified-statistical-signal-review-checklist.md`
- `docs/verified-statistical-signal-manual-review-round1.md`
- `docs/statistical-observation-schema.md`
- `docs/statistical-ingestion-pipeline-mvp.md`
- `docs/ssb-import-validation-checklist.md`
- `docs/scoring-and-signal-model.md`

**Design posture:** governance-first, explainability-first, deterministic-first, conservative promotion to persistence.

---

## 1. Purpose of persistent verified statistical signals

### Why preview signals are insufficient

Preview signals prove that normalized observations can be read, interpreted, and emitted **deterministically** with lineage and explainability fields (`docs/verified-statistical-signal-extraction-pilot.md`). They remain **ephemeral artifacts** by design: default `quality_flags` include `preview_not_product_ready`, thresholds can change between runs, and review samples are for **human triage**, not durable truth.

Preview alone does **not** establish:

- organizational approval to treat a row as a **long-lived** intelligence primitive,
- stable **consumer contracts** (APIs, dashboards, regulatory narrative),
- **audit-grade** linkage from product-facing numbers back to exact source revisions.

### Why persistence requires governance

Persistence elevates a derived row from “we computed it once” to “the dataset and product may rely on it.” That transition must be **gated**, **logged**, and **reversible** without silent mutation. Governance ensures that **statistical meaning**, **product risk**, and **lineage completeness** are explicitly accepted—aligned with `docs/verified-statistical-signal-review-checklist.md`.

### Layer distinctions

| Layer | Meaning |
|--------|---------|
| **Observations** | Atomic measured facts: one `value`, one period, one coordinate in dimension space (`docs/statistical-observation-schema.md`). Immutable, source-authoritative. |
| **Preview signals** | Rows emitted by the preview generator into local `signal_preview_*` outputs only. May include automated quality scores and flags; **not** approved for durable storage by default. |
| **Persistent signals** | **Approved** statistical interpretation artifacts stored as durable records, with full lineage, review history, and lifecycle state. Subject to superseding and archival rules. |
| **Downstream intelligence** | Gaps, overlaps, recommendations, RAG ranking, trajectory logic (`docs/scoring-and-signal-model.md`). **Consumes** persistent signals as **evidence primitives**; must not redefine observation meaning or bypass lineage. |

### Why persistent signals become reusable intelligence primitives

Once persisted under this model, a signal is:

- **Reviewed** — human or governed automation has accepted it for the declared tier.
- **Lineage-backed** — traceable to observations, dataset versions, and transformation logic.
- **Deterministic** — same inputs + same logic version + same thresholds reproduce the same **identity** and payload checksums.
- **Explainable** — mandatory narrative and structured explainability for audit and support.
- **Replayable** — can be validated or regenerated for drift and revision detection.
- **Governance-approved** — tied to identifiable approvers, timestamps, and policies.

Downstream systems can then cite signals as **typed, governed evidence** rather than recomputing ad hoc statistics in product code.

---

## 2. Definition of a persistent verified statistical signal

### One row, one approved interpretation

**One persistent verified statistical signal** equals **one approved statistical interpretation artifact**: a single, well-bounded claim derived from a **defined** set of observations, under **declared** rules, for a **declared** slice and time pairing.

Examples of **interpretation types** (non-exhaustive; bounded by MVP signal types in `docs/verified-statistical-signal-extraction-pilot.md` and `docs/verified-statistical-signal-generation-mvp.md`):

- employment growth or decline between two periods on a stable slice,
- regional education–employment shift (table `11615`-style semantics),
- industry education–employment shift (`12850`-style),
- occupation structure snapshot (`09793`-style, where defined),
- education-level workforce snapshot (`08417`-style, where defined).

### Derived, not raw

- Signals are **derived from** observations; they are **not** raw SSB cells duplicated without interpretation context.
- Signals **must inherit** observation lineage: dataset, version, table, period pairing, dimensions, units, and source observation identities sufficient to re-derive the numeric result.

### Not downstream product logic

Persistence records **what the statistics support under governance rules**. It does **not** assert career advice, hiring fit, or causal mechanisms.

---

## 3. Persistence eligibility

### Signals MUST

| Requirement | Rationale |
|---------------|-----------|
| Originate from **validated** observations | Importer and validation gates (`docs/ssb-import-validation-checklist.md`, `docs/statistical-ingestion-pipeline-mvp.md`) must have accepted the underlying rows. |
| Pass the **signal review workflow** | No silent promotion (`docs/verified-statistical-signal-review-checklist.md`). |
| Have **deterministic generation** | Same slice, periods, source observation set, `signal_logic_version`, and threshold set → same `signal_deterministic_hash` (pilot-aligned concept). |
| Have **stable lineage** | Non-null or explicitly governed placeholders for importer version, dataset version, normalization/transformation versions where the schema requires them; see Round 1 gaps (`docs/verified-statistical-signal-manual-review-round1.md`). |
| Have **explainability** | Human and machine-readable explanation of slice, periods, filters, thresholds, direction, exclusions (`docs/verified-statistical-signal-generation-mvp.md`). |
| Have **reproducible calculations** | Formulas and ordering rules versioned; no hidden randomness. |
| Have **confidence classification** | Mapped to persistent confidence categories (§14), not a single opaque number. |
| Have **valid temporal pairing** | Period granularity and ordering consistent with pilot rules (e.g. two-period change on the same non-time slice). |
| Pass **noise filtering** | Baseline, absolute change, unspecified category, total-category, and strict-validation policies as applicable to the signal type. |
| Avoid **unstable low-baseline artifacts** for the **declared tier** | Product-tier persistence may require stricter floors than preview defaults (Round 1: kommune × narrow slice volatility). |

### Signals MUST NOT

| Prohibition | Rationale |
|-------------|-----------|
| Depend on **hallucinated** or non-source values | No invented counts or periods. |
| Depend on **AI inference** for the numeric claim | LLMs may summarize **existing** persisted fields later; they must not be the source of the statistic. |
| Mix **incompatible units** | Enforced by validation and quarantine (pilot: `strict-validation` path). |
| Mix **incompatible periods** | e.g. do not compare quarterly to annual without explicit governed transform. |
| **Aggregate incompatible dimensions** | No silent roll-ups across definitions (e.g. residence vs workplace `ContentsCode`—keep distinct identities). |
| Contain **missing lineage** required for replay | Incomplete lineage → quarantine, not persistence. |
| Contain **unresolved** or ambiguous dimension semantics | “Unknown” mapping to a persisted slice without steward approval. |
| Depend on **ambiguous mappings** | Taxonomy or crosswalk not approved for this signal class. |

### Eligibility classes

| Class | Definition |
|--------|------------|
| **Eligible** | Passes automated validation + meets minimum lineage and explainability for **consideration** in review. |
| **Review-only** | Statistically computable but default **blocked** from product exposure until stratum-specific review (e.g. small kommune × narrow education slice—Round 1 guidance). |
| **Quarantined** | Held pending investigation: lineage gap, replay mismatch, duplicate hash, anomaly (§7). |
| **Rejected** | Fails structural, statistical, or policy gates; must not persist. |

---

## 4. Signal lifecycle

These states govern **the signal record** from preview through retirement. They are **conceptual** until implemented; align operational logging with them even before tables exist.

| State | Meaning |
|--------|---------|
| **preview_generated** | Emitted by preview run into `signal_preview_rows.csv` / summary JSON only. |
| **under_review** | Assigned to reviewers; decisions in progress. |
| **approved_for_persistence** | Governance accepts writing this signal to durable storage in a named batch/tier. |
| **persisted** | Stored as a durable verified statistical signal record; consumers may read according to tier. |
| **superseded** | Replaced by a newer signal identity (revised data, new logic version, or explicit replacement); old row retained per policy. |
| **quarantined** | Temporarily blocked from consumer use pending fix or re-review. |
| **deprecated** | Withdrawn from active use; reason and effective date recorded. |
| **archived** | Retained for audit only; no active product contract. |

### Transitions (normative intent)

```text
preview_generated → under_review → approved_for_persistence → persisted
                      ↓                ↓
                 quarantined ←────────┘
 persisted → superseded → archived
 persisted → deprecated → archived
 quarantined → under_review (after fix) OR rejected
```

### Rules

- **No silent promotion** to `persisted`: `approved_for_persistence` requires explicit human or governed automation with audit trail.
- **Review is mandatory** for the **persistence MVP**: preview generation is never approval (`docs/verified-statistical-signal-review-checklist.md`).
- **Persistence requires explicit approval** tied to identity of approver(s), policy version, and batch id.

**Mapping note:** `docs/verified-statistical-signal-review-checklist.md` uses `review_pending` and `approved_for_product`; this model uses `under_review` and defers **product** exposure to a separate tier/policy that may require stricter checks than persistence alone.

---

## 5. Signal governance model

### Roles

| Role | Responsibility |
|------|----------------|
| **Importer / operator** | Runs ingestion and preview jobs; ensures validation artifacts, batch ids, and metadata_json completeness (importer version, dataset version linkage). |
| **Signal reviewer** | Executes checklist (`docs/verified-statistical-signal-review-checklist.md`); classifies rows, flags product risk, requests quarantine. |
| **Statistical steward** | Owns definitions: tables, dimensions, ContentsCode semantics, threshold policy, SSB alignment. |
| **Governance approver** | Authorizes promotion from `under_review` to `approved_for_persistence` for a scope (batch, table, or stratum). |
| **Product consumer** | Engineering/product surfaces that read **only** approved tiers; must display lineage-aware copy and caveats. |

### Authority matrix (MVP default)

| Action | Who may perform |
|--------|------------------|
| Approve persistence | **Governance approver** (may require dual control for production tier). |
| Quarantine | **Signal reviewer**, **statistical steward**, or **governance approver**; incidents may allow **operator** quarantine with mandatory follow-up review. |
| Override thresholds | **Statistical steward** with written rationale; **governance approver** for persistence-affecting overrides. |
| Deprecate / supersede | **Statistical steward** or **governance approver**; operators execute **after** approval. |

### Governance logs and auditability

Every transition must append to an **append-only governance log** (conceptually part of `verified_statistical_signal_reviews` / batches—§16): actor, timestamp, prior state, new state, policy version, optional ticket id, optional diff of explainability or lineage fields.

**Approval traceability:** persisted rows must link to **review record ids** and **approval record ids**, not merely “someone ran a script.”

---

## 6. Review status model

Review status describes **human/automation judgment** on a preview or candidate row. It is orthogonal to **lifecycle state** but gates transitions.

| Status | Meaning |
|--------|---------|
| **not_reviewed** | No reviewer decision; eligible only for preview or internal tooling. |
| **auto_validated** | Scripted checks passed (lineage schema, hash uniqueness in batch, arithmetic checks). **Does not** replace manual persistence MVP gate unless explicitly enabled by policy. |
| **manually_reviewed** | A human recorded checklist outcome; may still be reject or conditional. |
| **approved** | Meets all checklist must-haves for the declared persistence tier. |
| **conditionally_approved** | Approved only for **non-product** tiers, or with mandatory UI/API caveats (e.g. review-only stratum). |
| **rejected** | Do not persist; reasons mandatory. |
| **quarantined** | Hold; distinct from lifecycle `quarantined` at record level but should sync in implementation. |

### MVP posture

- **Preview generation is NOT approval** (`docs/verified-statistical-signal-extraction-pilot.md`).
- **Manual review remains mandatory** for first persistence workflow MVP, consistent with Round 1 conclusion: lineage gaps and volatility require human judgment (`docs/verified-statistical-signal-manual-review-round1.md`).

---

## 7. Quarantine model

### When to quarantine

Signals **should** be quarantined if any of the following hold:

| Trigger | Examples |
|---------|----------|
| **Incomplete lineage** | Missing `dataset_version_id`, `importer_version` unknown where policy requires non-unknown, missing source observation ids. |
| **Unstable baselines** | Borderline `min_baseline`, high `%` on small N for the target tier (Round 1). |
| **Noisy changes** | Large swing with `unstable_slice`-class heuristics for product tier. |
| **Dimension inconsistencies** | Slice mismatch, mixed units, invalid period pairing when strict validation would fire. |
| **Mixed units / periods** | Hard validation failures in generator. |
| **Invalid periods** | Unparseable or non-comparable periods for the signal type. |
| **Duplicate deterministic hashes** | Collisions or duplicate emissions in a batch—investigate before persistence (`docs/verified-statistical-signal-manual-review-round1.md`). |
| **Replay inconsistency** | Regeneration yields different hash or material numeric mismatch under locked inputs. |
| **Unexplained statistical anomalies** | Steward-flagged outliers pending SSB revision check. |

### Quarantine reason tracking

Store structured **reason codes** (machine) and **free-text** (human), plus links to preview summary counters, importer batch, and observation ids.

### Re-review and replay after quarantine

1. Fix **data** or **metadata** (e.g. importer version stamping).  
2. Re-run **preview** or **validation replay**.  
3. **Re-review** with updated artifacts.  
4. Either **approve** (transition to `approved_for_persistence`) or **reject**.

### Manual override

Overrides require **governance approver** + **statistical steward** co-signature for production-affecting cases, with mandatory narrative in governance log. Overrides must **not** delete contradictory historical rows; they **supersede** or **deprecate** per §12.

---

## 8. Lineage inheritance model

### Inheritance chain

Persistent signals **inherit** and **extend** lineage from:

| Source | What must flow through |
|--------|-------------------------|
| **Observations** | `observation_id`s (or signatures), `table_id`, `period`, `value`, `unit`, `dimensions_json`, `dimension_labels_json`, `confidence_category`. |
| **Datasets** | `statistical_dataset_id` / logical dataset key. |
| **Dataset versions** | `dataset_version_id`, source file identifiers, fetch timestamps. |
| **Dimensions** | Codes and labels as observed at ingest; no silent relabel without new version. |
| **Transformation / normalization versions** | Version strings recorded on observations or batch metadata. |
| **Preview / emission logic** | `signal_logic_version`, `preview_script_version` (or successor persistent emitter version). |

### Persistent signal must retain (minimum)

- **Source observation ids** (or stable observation signatures if ids are internal-only),
- **Deterministic signal hash** and inputs that fed it,
- **Source table ids** and `contents_code` where applicable,
- **Generation logic version** and **threshold set** identifier,
- **Generation timestamp** (UTC),
- **Confidence derivation** — which rules mapped observation confidence + signal quality to persistent confidence (§14).

### Non-lossy lineage

Lineage must **never** be lossy for persisted rows: dropping fields to save space is forbidden without a **new major version** and explicit archival of the prior payload. Persistent signals remain **traceable to raw statistics** via dataset version → raw asset → observation coordinates.

---

## 9. Deterministic identity model

### Deterministic signal identity

The **identity** of a signal is the tuple of **semantic inputs** that define “the same claim” across replays:

- `signal_type`,
- non-time **dimension slice** (canonical sorted JSON),
- **periods compared** and granularity,
- **source observation** set (ordered ids or signatures),
- **signal_logic_version**,
- **threshold profile** (e.g. `min_baseline`, `min_absolute_change`, contents-code scope),
- **ContentsCode** and unit when part of slice identity.

### Deterministic signal hash

The **hash** is a cryptographic fingerprint (e.g. SHA-256) over the canonical serialization of the above, aligned with pilot `signal_deterministic_hash` behavior (`docs/verified-statistical-signal-extraction-pilot.md`).

### Replay identity

**Replay identity** is the hash plus **batch context** (ingestion batch, dataset version) used to prove that a replay job reproduced the **same** signal under **locked** configuration.

### Properties

- **Same inputs + same logic + same thresholds ⇒ same signal identity.**  
- **Deterministic identity** enables **diff-based regression**, **idempotent persistence**, and **collision detection**.

---

## 10. Signal stability model

### What “stable” means for persistence

A **stable signal** (for a given tier) should:

- **survive replay** under locked configuration,
- **survive re-import** of the same dataset version,
- **survive deterministic regeneration** after code changes **only** when logic version and thresholds are unchanged,
- **retain explainable meaning** when read months later (definitions still interpretable).

### Instability sources

| Source | Effect |
|--------|--------|
| **Tiny baselines** | Large % moves; product misleading. |
| **Changing dimensions / classifications** | Slice identity drift; requires new logic version or mapping version. |
| **Revised source statistics** | Same slice, new values; may **supersede** prior persisted signal. |
| **Threshold drift** | Inclusion/exclusion changes without version bump—forbidden; threshold sets must be versioned. |
| **Logic-version drift** | New `signal_logic_version` creates new identities; old rows **superseded**, not mutated. |

### Classifications

| Term | Definition |
|------|------------|
| **Stable signal** | Passes stability rules for the tier; replay matches within declared tolerance. |
| **Unstable signal** | High volatility or borderline N; may exist in preview or **review-only** persistence tier. |
| **Transient signal** | Short-lived diagnostic or experimental emission; must not enter default persistence without explicit policy. |

---

## 11. Replay and reproducibility model

### Replay behaviors

| Mode | Intent |
|------|--------|
| **Deterministic regeneration** | Recompute from observations + locked config; compare hash and metrics. |
| **Validation replay** | CI-style `--preview-report-only` or successor: counters, uniqueness, lineage schema. |
| **Historical replay** | Reconstruct what was believed true at a past `dataset_version_id` (archive access). |

### Reproducibility requirement

A persisted signal must be reproducible from:

- the **exact source observations** (or dataset version + coordinates if observations are re-derived),
- **logic version**,
- **threshold profile**,
- **lineage** sufficient to fetch the above.

### Replay mismatch handling

| Outcome | Action |
|---------|--------|
| **Hash match, values match** | Pass; optional metadata-only drift logged. |
| **Hash match, values differ** | **Critical** — quarantine persisted row; investigate float policy, rounding, or data corruption. |
| **Hash mismatch** | Treat as **new** signal identity or importer bug; do not overwrite silently. |

### Validation checks (conceptual)

- uniqueness of `signal_deterministic_hash` within **persistence scope** (e.g. active + non-archived),
- non-null required lineage fields per policy,
- arithmetic consistency (value_end − value_start, percent rules),
- period ordering and granularity consistency.

### Replay audit trails

Store replay job id, timestamp, outcome, diff summary, and links to preview summary JSON for that run.

---

## 12. Superseding and versioning model

### Principles

- Signals **never silently mutate** in place: corrected numbers or definitions create a **new version** or a new row with a distinct supersession chain.
- **New logic versions** create **new signal identities** (hashes) and may co-exist until old ones are deprecated.
- **Revised official statistics** may **supersede** old signals while old rows remain **archived** for audit.

### Definitions

| Term | Meaning |
|------|---------|
| **Signal versioning** | Monotonic version or supersession pointer on the logical “family” of a claim (same slice intent, revised inputs). |
| **Superseded signal** | Replaced by a newer persisted row; retained with `superseded_by` reference. |
| **Active signal** | Current best representation for consumers in the approved tier. |
| **Archived signal** | Retained but excluded from default consumer queries. |

### Historical persistence

Old rows remain queryable for regulators and internal forensics; product defaults to **active** only.

---

## 13. Explainability guarantees

### Mandatory contents

A persistent signal must always explain:

1. **Source observations** — ids, table, and key dimension labels.  
2. **Periods used** — start, end, granularity, and pairing rule.  
3. **Dimensions used** — full slice, including `ContentsCode` semantics where relevant.  
4. **Filters applied** — unspecified exclusion, totals exclusion, small-slice rules, etc.  
5. **Thresholds applied** — numeric floors, percent bands, absolute change minima, with **threshold profile id**.  
6. **Confidence assignment** — mapping from observation confidence + signal quality to persistent category.  
7. **Direction assignment** — growth / decline / stable / snapshot per rules.  
8. **Excluded categories** — what was filtered and why (reference to pilot counters where useful).  
9. **Lineage chain** — dataset → version → file → normalization → transformation → emission.

### Policy

- **Explainability is mandatory.** Rows failing explainability completeness are **not persisted** (quarantine or reject).  
- Short consumer text may be added later; **long-form audit text** remains the source of truth.

### Example narratives (illustrative)

1. *“Employment count change (workplace, `SysselsatteArbSted`), SSB table 11615, Region=Oslo, Fagfelt=Datateknologi, UtdNivaa=Universitets- og høyskolenivå lang, Kvinner: 2024 → 2025, +Δ persons and +Δ% after `min_baseline=100`, `min_absolute_change=10`, unspecified categories excluded (`signal_logic_version` …). Source observations: …”*

2. *“Occupation structure snapshot, table 09793, latest period 2025K4, Yrke=… , value persons, no multi-period trend in locked window—neutral snapshot label per pilot.”*

---

## 14. Confidence model

### Persistent confidence categories (examples)

Categories must be **enumerated and governed**; examples aligned with existing vocabulary:

| Category | Intended use |
|----------|----------------|
| `verified_statistical` | Persisted, lineage-complete, approved for declared tier. |
| `verified_statistical_preview` | Preview-only or sandbox persistence; must not appear in production consumer APIs. |
| `quarantined_statistical` | Blocked from consumption pending review. |
| `deprecated_statistical` | Was active; withdrawn with reason and optional replacement link. |

(Additional tiers such as **review-only** may be encoded as metadata + confidence, not as a weakening of lineage.)

### Inheritance and degradation

- **Confidence inheritance:** starts from observation `confidence_category` and is **narrowed** by signal rules (e.g. preview flags cap product confidence).  
- **Confidence degradation:** e.g. lineage gap discovered post-persistence → move to `quarantined_statistical` or `deprecated_statistical` with supersession.  
- **Confidence invalidation:** source revision or bug → deprecate; never silently upgrade.

### Persistence does not imply correctness

Persistence means **governance-approved interpretation under known rules at a known time**, not infallibility. Official statistics revise; definitions evolve; small-area estimates remain volatile. The model makes that explicit through versioning, quarantine, and deprecation.

---

## 15. Persistence boundary

This layer **only**:

- defines **what** may be stored as a persistent verified statistical signal,
- defines **how** it is reviewed, lineage-bound, versioned, and replayed,
- defines **confidence and lifecycle** semantics for statistical evidence.

This layer **does not**:

- score candidates or roles,
- compute overlaps or gaps,
- generate recommendations,
- train embeddings or run vector retrieval,
- forecast labor demand,
- run LLM interpretation of statistics as a source of truth,
- assert causal explanations (“because X, therefore Y”).

Downstream systems **read** persistent signals as **primitives**; they own product logic separately.

---

## 16. Persistence storage model (conceptual only)

**No SQL in this document.** The following entities are **logical** separations of concerns.

### `verified_statistical_signals`

**Signal payload:** type, slice JSON, periods, metrics (`value_start`, `value_end`, deltas, percent), units, labels, deterministic hash, logic version, threshold profile id, confidence category, lifecycle state, active/superseded links.

### `verified_statistical_signal_sources`

**Many-to-many** (or ordered list) linking signals to **observation_id** / signature rows; supports audit of “which cells built this claim.”

### `verified_statistical_signal_reviews`

**Review history:** reviewer ids, timestamps, checklist outcomes, comments, review status (§6), attachments to preview CSV row ids or hashes.

### `verified_statistical_signal_batches`

**Batch governance:** importer batch, preview run id, approver, policy version, counts, replay outcomes.

### `verified_statistical_signal_lineage`

**Extended lineage document** normalized if payload grows: raw file hashes, JSON-stat extract pointers, normalization parameters—optional split from core row for storage efficiency **without** losing information (§8: no lossy drop).

### Separation rationale

| Concern | Store separately to… |
|---------|----------------------|
| Payload vs lineage | Allow lineage schema evolution without rewriting narrative fields. |
| Review history | Append many events without mutating signal fact columns. |
| Governance metadata | Enable compliance queries (who approved what, when). |

---

## 17. Operational persistence workflow

```text
preview generation
    → automated validation (lineage schema, arithmetic, hashes)
        → review (checklist)
            → governance approval (batch or row-level)
                → persistence write (idempotent by hash + version)
                    → replay validation job
                        → downstream consumption (gated by tier + lifecycle)
```

- **Persistence is a controlled promotion process**, not a side effect of analytics scripts.  
- **Replay validation** after persistence catches drift between writer and reader environments before wide publication.  
- **Downstream consumption** must check **lifecycle**, **confidence**, and **tier** (persistence ≠ product—`docs/verified-statistical-signal-review-checklist.md`).

---

## 18. MVP limitations

The first operational persistence MVP is intentionally narrow:

- **SSB-backed** observations only for in-scope tables (`docs/ssb-import-validation-checklist.md`, pilot tables).  
- **Limited signal types** as listed in extraction pilot / generation MVP.  
- **No fully automated governance** — human approval path remains default.  
- **No realtime replay** — batch replay on release or schedule.  
- **No auto-persistence** from preview runs.  
- **No ML scoring** of signal truthfulness.  
- **No forecasting** or anomaly ML.  
- **No automated anomaly detection** beyond rule-based flags (e.g. unstable slice).

---

## 19. Future evolution

Possible extensions **outside** this MVP spec but aligned with this model:

- **Automated replay validation** in CI on every importer or logic release.  
- **Governance dashboards** — quarantine queues, approval SLAs, steward workload.  
- **Signal lineage graph** — visual path from raw file to product number.  
- **Confidence calibration** — formal uncertainty where SSB publishes margins (future).  
- **Signal drift monitoring** — hash-stable value shifts across dataset versions.  
- **Benchmarking** — compare Norway to OECD/Eurostat where harmonized.  
- **Cross-source validation** — NAV vs SSB where definitions align.  
- **NAV integration** — new observation classes feeding **new** signal types, still under this governance shell.  
- **Signal graph relationships** — explicit edges (“informs”, “contradicts”) without becoming recommendations.

---

## 20. Open questions

Unresolved decisions to settle before implementation:

1. **Signal uniqueness scope** — global uniqueness vs per `table_id` vs per `dataset_version_id` for active rows.  
2. **Replay retention policy** — how long to keep replay job artifacts and diffs.  
3. **Confidence recalculation rules** — whether persisted confidence may be batch-recomputed without supersession.  
4. **Quarantine expiration** — auto-reject vs escalate after N days.  
5. **Review scalability** — row-level vs stratum-level (kommune tier) approval patterns.  
6. **Superseding strategy** — soft supersede with pointer vs hard archive of superseded payloads.  
7. **Lineage depth** — how much normalization detail is inlined on the signal row vs normalized `*_lineage` tables.  
8. **Governance ownership** — RACI between steward, approver, and operator on-call.  
9. **Statistical revision policy** — when SSB revises back years, mandatory supersede vs human triage per magnitude.

---

## Summary

- **Persistence is a governance boundary** between computation and organizational commitment.  
- **Deterministic lineage** makes regulatory and product defense possible: every number has a path to source.  
- **Explainability** is not documentation polish; it is a **hard gate** for trust and supportability.  
- **Persistence must remain conservative** — preview quality (Round 1) already shows that correct arithmetic is not sufficient for naive product use.  
- **This layer** becomes the **canonical labor-market intelligence foundation**: downstream recommendation and RAG systems should cite **persistent verified statistical signals** rather than re-deriving statistics ad hoc—while remaining explicitly **out of scope** for this specification’s core definition.

---

## Related documents

- `docs/verified-statistical-signal-generation-mvp.md`
- `docs/verified-statistical-signal-extraction-pilot.md`
- `docs/verified-statistical-signal-review-checklist.md`
- `docs/verified-statistical-signal-manual-review-round1.md`
- `docs/statistical-observation-schema.md`
- `docs/statistical-ingestion-pipeline-mvp.md`
- `docs/ssb-import-validation-checklist.md`
- `docs/scoring-and-signal-model.md`

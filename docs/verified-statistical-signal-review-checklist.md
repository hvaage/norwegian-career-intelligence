# Verified statistical signal — review and approval checklist

**Specification only:** this document defines the **operational review and approval framework** for deciding which **preview** `verified_statistical` signals are good enough to become **persistent** signals in the Norwegian Career Intelligence Dataset and **sokr.online**. It does **not** define SQL, import scripts, preview generators, persistence jobs, or product UI.

**Related documents:**

- `docs/verified-statistical-signal-generation-mvp.md`
- `docs/verified-statistical-signal-extraction-pilot.md`
- `docs/ssb-import-validation-checklist.md`
- `docs/statistical-observation-schema.md`
- `docs/scoring-and-signal-model.md`
- `docs/verified-statistical-signal-manual-review-round1.md` (structured manual review of preview samples)
- `docs/persistent-verified-statistical-signal-model.md` (canonical persistence and governance layer after approval)

**Current preview artifacts (examples for review practice):**

- `data/processed/signal_preview/signal_preview_rows.csv`
- `data/processed/signal_preview/signal_preview_summary.json`
- `data/processed/signal_preview/review_samples/` (`top_growth.csv`, `top_decline.csv`, `unstable_signals.csv`, `low_quality_signals.csv`)

The summary JSON includes **runtime**, expanded **skip counters** (e.g. low absolute change, both periods below baseline, invalid period pairing, slice/unit mismatches, aggregation safeguards), **quality score histograms**, and **deterministic ordering** metadata aligned with `preview_verified_statistical_signals_v1.3+`.

**Explicit exclusions from this checklist’s scope:**

- Recommendations, ranking, embeddings, forecasting  
- Overlaps, gaps, labor-market simulation  
- AI interpretation, inferred causal logic  
- Any Supabase write, migration, or signal persistence workflow  

---

## 1. Purpose of signal review

### Why preview signals must be reviewed before persistence

Preview outputs prove that observations can be read and summarized **deterministically** with lineage attached (`docs/verified-statistical-signal-extraction-pilot.md`). They do **not** prove that every row is appropriate to store as a durable product signal. Automated filters (for example `skipped_low_baseline`, `skipped_unspecified_category` in `signal_preview_summary.json`) **reduce noise**; they do **not** replace human judgment on slice sparsity, definitional fit, or product risk.

### Distinction between layers

| Concept | Meaning |
|--------|---------|
| **Observations** | Atomic measured facts in the statistical observation layer: one value, one period, one coordinate in dimension space (`docs/statistical-observation-schema.md`). |
| **Preview signals** | Rows emitted by the pilot preview path into local CSV/JSON only. Carrying `quality_flags` such as `preview_not_product_ready` is the default posture until review (`docs/verified-statistical-signal-extraction-pilot.md`). |
| **Approved signals** | Signals that have passed governance review and may be written to persistent signal storage (when that pipeline exists). |
| **Product-visible signals** | A subset of approved signals explicitly cleared for user-facing surfaces (API/UI copy, summaries). Approval for persistence **does not** automatically mean approval for product exposure. |

### Why statistically correct signals may still be low-quality product signals

A two-period comparison can be **arithmetically correct** yet **misleading in product**: tiny baselines produce extreme percentage changes; small municipalities and narrow education × region cells swing on few persons; mixed **ContentsCode** semantics (residence vs workplace) change the story without any bug. The scoring model already separates **strength** and **confidence** and warns that high inferred impact must not outweigh weak evidence (`docs/scoring-and-signal-model.md`); the same discipline applies here.

### Signal review as governance

Review is a **governance layer** between “we can compute it” and “we may ship it.” It protects:

- **Explainability** — every approved signal must remain traceable to observations and source definitions.  
- **User trust** — especially where numbers influence career decisions or public narrative.  
- **Statistical integrity** — definitions, periods, and units stay aligned with SSB intent.  
- **Recommendation quality (future)** — recommendations must not inherit noisy or ambiguous stats as if they were hard facts.  
- **Future AI layers** — summarization and retrieval must not erase lineage; review criteria should assume downstream compression.  

---

## 2. Scope of MVP review

### In scope

- **Only** preview signals with `confidence_category` aligned to the pilot’s **`verified_statistical`** path (`docs/verified-statistical-signal-extraction-pilot.md`).  
- **Only** SSB-derived preview signals tied to normalized observations for the MVP tables (`docs/ssb-import-validation-checklist.md`, `docs/statistical-observation-schema.md`).  
- **Only** these preview `signal_type` values:  
  - `employment_count_change`  
  - `regional_education_employment_signal`  
  - `industry_education_employment_signal`  
  - `occupation_structure_signal`  
  - `education_level_workforce_signal`  

### Explicit exclusions (do not treat as in-scope for this MVP review gate)

- Recommendations, ranking, embeddings, forecasting  
- Overlaps, gaps, NAV or other non-SSB sources in this gate  
- Cross-table fusion not defined in the pilot  
- AI-generated interpretation beyond the fixed `explainability_note` pattern  
- Inferred causal claims (“because X, therefore Y”)  

---

## 3. Signal lifecycle

These are **conceptual states** for governance and logging (not a database schema in this document).

| State | Meaning |
|--------|---------|
| **preview_generated** | Row exists in `signal_preview_rows.csv` / summary JSON from a preview run. |
| **review_pending** | Eligible for human or batched review; no persistence decision recorded. |
| **approved_for_persistence** | Reviewer(s) accept the row for durable storage when the persistence pipeline exists. |
| **approved_for_product** | Explicitly cleared for user-facing API/UI (stricter than persistence alone). |
| **rejected** | Not persisted; reason recorded (structural, statistical, category, product). |
| **quarantined** | Uncertain or incident-driven hold; may be re-reviewed after definition or data fixes. |
| **deprecated** | Was persisted or exposed; later withdrawn (revision, definition change, or error). |

### Key implications

- **Preview generation does not imply approval.** Script defaults and filters are pre-review hygiene only.  
- **Approval for persistence does not imply approval for UI exposure.** Internal analytics may allow slices that marketing or support surfaces should not emphasize.  

---

## 4. Signal review philosophy

Principles for MVP review:

- **Conservative over aggressive** — when in doubt, reject or quarantine rather than ship.  
- **Explainability over novelty** — interesting spikes that cannot be explained simply to a non-statistician are not MVP wins.  
- **Reproducibility over automation** — the same observation snapshot + same rules should reproduce the same signal row.  
- **Evidence over interpretation** — describe what changed between periods; do not imply causation.  
- **Stable signals over noisy signals** — prefer aggregates and slices with stable counts.  
- **Large-sample trust over micro-slice volatility** — prefer county-level or broader education bands when municipality-level swings dominate.  

### Operational truths

- **High percentage changes on small baselines are risky** even when mathematically valid; combine with minimum baseline rules and human judgment (`docs/verified-statistical-signal-extraction-pilot.md` §4b).  
- **Statistically true is not always product-useful** — correct “7 → 14” style comparisons can be true and useless or harmful in UI.  

---

## 5. Structural review checks

Verify each candidate preview row (CSV columns per `docs/verified-statistical-signal-extraction-pilot.md` §7 plus §4b):

| Check | Description | Typical severity |
|--------|-------------|------------------|
| Required fields present | `signal_type`, `table_id`, `periods_compared`, metrics as applicable, `source_observation_ids`, JSON fields parseable | **Hard-fail** if missing |
| `lineage_json` complete | Includes script/version, dataset or file lineage as available; no empty `{}` for change rows | **Hard-fail** if broken; **Warning** if partial |
| `source_observation_ids` present | UUIDs traceable to `statistical_observations` | **Hard-fail** if missing for change rows |
| `dimension_labels_json` readable | Human labels align to codes; UTF-8 and JSON valid | **Warning** if ambiguous |
| Confidence fields valid | `confidence_category` and `confidence_score` match pilot rules (0.9 change, 1.0 snapshot) | **Hard-fail** if inconsistent with signal family |
| Periods valid | Periods parseable; change rows show `start→end`; snapshots show single period | **Hard-fail** if malformed |
| No malformed labels | No truncated JSON, no obvious encoding corruption | **Hard-fail** / **Warning** |
| No missing dimensions | Slice identity dimensions present for the signal family (e.g. Region + education for regional education signal) | **Hard-fail** if slice undefined |
| `contents_code` explicitly known | `contents_code` and `contents_code_label` populated or intentionally empty with documented reason | **Warning** if ambiguous metric |

**Severity definitions**

- **Hard-fail** — do not approve for persistence until fixed or regenerated.  
- **Warning** — may persist only with documented exception or stronger aggregation.  
- **Informational** — note for metrics or training reviewers; not blocking alone.  

---

## 6. Statistical quality review

Checks beyond the preview script counters (see `signal_preview_summary.json` for examples: `skipped_zero_baseline`, `skipped_low_baseline`, `skipped_missing_prior_period`, `candidate_pairs` vs `preview_signals_generated`):

| Check | Question for reviewer | MVP guidance |
|--------|------------------------|--------------|
| Minimum baseline | Is `value_start` large enough that percent change is stable? | Align with pilot `--min-baseline` (default 100); **raise** for product if volatility remains high. |
| Minimum observation volume | Is the underlying count small enough that random noise dominates? | Treat very small integers as **warning**; prefer aggregation or rejection for product. |
| Zero-baseline handling | Was zero baseline correctly excluded (`skipped_zero_baseline`)? | Do not manually “fix” by inventing percentages. |
| Percent-change sanity | Does `%` direction match absolute change? | **Hard-fail** on inconsistency. |
| Unrealistic jumps | Does the jump contradict adjacent slices or known revisions? | **Quarantine** pending dataset version check (`docs/ssb-import-validation-checklist.md`). |
| Unstable micro-slices | Municipality × narrow education × single gender? | **Warning** or **reject** for product; may keep for internal. |
| Category sparsity | Rare `Fagfelt` or occupation codes? | **Warning**; document sparsity. |
| Missing prior periods | Elevated `skipped_missing_prior_period` in summary? | Improve fetch strategy (balanced periods) before comparing runs (`docs/verified-statistical-signal-extraction-pilot.md` §6). |
| Duplicated signals | Same slice + periods repeated? | **Hard-fail** or merge policy in open questions (§18). |

**Examples reviewers should suppress or flag for product**

- **“7 → 14” style** — large relative change on trivial absolute scale; keep out of user-facing copy unless aggregated.  
- **High volatility in tiny municipalities** — prefer county, rolling periods, or suppression rules (§18).  

---

## 7. Category quality review

### Exclusion or downgrade logic

Treat dimension label text as carrying risk when it matches reserved unspecified semantics (`docs/verified-statistical-signal-extraction-pilot.md` §4b), including **Uoppgitt**, **unknown**, **unspecified**, **not stated**, **ikke oppgitt**, and similar SSB phrasing discovered in review.

| Situation | Action |
|-----------|--------|
| Unspecified / unknown category in slice | **Reject** for product; **quarantine** for persistence until hierarchy clarified |
| Incomplete hierarchy | **Warning** — may be informational only |
| Deprecated category codes still in data | **Warning** — align with taxonomy owners |

### Informational vs product-grade

- **Informational** — correct for analysts, dashboards, or internal QA; may include sparse or awkward slices.  
- **Product-grade** — safe for generalized user language in sokr.online without heavy caveats.  

---

## 8. Dimension review

Dimensions commonly appearing in MVP preview rows include **region**, **education level** (`UtdNivaa`), **fagfelt**, **industry** (`NACE2007`), **occupation** (`Yrke`), **gender**, **age**, and **ContentsCode** (as metric, not only a dimension code).

### Safety and sparsity

- Some dimensions are **safer at coarse grain** (national, county, broad education level) than at **fine grain** (small municipality × narrow field).  
- Some **combinations** explode cardinality and become **noisy** even when each row is “true.”  

### Recommended safe MVP combinations (product-oriented)

Prioritize combinations that remain interpretable without a statistician in the loop:

- **Region (county or larger)** × **broad education level** × **SysselsatteBosted** or **SysselsatteArbSted** (chosen explicitly, never mixed in copy).  
- **Industry section level** × **broad education** for `industry_education_employment_signal`.  
- **Occupation** at **aggregated occupation family** where possible for `occupation_structure_signal` before highlighting single codes.  

Treat **municipality × narrow fagfelt × single gender** as **high scrutiny** defaulting to **non-product** unless counts clearly support stability.

---

## 9. ContentsCode review

### Distinction

- **`SysselsatteBosted`** — employment counted by **place of residence** (typical “where people live” framing).  
- **`SysselsatteArbSted`** — employment counted by **place of work** (commuting and cross-border patterns differ).  

They answer **different questions**; conflating them in narrative is a **definition error**, not a rounding error.

### Rules

- **Remain separate** in persistence and in API unless a **new, explicitly defined** aggregated signal is approved with its own lineage and label.  
- **Aggregation across ContentsCode is dangerous** for product without a documented combiner and dominance checks.  
- **Explainability must expose the distinction** — labels and API fields should make “bosted vs arbeidssted” obvious to downstream UI and any summarization layer.  

---

## 10. Explainability review

### Principle

Every **approved** signal must be explainable **after** persistence, **under** API exposure, **in** UI rendering, and **under** future AI summarization (which tends to compress nuance).

### Requirements

- **Clear `signal_label`** — identifies metric, geography/slice, and time comparison in plain language.  
- **Understandable `dimension_labels_json`** — human labels, not code soup.  
- **Traceable `source_table` / `table_id`** — SSB table id visible to support and power users.  
- **Traceable periods** — `periods_compared` unambiguous.  
- **Readable `explainability_note`** — short, factual, no causation.  
- **`lineage_json` completeness** — enough to replay provenance (`docs/ssb-import-validation-checklist.md`).  

### Example acceptable explanation pattern

> “This signal is based on **SSB table 11615**, comparing **2024 and 2025** employment counts (**Sysselsatte etter bosted**) for **engineering-related education in Oslo**.”

Reviewers should reject patterns that omit **table**, **metric (ContentsCode)**, **periods**, or **slice identity**.

---

## 11. Confidence review

### Layers

| Layer | MVP meaning |
|--------|-------------|
| **Observation confidence** | Carried from normalized observations; typically high for direct SSB cells (`docs/statistical-observation-schema.md`). |
| **Signal confidence** | Pilot uses fixed scores: **0.9** for two-period change, **1.0** for direct snapshots (`signal_preview_summary.json` `confidence_rules`). |
| **Derived confidence (future)** | Any composite or model-assisted confidence must be **lower** than direct citation when interpretation increases. |

### Principle

**Direct two-period summaries are not forecasts.** Reviewers must not treat `direction_label` as predictive; language in product must stay descriptive.

### Approved MVP confidence logic (for persistence eligibility)

- Snapshot signals: may use **1.0** only when lineage and labels are complete and slice is not quarantined for sparsity.  
- Change signals: **0.9** ceiling in pilot; reviewers may **downgrade** exposure tier even when 0.9 is numerically allowed.  

---

## 12. Product-readiness review

### Tiers

| Tier | Meaning |
|------|---------|
| **Preview-safe** | OK for internal CSV review and QA; may still carry `preview_not_product_ready`. |
| **Persistence-safe** | OK to store for analytics, audit, or internal APIs after structural + statistical gates. |
| **Product-safe** | OK for generalized user-facing narrative, default dashboards, or marketing-adjacent surfaces. |

### Guidance

- Some signals are **useful internally** but **unsafe for user-facing UI** (noisy municipality, ambiguous category, or volatile small baseline even above minimum threshold).  
- **Examples** — stable regional trends at county level: often **product-safe**; noisy municipality slices or **unspecified** categories: **not product-safe**; **tiny baseline** changes: usually **not product-safe** unless aggregated.  

---

## 13. Human review workflow

### Who reviews

- **Owner** — data/statistics steward signs off on definitions and ContentsCode semantics.  
- **Reviewer** — second person for product-visible tier (four-eyes on user-facing).  

### What gets sampled

- Stratified sample by **`signal_type`**, **`table_id`**, **`contents_code`**, and **geographic granularity**.  
- Oversample high `|percent_change|` and low `value_start` near threshold.  

### What gets spot-checked

- Random draws against **raw observations** in Supabase (read-only) for UUIDs in `source_observation_ids`.  
- Comparison to **CSV preview** row for the same signal id or composite key.  

### Frequency

- After each **material import or normalization version change** (`docs/ssb-import-validation-checklist.md`).  
- After preview script **version bump** (see `script_version` in `signal_preview_summary.json`).  

### Approval logging (when persistence exists)

- Reviewer id, timestamp, decision, state transition, optional comment, link to observation batch or dataset version.  

### Quarantine handling

- Move state to **quarantined** on suspected regression; unblock only with root-cause note and optional re-run of preview.  

### Deterministic replay checks

- Same **source file + normalization_version + transformation_version** + script version should reproduce preview outputs within documented limits (`lineage_json`).  

---

## 14. Review metrics

Track over time (even manually at first); these metrics become **governance signals** themselves:

| Metric | Definition |
|--------|------------|
| **Approved rate** | Approved ÷ review_pending closed. |
| **Rejection rate** | Rejected ÷ review_pending closed. |
| **Quarantine rate** | Quarantined ÷ all decisions. |
| **Low-baseline suppression rate** | From summary: `skipped_low_baseline` ÷ relevant candidate base (e.g. `candidate_pairs` or pair attempts). |
| **Unspecified-category suppression rate** | `skipped_unspecified_category` ÷ comparable base. |
| **Duplicate rate** | Duplicates found ÷ approved (should trend to zero). |
| **Explainability completeness** | Share of approved rows passing §10 checklist. |
| **Lineage completeness** | Share with full `lineage_json` fields populated. |

Example snapshot reference (one historical run): `signal_preview_summary.json` showed `preview_signals_generated: 2623` with `skipped_low_baseline: 6881` and `skipped_unspecified_category: 1610` for a scoped regional run — use such ratios to calibrate sampling intensity, not as quality targets in isolation.

---

## 15. Approval criteria

### When a preview signal may become **persistent**

- Passes **§5 structural** checks at **hard-fail** level.  
- Passes **§6 statistical** checks at least at **warning** resolution (warnings documented).  
- Passes **§7 category** and **§9 ContentsCode** semantics.  
- **Explainability** (§10) and **confidence** (§11) are internally consistent.  

### When it may become **product-visible**

- Meets persistence criteria **and** **§12 product-safe** tier.  
- No ambiguous **ContentsCode** or residence/workplace framing in user copy.  
- Slice is not known-sparse without explicit UI caveats (if caveats are disallowed, **reject** for product).  

### When it must be **rejected** or **quarantined**

**Hard-fail conditions** — missing lineage, missing observation ids (for change signals), malformed JSON, wrong table/period, inconsistent arithmetic, unspecified category in product path, or conflated ContentsCode semantics.

**Warning escalation** — repeated warnings on same slice family → **quarantine** until aggregation or suppression policy is decided.

**Quarantine logic** — dataset revision, importer change, or spike in `skipped_missing_prior_period` / duplicate rate without explanation.

---

## 16. MVP limitations

- **Only SSB** observations in scope for this MVP gate.  
- **Only a few tables** (`11615`, `12850`, `09793`, `08417` per validation checklist).  
- **No causal inference** — direction labels describe change, not causes.  
- **No predictive confidence** — not forecasts.  
- **No recommendation scoring** — out of scope for this checklist.  
- **No embeddings** — out of scope.  
- **No labor-market simulation** — out of scope.  

---

## 17. Future evolution

Topics for later versions of this checklist (not MVP requirements):

- **NAV integration** — mixing administrative and statistical evidence with explicit confidence downgrades.  
- **Confidence calibration** — empirically tuned bands beyond fixed 0.9 / 1.0.  
- **Benchmark normalization** — peer comparisons with fair baselines.  
- **Cohort-level signals** — stable populations vs open cohort noise.  
- **Forecasting** — separate review class with model cards.  
- **AI-assisted review** — human-in-the-loop suggestions only; no auto-approval without policy.  
- **Anomaly detection** — flagging outliers for quarantine.  
- **Statistical benchmarking** — uncertainty intervals where product promises stability.  
- **Graph-based signal lineage** — multi-hop evidence graphs with review on edges.  

---

## 18. Open questions

Record decisions here as the program matures:

- **Signal uniqueness strategy** — idempotency key: slice + periods + contents_code + signal_type + normalization_version?  
- **Persistence granularity** — one row per slice vs rolled-up variants stored separately.  
- **Aggregation persistence** — when pre-aggregated signals become first-class vs computed in API.  
- **Municipality suppression rules** — minimum count thresholds, k-anonymity, or geographic roll-up.  
- **Confidence calibration** — mapping statistical noise to user-facing confidence language.  
- **Volatility handling** — multi-year windows vs single-year jump.  
- **Signal versioning** — invalidation on importer or definition change.  
- **Reviewer governance** — rotation, escalation, and regulatory readiness.  
- **Explainability storage strategy** — whether `explainability_note` is duplicated in API payloads vs generated from templates.  

---

## Summary

- **Preview signals are not automatically trustworthy** — generation proves feasibility, not fitness.  
- **Review is mandatory before persistence** — automated filters are necessary but insufficient.  
- **Explainability and lineage are non-negotiable** for anything that might surface to users or feed future recommendations.  
- **Stable statistical governance** for `verified_statistical` signals must exist **before** expanding into recommendations, ranking, AI interpretation, or broad product exposure.

When in doubt: **reject**, **quarantine**, or **approve for persistence only** until product criteria are explicit and measurable.

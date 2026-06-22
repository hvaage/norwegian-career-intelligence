# Manual review — verified statistical signal preview (Round 1)

**Specification / governance note:** this document records a **structured manual review** of preview outputs only. It does **not** authorize persistence, SQL, product UI, recommendations, gaps/overlaps, or embeddings.

**Review date:** 2026-05-11 (aligned with `signal_preview_summary.json` timestamp).

**Artifacts reviewed:**

- `data/processed/signal_preview/review_samples/top_growth.csv`
- `data/processed/signal_preview/review_samples/top_decline.csv`
- `data/processed/signal_preview/review_samples/low_quality_signals.csv`

**Context from `data/processed/signal_preview/signal_preview_summary.json`:**

- `script_version`: `preview_verified_statistical_signals_v1.3`
- `signal_logic_version`: `verified_stat_preview_emit_v1.3.0`
- Run: `regional_education_employment_signal`, `table_id` **11615**, `--balanced-periods`, `--limit` 20000 per period, **no** `--contents-code` (both `SysselsatteArbSted` and `SysselsatteBosted` appear across rows).
- `periods_selected`: 2024 → 2025 (annual granularity).
- `preview_signals_generated`: **1283**
- `quality_score_distribution`: 0 in bins below 0.6; **371** in 0.6–0.8; **912** in 0.8–1.0.

**Reference frameworks:**

- `docs/verified-statistical-signal-review-checklist.md`
- `docs/verified-statistical-signal-extraction-pilot.md`
- `docs/verified-statistical-signal-generation-mvp.md`
- `docs/statistical-observation-schema.md`

---

## Executive summary

The v1.3 preview pipeline produces **arithmetically consistent**, **slice-aligned**, and **heavily documented** regional education employment signals for SSB table **11615** between **2024** and **2025**. `explainability_note`, `explainability_summary_json`, `lineage_json`, and `signal_deterministic_hash` make rows **operationally reviewable** and suitable for **repeatable audit**.

However, **top growth** still includes **high percentage moves on modest person-counts** (e.g. `value_start` in the low 100s), and **both workplace and residence metrics** interleave when `contents_code` is not fixed at export time—**statistically legitimate** but **easy to misread** in product. **Lineage** is strong on file and normalization identifiers but **`importer_version` is `unknown`** and **`dataset_version_id` is null**, which weakens long-term reproducibility claims until the importer populates those fields.

**Verdict:** preview quality is **good enough for continued governance and persistence *design***; **not** yet a blanket “persist all rows” without per-row or stratum-level review and a **deduplication / uniqueness** policy.

---

## 1. Review: `top_growth.csv`

### Checks performed

| Criterion | Finding |
|-----------|---------|
| Largest growth believable? | Mostly yes for **directional** labor-market narrative; magnitudes in the **15–32%** band on **100–250 persons** are **noisy but plausible** year-on-year for narrow slices. |
| Tiny baselines? | **`min_baseline` 100** removes very small starts; remaining top rows still sit near the floor (e.g. **100–139**), so **relative % remains volatile**. |
| Unspecified categories? | No reserved unspecified phrases observed in sampled labels; aligns with `skipped_unspecified_category` mass in summary. |
| Noisy rows? | Rows combining **small kommune** + **narrow fagfelt** + **university level** should be treated as **review-only** for public copy. |
| ContentsCode mixing? | **Not within a row**—each row has a single `contents_code`. **The file mixes** `SysselsatteArbSted` and `SysselsatteBosted` across rows; readers must not aggregate without explicit rules (`docs/verified-statistical-signal-review-checklist.md` §9). |
| Region / fagfelt / utdanning explainable? | Yes: labels are human-readable and consistent with `dimensions_json`. |
| % vs absolute aligned? | Spot checks: `percent_change` matches `(value_end - value_start) / value_start * 100` within rounding. |
| Explainability notes | Long but **accurate**: thresholds, exclusions, units, and direction rules are spelled out—suitable for analyst onboarding. |
| `lineage_json` complete? | **Partially:** `statistical_dataset_id`, `source_file`, `normalization_version`, `transformation_version`, `ingestion_batch_id`, and **two** `observation_signature`s are present. **`importer_version`: `unknown`**, **`dataset_version_id`**: null. |
| Deterministic hashes | **Present** and **non-empty** on sampled rows; distinct slices produce distinct hashes. |

### Strengths

- Clear **bosted vs arbeidssted** labeling in `signal_label` and `contents_code_label`.
- **Annual period** consistency (`period_granularity`: `year`).
- **Quality score** correlates inversely with baseline size in the growth tail (lower scores when `value_start` is smaller).

### Weaknesses

- **Product volatility:** top-ranked growth is still driven by **moderate N** cells; narrative strength exceeds statistical comfort for **default UI**.
- **Mixed metrics in one sample file** when `contents_code` filter is off—increases cognitive load for reviewers.

### Suspicious / spotlight rows (examples from sample)

- **Moss / Lærerutdanning / Kvinner / Univ. long** — `value_start` **139**, **+31.7%**: arithmetically fine; **policy** should decide if this is headline-worthy.
- **Boundary** `value_start` **100** rows — pass filter by definition but sit at **maximum relative noise**.

### Recommended additional filters (growth / product)

1. **Raise `min_baseline`** for product-tier exports (e.g. 250–500 for kommune-level) *or* require **county roll-up** for publication.
2. When reviewing, **run separate previews** per `--contents-code` to avoid mixing residence and workplace in one mental model.
3. Optional **maximum % cap** for persistence (e.g. flag or drop if `% > X` and `value_start < Y`)—governance, not statistics.

### Recommendation (top growth)

**Needs tuning** before default persistence or public surfacing; **acceptable** for internal preview and checklist-driven review.

---

## 2. Review: `top_decline.csv`

### Checks performed

| Criterion | Finding |
|-----------|---------|
| Plausible declines? | Yes; mix of **small-area** volatility and **larger** cells (e.g. **Bærum** / **Asker** slices with hundreds of persons). |
| Collapse-to-zero? | Sampled rows retain **non-zero** `value_end`; zero-baseline path remains covered by `skipped_zero_baseline` in summary, not in decline tail. |
| Unstable tiny slices? | Frequent **kommune** + **narrow education** combinations; large **negative %** on ~**100–160** persons is expected under sampling noise. |
| Low absolute change leaking? | Rows respect **`min_absolute_change` ≥ 10** in the sample (e.g. −10, −11, −12 at the milder end). |
| Decline thresholds | **±5%** band for growth/decline vs stable is **coarse but consistent** with pilot docs; appropriate for **screening**, not significance testing. |
| Small municipality distortion? | **Yes pattern:** Grue, Alvdal, Sør-Fron, Hole, etc. appear—should default to **review-only** tier. |
| Misleading operationally? | Without caveats, **“−40% employment”** in a **130-person** slice is **true but easy to misinterpret** as structural collapse. |

### Strengths

- **`unstable_slice`** flag appears where the scorer penalizes **high |%|** with **moderate baseline** (e.g. **−43%** decline row)—useful triage.
- **Large-N** declines (e.g. **2487 → 2170**, **−12.7%**) are **credible macro slices** for storytelling with lighter caveats.

### Weaknesses

- Same **kommune × narrow field** pattern as growth: **reviewer fatigue** without automated **geo tier** (kommune vs county).

### Suspicious / spotlight rows

- **Lier / Naturvitenskapelige fag / Kvinner / Univ. long** — **139 → 79**, **−43%**, `unstable_slice` flagged — **keep for diagnostics**, **block** for naive UI.
- **Alvdal** and similar **small kommuner** with double-digit % moves — **review-only**.

### Recommended additional filters

1. **Geo population proxy** or **SSB disclosure rule** alignment before persistence (even if approximate).
2. **Symmetric** handling: apply the same **volatility score** to large **negative** % as to large **positive** % for product gates.

### Recommendation (top decline)

**Needs tuning** for product; **acceptable** for internal preview and governance iteration.

---

## 3. Review: `low_quality_signals.csv`

### Why rows are “low quality”

Rows are ordered by **ascending `signal_quality_score`**. In sampled rows, `quality_reasoning_json` shows **low `baseline_size`** and/or **`absolute_change_magnitude`** components near the floor—**deterministic** and **interpretable**.

### Quality flags

- Most rows carry `preview_not_product_ready`, `two_period_change`, `min_baseline_met(>=100.0)`, `no_blocked_unspecified_phrase_in_labels`.
- **`unstable_slice`** appears where `%` is large relative to baseline in the scorer’s heuristic.

### Recurring patterns

- **`value_start` at or just above 100** with **|Δ| at or just above 10** — exactly at **filter edges** → lowest scores.
- **Mix of growth and decline** at the bottom—low score is **not** direction-specific; it is **magnitude / baseline** driven.

### Unspecified / missing labels

- No evidence of **forbidden unspecified** text in sampled labels.
- **`dimension_labels_present`** in `explainability_summary_json` shows **true** for included keys.

### Score vs intuition

- **Aligned** for triage: borderline thresholds produce **~0.60** scores; comfortable baselines produce **>0.9**.

### Duplicate artifact (review sample)

- The sample file contains **repeated rows with identical `signal_deterministic_hash`** (same slice and observation ids). That should be **investigated upstream** (preview dedupe vs data duplication). **Do not persist** without a **uniqueness key** policy (`docs/verified-statistical-signal-review-checklist.md` §18).

### Suggested hard filters (future)

1. **Dedupe** on `signal_deterministic_hash` before persistence.
2. Optional **minimum `value_end`** for product tier.
3. **Stratified review quotas** by `Region` population class.

### Suggested scoring improvements

- Add **explicit margin above thresholds** (distance from `min_baseline` / `min_absolute_change`) as a first-class component.
- Penalize **kommune** tier unless **N** proxy exceeds a floor.

### Recommendation (low quality)

**Acceptable** as a **review queue**; file fulfills its purpose. **Needs tuning** in the **generator** if duplicate hashes are confirmed in `signal_preview_rows.csv`.

---

## 4. Cross-file comparison

| Question | Conclusion |
|-----------|------------|
| Filters improving quality? | **Yes materially:** summary shows large `skipped_both_periods_below_baseline`, `skipped_low_absolute_change`, and `skipped_unspecified_category`—the emitted 1283 rows are a **thin, higher-signal** slice of 11 299 candidate pairs. |
| Deterministic ordering? | **Yes** within files: growth sorted by `%` desc then stable keys; decline by `%` asc; low quality by score asc. **Hashes** stable for the same slice. |
| Human-understandable explanations? | **Yes**, with verbosity acceptable for pilot; consider a **short** consumer field later. |
| Lineage sufficient for audit? | **Partially:** strong on **SSB file + normalization + transformation**; **weak** on **importer semantic version** and **dataset version UUID**. |
| Permanent exclusions | Reserved unspecified phrases—**keep** global exclusion for product. |
| Review-only | **Kommune × narrow fagfelt × narrow utdanning** cells—default **review-only**. |
| Warning-only | **Mid-size** kommune with **10–25** person absolute change—**warning-only** before UI. |

### Recurring dimensions / geographies

- **Small inland / peripheral kommuner** recur in both growth and decline tails.
- **Oslo–Oslove**, **Drammen**, **Bærum**, **Lillestrøm** appear in **larger-N** contexts—more **stable** for messaging.

### Recurring low-baseline / % issues

- **`value_start` 100–130** with **double-digit %** remains the dominant “**statistically OK, product risky**” pattern.

---

## 5. Persistence readiness assessment

| Track | Classification | Rationale |
|-------|----------------|-----------|
| **First persistence schema design** | **Safe for pilot** | Observations already normalized; preview rows expose the **payload** you would persist (`signal_type`, slice JSON, periods, metrics, lineage, hash). |
| **First persistent `verified_statistical` signal table** | **Partially ready** | Need **uniqueness / idempotency**, **dedupe**, **`dataset_version_id` / importer version** propagation, and **ContentsCode** policy before bulk insert. |
| **First persistence workflow MVP** | **Partially ready** | Checklist states (`review-checklist`) are defined; automation must enforce **review states** and **reject/quarantine** paths. |

**Why not “fully ready”:** `importer_version` unknown, `dataset_version_id` null, potential **duplicate hash** in review sample, and **product-tier** rules still **manual**.

---

## 6. Recommendations

### Immediate tuning

1. Re-run preview with **`--contents-code SysselsatteBosted`** and separately **`SysselsatteArbSted`** for reviewer clarity.
2. **Investigate duplicate `signal_deterministic_hash`** in `signal_preview_rows.csv`; add **assertion** in CI (`--preview-report-only` + hash uniqueness).
3. Populate **`metadata_json.importer_version`** (or equivalent) during SSB import for stronger lineage.

### Medium-term

- **Population-weighted** or **county-aggregated** companion signals for product.
- **Statistical significance / MOE** even as crude heuristics (not ML).
- **Versioned threshold** tables stored beside signals for reproducibility.

### Statistical governance

- Publish **tier tables**: internal analytics vs review-only vs product.
- Require **two-person** approval for product tier (`review-checklist` §13).

### Explainability

- Add optional **`explainability_short`** (1–2 sentences) for UI while retaining the long note for audit.

### Review workflow

- Attach **`signal_preview_summary.json`** to each review ticket; track **decision** per `signal_deterministic_hash`.

---

## 7. Final summary — major findings, risks, next actions

### Major findings

1. **Preview outputs are coherent** with SSB observation semantics (`statistical-observation-schema.md`).
2. **Filters materially reduce junk** (see summary skip counters).
3. **Tail rows remain volatile** despite `min_baseline` / `min_absolute_change`—expected for kommune-level labor statistics.

### Strengths

- **Slice integrity**, **unit match**, **ContentsCode** discipline **per row**.
- **Rich JSON** for machine audit + **CSV** for human scan.
- **Deterministic hashes** and **sorted dimensions** support diff-based regression.

### Critical risks

- **Misinterpretation** of **bosted vs arbeidssted** if exports are merged carelessly.
- **Over-trust** in **%-change** for **small N** without disclosure context.
- **Lineage gaps** (`importer_version` unknown, `dataset_version_id` null) under long-horizon audit.

### Recommended next actions

1. **Confirm / fix duplicate** preview rows (hash-level uniqueness).
2. **Importer change** to stamp **version + dataset_version_id** on observations.
3. **Round 2 review** after threshold tuning and optional **dedupe** patch—update this doc or add `...-round2.md`.

### Persistence readiness decision

**Partially ready:** proceed with **schema design** and **pilot persistence** behind feature flags **only** with **hash-level idempotency**, **ContentsCode policy**, and **review state** enforcement. **Do not** enable bulk product surfacing without the additional filters and lineage fixes above.

---

## Related documents

- `docs/verified-statistical-signal-review-checklist.md`
- `docs/verified-statistical-signal-extraction-pilot.md`
- `docs/verified-statistical-signal-generation-mvp.md`
- `docs/statistical-observation-schema.md`

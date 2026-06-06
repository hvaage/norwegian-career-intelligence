# Persistent verified statistical signal — persistence workflow (MVP)

**Specification and operational guide:** this document describes the **first controlled persistence workflow** for manually review-approved `verified_statistical` preview signals. It complements:

- `docs/persistent-verified-statistical-signal-model.md`
- `docs/verified-statistical-signal-review-checklist.md`
- `docs/verified-statistical-signal-manual-review-round1.md`
- `docs/verified-statistical-signal-extraction-pilot.md`
- `docs/verified-statistical-signal-generation-mvp.md`
- `docs/statistical-observation-schema.md`
- `sql/004_verified_statistical_signals.sql`

**Script:** `scripts/persist_verified_statistical_signals.py`

**Explicit non-goals:** no recommendations, gaps, overlaps, RAG/vector tables, candidate scoring, forecasting, frontend, orchestration jobs, observation mutation, or changes to existing SQL migrations.

---

## 1. Purpose

Preview CSV/JSON proves **deterministic generation** and **explainability** locally. **Persistence** is a separate, **governance-gated** promotion: only rows that pass **strict eligibility**, **duplicate checks**, and **observation existence validation** may be inserted into Supabase tables defined in **004**.

This MVP persists **only** what a human reviewer would treat as **product-safe statistical evidence** under elevated thresholds—stricter than default preview emission.

---

## 2. Persistence flow (end-to-end)

### 2a. Default path (no `--review-decisions-file`)

```text
signal_preview_rows.csv
    → load + parse JSON fields
    → signal_type filter (MVP allowlist)
    → eligibility rules (confidence, quality, flags, lineage, observations)
    → optional --limit (cap count after eligibility)
    → duplicate check (existing signal_deterministic_hash in DB)
    → observation fetch (statistical_observations read-only)
    → (non–dry-run) INSERT batch → signals → sources → reviews
    → persistence_summary.json + preview CSVs
```

### 2b. With `--review-decisions-file` (manual review decisions)

```text
signal_preview_rows.csv + manual_review_decisions.csv
    → load preview + load decisions (validated CSV; duplicate hash → last row wins)
    → signal_type filter (unchanged)
    → for each preview row:
          • hash not in decision file → reject not_review_approved
          • decision reject / quarantine / needs_more_review → reject with explicit reason
          • decision approve_for_persistence → run statistical gates with ONLY
            preview_not_product_ready bypassed (see §3)
    → optional --limit, duplicate check, observation fetch, inserts, outputs (same as 2a)
```

**Template CSV (placeholders only):** `data/processed/persistent_signal_preview/manual_review_decisions.example.csv`
Copy it, replace hashes with real `signal_deterministic_hash` values from `signal_preview_rows.csv` after completing `docs/verified-statistical-signal-review-checklist.md`, and pass the path via `--review-decisions-file`.

---

## 3. Governance gates

| Gate | Intent |
|------|--------|
| **Allowlisted `signal_type`** | Only `regional_education_employment_signal` and `industry_education_employment_signal` (MVP scope). |
| **Elevated scores** | `confidence_score >= 0.9`, `signal_quality_score >= 0.8`, `confidence_category = verified_statistical`. |
| **Baseline and absolute change** | `value_start >= min_baseline` and `abs(absolute_change) >= min_absolute_change` (thresholds from row / `explainability_summary_json`, aligned with preview semantics). |
| **Blocked `quality_flags` (default)** | Rows containing any of: `unstable_slice`, `preview_not_product_ready`, `unspecified_category`, `unspecified_dimension`, `total_category` are **rejected**. |
| **Blocked `quality_flags` (with review file + approve)** | Same list **except** `preview_not_product_ready` may be present if and only if the decision file contains `approve_for_persistence` for that hash. **All other flags remain hard blockers** (statistical / slice integrity). |
| **Decision file allowlist (when file provided)** | Only hashes listed with `approve_for_persistence` may become eligible; every other preview row is `not_review_approved` or rejected per explicit decision. |
| **Mandatory narrative + lineage** | Non-empty `explainability_note`, non-empty `lineage_json`, non-empty `signal_deterministic_hash`, non-empty `source_observation_ids`. |
| **Observation existence** | Every referenced `statistical_observations.id` must exist; otherwise the row is **rejected** (no partial persist). |
| **Duplicate hash** | If `signal_deterministic_hash` already exists in `verified_statistical_signals`, the row is **skipped** (no overwrite). |
| **Manual reviewer identity** | Without a decision file: `--reviewer-id` stamps batch/reviews. With a decision file and approve: **CSV `reviewer_id`** is used on the persisted review row (and in signal `metadata_json.manual_review_decision`); CLI `--reviewer-id` is still recorded as `cli_reviewer_id` in review metadata for operator traceability. |

**Preview generation is not approval:** the script never treats “present in preview CSV” as consent; rules above model **manual review round 1** plus stricter automation.

### 3b. Human override boundary (`preview_not_product_ready` only)

- **Default remains strict:** without `--review-decisions-file`, `preview_not_product_ready` is still a **blocking** flag. That keeps automation and accidental “fat finger” runs aligned with the extraction pilot posture.
- **Override is narrow:** `approve_for_persistence` means a human attests the row is acceptable **despite** the preview-only flag—not that statistics, lineage, or scores can be ignored.
- **Not bypassed by approval:** low confidence, low `signal_quality_score`, `unstable_slice`, unspecified/total category flags, missing lineage/hash/observations, and duplicate hashes **cannot** be waived via the decision file; they remain **hard fails**.

Metadata written on persist includes `preview_not_product_ready_override: true` on signals (and parallel fields on reviews) when the bypass applied, for audit.

---

## 4. Why persistence is conservative

- Default preview attaches `preview_not_product_ready` to emitted rows (`docs/verified-statistical-signal-extraction-pilot.md`). **Without a decision file, this workflow rejects any row that still carries that flag**, so **most raw preview files will persist zero rows** until preview output changes.
- With a **manual review decisions** file, humans may **explicitly** approve specific hashes for persistence; that is the only supported path to persist while the flag remains on the preview row.
- Small-area volatility and mixed `ContentsCode` semantics (Round 1 review) motivate **high score floors** and **non-waivable** quality flags (other than `preview_not_product_ready` when approved).
- **No silent promotion:** inserts only run outside `--dry-run` and only for rows that pass **all** gates for the chosen mode.

---

## 5. Database mapping (004 schema)

| Concept | Stored as |
|---------|-----------|
| **Batch “completed”** (workflow language) | `verified_statistical_signal_batches.status = 'persisted'` (SQL CHECK has no `completed`; `persisted` means batch write finished). |
| **Batch review** | `verified_statistical_signal_batches.review_status = 'approved'`. |
| **Signal lifecycle** | `verified_statistical_signals.lifecycle_status = 'active'`. |
| **Signal review** | `verified_statistical_signals.review_status = 'approved'`. |
| **Eligibility tier** | `persistence_eligibility = 'eligible'`. |
| **Signal stability** (not a column in 004) | `verified_statistical_signals.metadata_json.signal_stability = 'stable'`. |

---

## 6. Replay and idempotency

- **Deterministic identity:** `signal_deterministic_hash` (from preview) is **globally unique** in `verified_statistical_signals` (004). Re-running the script with the same preview row **does not insert a second signal**; it counts as **duplicate skipped**.
- **Replay:** persisted `lineage_json` retains `signal_logic_version`, `preview_script_version`, `generation_timestamp_utc`, normalization/transformation versions, dataset ids, and **`source_observation_ids`** for audit.
- **No observation mutation:** sources are **read** from `statistical_observations`; inserts go only into `verified_statistical_signal_sources` and related signal tables.

---

## 7. Duplicate prevention

1. **Pre-insert SELECT** on `verified_statistical_signals.signal_deterministic_hash` for candidates in the current run chunk.
2. **Database UNIQUE** on hash remains the final backstop if two writers race (second insert fails; script today is single-threaded).

---

## 8. Lineage inheritance

For each persisted signal:

- `lineage_json` is the preview lineage **plus** explicit `source_observation_ids` array (canonical list for auditors).
- `verified_statistical_signal_sources` stores **one row per** source observation id with denormalized `table_id`, `source_file`, `period`, `value`, `unit`, `dimensions_json`, `dimension_labels_json`, and `observation_signature` when present.

Required lineage keys are expected to be present in preview output (see extraction pilot); empty `source_dataset_version_ids` is allowed but should be improved at import time (Round 1).

---

## 9. Review inheritance

For each persisted signal, one row in `verified_statistical_signal_reviews`:

- `signal_id` and `batch_id` both set (traceability).
- `review_status = 'approved'`, `review_round = 'manual_review_round1'`.
- `decision = 'approve'`, `decision_reason` prefers the decision file’s `review_notes` when `--review-decisions-file` was used, else `--review-notes`.
- `metadata_json.reviewer_type = 'manual_review_round1'`.
- When a decision file was used for that hash: `metadata_json.manual_review_decision` (full CSV row), `review_decisions_file`, `preview_not_product_ready_override`, and `cli_reviewer_id` (the `--reviewer-id` that ran the script).

This is an **append-style governance event**, not a product score.

---

## 10. Explainability guarantees

Persisted rows carry through:

- `explainability_note`, `explainability_summary_json`, `dimensions_json`, `dimension_labels_json`, `quality_flags`, `quality_reasoning_json`.

The script does **not** shorten or rewrite explainability text.

---

## 11. Quarantine behavior

This MVP script **does not insert quarantined signals**. Rows that would be quarantined under checklist policy should be **filtered out earlier** (or rejected by flags). Future work: write `persistence_eligibility = review_only` / `lifecycle_status = quarantined` via a separate steward tool or SQL.

---

## 12. Outputs (local)

Written under `data/processed/persistent_signal_preview/`:

| File | Purpose |
|------|---------|
| `persistence_summary.json` | Counts, reject reason histogram, batch id (if any), runtime. |
| `persisted_signals_preview.csv` | Rows that **would be** or **were** persisted. |
| `rejected_signals_preview.csv` | All rejected/skipped rows with `reject_reason`. |

---

## 13. CLI

| Flag | Purpose |
|------|---------|
| `--preview-csv` | Override path to `signal_preview_rows.csv`. |
| `--limit` | Max rows to take from the **eligible** pool (after rules, before duplicate skip). |
| `--signal-type` | Restrict to one allowlisted type. |
| `--dry-run` | Full validation + files; **no INSERTs**. |
| `--reviewer-id` | Recorded on batch + reviews (default: env `PERSISTENCE_REVIEWER_ID` or `manual_reviewer`). |
| `--review-notes` | Free-text governance notes. |
| `--review-decisions-file` | Path to manual review CSV (see §16). When set, **only** `approve_for_persistence` hashes may persist; `preview_not_product_ready` is the **only** waivable flag. |

**Environment (live mode):** `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (via `python-dotenv`). **Dry-run** benefits from the same variables to run **duplicate** and **observation** checks; without them, those checks are skipped with a warning.

---

## 16. Manual review decisions CSV

**Required columns** (header row, exact names):

| Column | Meaning |
|--------|---------|
| `signal_deterministic_hash` | Must match preview row hash (64-char hex from preview). |
| `review_decision` | One of: `approve_for_persistence`, `reject`, `quarantine`, `needs_more_review` (case-insensitive). |
| `reviewer_id` | Non-empty for every `approve_for_persistence` row (governance identity). |
| `reviewed_at` | ISO-8601 timestamp (informational; stored in metadata). |
| `review_notes` | Free text (stored on signal/review metadata and `decision_reason` when present). |

**Validation:** invalid `review_decision` or missing `reviewer_id` on an approve line causes the script to **exit with error** at load time (fail-fast). Duplicate hashes: **last row wins**.

**Orphaned hashes:** hashes present in the decision file but absent from the preview CSV are reported in `persistence_summary.json` as `orphaned_decision_hashes_count` / list under batch metadata for steward cleanup.

---

## 14. Future automation boundaries

Safe extensions later:

- CI job running `--dry-run` + threshold assertions on `persistence_summary.json`.
- Optional `auto_validated` review status **only** if checklist automation is formally approved.
- Partial unique indexes / supersession chains (see persistent model open questions).

**Out of scope for this MVP script:** scheduled jobs, auto-persist on preview, ML anomaly detection, cross-table fusion.

---

## 15. Operational checklist before first production persist

1. Apply `sql/004_verified_statistical_signals.sql` in Supabase.
2. Run `--dry-run` and inspect `reject_reason` distribution.
3. If zero rows persist without a decision file, use **`--review-decisions-file`** with checklist-approved hashes, or regenerate preview when policy allows.
4. Run live persist with a named `--reviewer-id`, a versioned decision CSV (if used), and archived preview/summary paths.
5. Spot-check `verified_statistical_signal_sources` join to observations.

---

## Related documents

- `docs/persistent-verified-statistical-signal-model.md`
- `docs/verified-statistical-signal-review-checklist.md`
- `docs/verified-statistical-signal-manual-review-round1.md`
- `sql/004_verified_statistical_signals.sql`

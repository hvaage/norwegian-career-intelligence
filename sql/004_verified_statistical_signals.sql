-- =============================================================================
-- Norwegian Career Intelligence — persistent verified statistical signals (004)
-- =============================================================================
-- First persistence layer for governance-approved verified_statistical signals.
-- Aligns with docs/persistent-verified-statistical-signal-model.md and review
-- checklist; does NOT create recommendations, gaps, overlaps, RAG, embeddings,
-- import jobs, or application persistence scripts.
--
-- Re-runs: CREATE TABLE/INDEX IF NOT EXISTS; DROP TRIGGER IF EXISTS before
-- CREATE TRIGGER; CREATE OR REPLACE set_updated_at (same body as 001/002).
-- Re-run does NOT alter existing columns/constraints (PostgreSQL limitation).
--
-- Safety: no INSERT/UPDATE; does not mutate statistical_observations.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Extension support for gen_random_uuid()
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- -----------------------------------------------------------------------------
-- updated_at trigger support (idempotent replace; matches 001/002 body)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.set_updated_at() IS
  'Sets NEW.updated_at to transaction time; attach to tables with updated_at column.';

-- =============================================================================
-- A) verified_statistical_signal_batches
-- =============================================================================
-- One controlled persistence batch (e.g. from reviewed preview CSV + summary).
-- Preview signals are NOT persisted automatically; batches track promotion.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.verified_statistical_signal_batches (
  id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_slug                 text NOT NULL,
  source_preview_file        text,
  source_summary_file        text,
  signal_logic_version       text NOT NULL,
  preview_script_version     text,
  persistence_logic_version  text,
  generation_timestamp       timestamptz,
  persisted_at               timestamptz,
  status                     text NOT NULL DEFAULT 'pending',
  review_status              text NOT NULL DEFAULT 'not_reviewed',
  reviewer_id                text,
  approved_by                text,
  approved_at                timestamptz,
  notes                      text,
  metadata_json              jsonb NOT NULL DEFAULT '{}'::jsonb,
  quality_summary_json       jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at                 timestamptz NOT NULL DEFAULT now(),
  updated_at                 timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_verified_statistical_signal_batches_slug UNIQUE (batch_slug),
  CONSTRAINT verified_statistical_signal_batches_slug_nonempty CHECK (
    length(trim(batch_slug)) > 0
  ),
  CONSTRAINT verified_statistical_signal_batches_status_check CHECK (
    status IN (
      'pending',
      'approved',
      'persisted',
      'quarantined',
      'rejected',
      'superseded',
      'archived'
    )
  ),
  CONSTRAINT verified_statistical_signal_batches_review_status_check CHECK (
    review_status IN (
      'not_reviewed',
      'pending_review',
      'in_review',
      'approved',
      'rejected',
      'conditionally_approved',
      'quarantined'
    )
  )
);

COMMENT ON TABLE public.verified_statistical_signal_batches IS
  'Governance container for one promotion of reviewed preview output into durable '
  'verified_statistical signals. Preview generation alone does not populate this table.';

COMMENT ON COLUMN public.verified_statistical_signal_batches.batch_slug IS
  'Human- and log-friendly unique identifier for the batch (e.g. run id + table scope).';

COMMENT ON COLUMN public.verified_statistical_signal_batches.status IS
  'Batch workflow: pending → approved → persisted, or terminal quarantined/rejected/superseded/archived.';

COMMENT ON COLUMN public.verified_statistical_signal_batches.review_status IS
  'Governance review state for the batch (distinct from per-signal review rows).';

COMMENT ON COLUMN public.verified_statistical_signal_batches.quality_summary_json IS
  'Optional copy of preview summary counters / histograms for audit (signal_preview_summary.json shape).';

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signal_batches_batch_slug
  ON public.verified_statistical_signal_batches (batch_slug);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signal_batches_status
  ON public.verified_statistical_signal_batches (status);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signal_batches_review_status
  ON public.verified_statistical_signal_batches (review_status);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signal_batches_created_at
  ON public.verified_statistical_signal_batches (created_at DESC);

DROP TRIGGER IF EXISTS trg_verified_statistical_signal_batches_updated_at
  ON public.verified_statistical_signal_batches;
CREATE TRIGGER trg_verified_statistical_signal_batches_updated_at
  BEFORE UPDATE ON public.verified_statistical_signal_batches
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- =============================================================================
-- B) verified_statistical_signals
-- =============================================================================
-- One row = one approved persistent statistical interpretation artifact (not a
-- raw observation). signal_deterministic_hash is the replay-safe identity key.
-- UNIQUE(hash): supersession should use a new hash when logic/thresholds change;
-- archived rows still consume the hash — plan supersede flows accordingly.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.verified_statistical_signals (
  id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_id                    uuid NOT NULL REFERENCES public.verified_statistical_signal_batches (id) ON DELETE RESTRICT,
  signal_type                 text NOT NULL,
  signal_label                text,
  signal_deterministic_hash   text NOT NULL,
  signal_logic_version        text NOT NULL,
  source_system               text NOT NULL DEFAULT 'ssb',
  table_id                    text,
  source_table                text,
  periods_compared            text,
  period_start                text,
  period_end                  text,
  period_type                 text,
  period_granularity          text,
  value_start                 numeric,
  value_end                   numeric,
  absolute_change             numeric,
  percent_change              numeric,
  direction_label             text,
  confidence_category         text NOT NULL DEFAULT 'verified_statistical',
  confidence_score            numeric(5, 4),
  signal_quality_score        numeric(5, 4),
  review_status               text NOT NULL DEFAULT 'pending_review',
  lifecycle_status            text NOT NULL DEFAULT 'persisted',
  persistence_eligibility     text NOT NULL DEFAULT 'eligible',
  quality_flags               jsonb NOT NULL DEFAULT '[]'::jsonb,
  quality_reasoning_json      jsonb NOT NULL DEFAULT '{}'::jsonb,
  dimensions_json             jsonb NOT NULL DEFAULT '{}'::jsonb,
  dimension_labels_json       jsonb NOT NULL DEFAULT '{}'::jsonb,
  explainability_note         text,
  explainability_summary_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  lineage_json                jsonb NOT NULL DEFAULT '{}'::jsonb,
  metadata_json               jsonb NOT NULL DEFAULT '{}'::jsonb,
  supersedes_signal_id        uuid REFERENCES public.verified_statistical_signals (id) ON DELETE SET NULL,
  superseded_by_signal_id     uuid REFERENCES public.verified_statistical_signals (id) ON DELETE SET NULL,
  valid_from                  timestamptz,
  valid_to                    timestamptz,
  stale_after                 timestamptz,
  created_at                  timestamptz NOT NULL DEFAULT now(),
  updated_at                  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_verified_statistical_signals_deterministic_hash UNIQUE (signal_deterministic_hash),
  CONSTRAINT verified_statistical_signals_hash_nonempty CHECK (
    length(trim(signal_deterministic_hash)) > 0
  ),
  CONSTRAINT verified_statistical_signals_signal_type_nonempty CHECK (
    length(trim(signal_type)) > 0
  ),
  CONSTRAINT verified_statistical_signals_confidence_score_range CHECK (
    confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)
  ),
  CONSTRAINT verified_statistical_signals_signal_quality_score_range CHECK (
    signal_quality_score IS NULL OR (signal_quality_score >= 0 AND signal_quality_score <= 1)
  ),
  CONSTRAINT verified_statistical_signals_review_status_check CHECK (
    review_status IN (
      'pending_review',
      'approved',
      'conditionally_approved',
      'rejected',
      'quarantined',
      'manually_reviewed',
      'auto_validated'
    )
  ),
  CONSTRAINT verified_statistical_signals_lifecycle_status_check CHECK (
    lifecycle_status IN (
      'persisted',
      'active',
      'superseded',
      'quarantined',
      'deprecated',
      'archived'
    )
  ),
  CONSTRAINT verified_statistical_signals_persistence_eligibility_check CHECK (
    persistence_eligibility IN ('eligible', 'review_only', 'quarantined', 'rejected')
  ),
  CONSTRAINT verified_statistical_signals_no_self_supersede CHECK (
    supersedes_signal_id IS NULL OR supersedes_signal_id <> id
  ),
  CONSTRAINT verified_statistical_signals_no_self_superseded_by CHECK (
    superseded_by_signal_id IS NULL OR superseded_by_signal_id <> id
  )
);

COMMENT ON TABLE public.verified_statistical_signals IS
  'Durable, governance-approved verified statistical signals. Not recommendations, '
  'gaps, overlaps, or RAG primitives — consumption layers read these as evidence.';

COMMENT ON COLUMN public.verified_statistical_signals.signal_deterministic_hash IS
  'Deterministic identity (e.g. SHA-256 over slice, periods, observation ids, logic version). '
  'Globally UNIQUE in this table: superseding flows should issue a new hash when semantics change.';

COMMENT ON COLUMN public.verified_statistical_signals.lineage_json IS
  'Non-lossy lineage payload (dataset ids, versions, ingestion batch, observation signatures, emitter versions).';

COMMENT ON COLUMN public.verified_statistical_signals.explainability_note IS
  'Human-readable audit narrative; mandatory before promotion per governance model.';

COMMENT ON COLUMN public.verified_statistical_signals.persistence_eligibility IS
  'Tier gate: eligible vs review_only vs quarantined vs rejected (see persistent signal model).';

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signals_batch_id
  ON public.verified_statistical_signals (batch_id);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signals_signal_type
  ON public.verified_statistical_signals (signal_type);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signals_table_id
  ON public.verified_statistical_signals (table_id);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signals_direction_label
  ON public.verified_statistical_signals (direction_label);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signals_review_status
  ON public.verified_statistical_signals (review_status);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signals_lifecycle_status
  ON public.verified_statistical_signals (lifecycle_status);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signals_persistence_eligibility
  ON public.verified_statistical_signals (persistence_eligibility);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signals_confidence_category
  ON public.verified_statistical_signals (confidence_category);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signals_period_end
  ON public.verified_statistical_signals (period_end);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signals_supersedes_signal_id
  ON public.verified_statistical_signals (supersedes_signal_id)
  WHERE supersedes_signal_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signals_superseded_by_signal_id
  ON public.verified_statistical_signals (superseded_by_signal_id)
  WHERE superseded_by_signal_id IS NOT NULL;

-- Deterministic hash already indexed via UNIQUE constraint name uq_..._deterministic_hash

DROP TRIGGER IF EXISTS trg_verified_statistical_signals_updated_at
  ON public.verified_statistical_signals;
CREATE TRIGGER trg_verified_statistical_signals_updated_at
  BEFORE UPDATE ON public.verified_statistical_signals
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- =============================================================================
-- C) verified_statistical_signal_sources
-- =============================================================================
-- Links each persistent signal to contributing statistical_observations rows
-- for non-lossy lineage (does not modify observations).
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.verified_statistical_signal_sources (
  id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  signal_id                  uuid NOT NULL REFERENCES public.verified_statistical_signals (id) ON DELETE CASCADE,
  statistical_observation_id uuid NOT NULL REFERENCES public.statistical_observations (id) ON DELETE RESTRICT,
  observation_signature      text,
  table_id                   text,
  source_file                text,
  period                     text,
  value                      numeric,
  unit                       text,
  dimensions_json            jsonb NOT NULL DEFAULT '{}'::jsonb,
  dimension_labels_json      jsonb NOT NULL DEFAULT '{}'::jsonb,
  role                       text,
  metadata_json              jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at                 timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_verified_statistical_signal_sources_signal_observation UNIQUE (
    signal_id,
    statistical_observation_id
  )
);

COMMENT ON TABLE public.verified_statistical_signal_sources IS
  'Join table from persistent signals to statistical_observations; preserves replay '
  'and audit without denormalizing away observation identity.';

COMMENT ON COLUMN public.verified_statistical_signal_sources.role IS
  'Optional contributor role, e.g. period_start, period_end, snapshot, contributing.';

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signal_sources_signal_id
  ON public.verified_statistical_signal_sources (signal_id);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signal_sources_statistical_observation_id
  ON public.verified_statistical_signal_sources (statistical_observation_id);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signal_sources_observation_signature
  ON public.verified_statistical_signal_sources (observation_signature)
  WHERE observation_signature IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signal_sources_table_id
  ON public.verified_statistical_signal_sources (table_id)
  WHERE table_id IS NOT NULL;

-- =============================================================================
-- D) verified_statistical_signal_reviews
-- =============================================================================
-- Append-style governance events (manual review, approval, quarantine, override).
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.verified_statistical_signal_reviews (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  signal_id          uuid REFERENCES public.verified_statistical_signals (id) ON DELETE CASCADE,
  batch_id           uuid REFERENCES public.verified_statistical_signal_batches (id) ON DELETE CASCADE,
  review_status      text NOT NULL,
  reviewer_id        text NOT NULL,
  review_round       text,
  reviewed_at        timestamptz NOT NULL DEFAULT now(),
  decision           text,
  decision_reason    text,
  quarantine_reason  text,
  override_reason    text,
  metadata_json      jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT verified_statistical_signal_reviews_target_check CHECK (
    signal_id IS NOT NULL OR batch_id IS NOT NULL
  ),
  CONSTRAINT verified_statistical_signal_reviews_reviewer_nonempty CHECK (
    length(trim(reviewer_id)) > 0
  )
);

COMMENT ON TABLE public.verified_statistical_signal_reviews IS
  'Governance audit log for signal- and batch-level decisions; not product scoring or recommendations.';

COMMENT ON COLUMN public.verified_statistical_signal_reviews.decision IS
  'Verb/noun outcome (e.g. approve, reject, quarantine); keep free-form until workflow enums stabilize.';

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signal_reviews_signal_id
  ON public.verified_statistical_signal_reviews (signal_id);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signal_reviews_batch_id
  ON public.verified_statistical_signal_reviews (batch_id);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signal_reviews_review_status
  ON public.verified_statistical_signal_reviews (review_status);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signal_reviews_reviewed_at
  ON public.verified_statistical_signal_reviews (reviewed_at DESC);

CREATE INDEX IF NOT EXISTS idx_verified_statistical_signal_reviews_reviewer_id
  ON public.verified_statistical_signal_reviews (reviewer_id);

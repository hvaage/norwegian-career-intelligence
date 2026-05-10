-- =============================================================================
-- Norwegian Career Intelligence — MVP foundation (001)
-- =============================================================================
-- FIRST MVP FOUNDATION SCHEMA for source, taxonomy, signal, and analysis
-- layers (Norwegian Career Intelligence Dataset + sokr.online).
--
-- Re-runs: This file is designed to be REASONABLY RE-RUNNABLE in the Supabase
-- SQL Editor: CREATE TABLE/INDEX use IF NOT EXISTS; each trigger is dropped
-- before recreate. Re-run still does NOT upgrade an existing table's columns
-- or constraints (PostgreSQL limitation); use explicit ALTER migrations when
-- the schema definition changes after first deploy.
--
-- Out of scope here: RAG / vector tables; candidate_profiles and market import
-- tables; import scripts (see repo docs).
--
-- PostgreSQL / Supabase compatible. Aligns with docs/minimum-viable-
-- intelligence-schema.md and related specs.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Extensions (Supabase: often already enabled)
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- gen_random_uuid() is preferred; pgcrypto ensures compatibility on older PG.

-- =============================================================================
-- updated_at trigger helper
-- =============================================================================
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
-- SOURCE LAYER
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.sources (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug              text NOT NULL UNIQUE,
  name              text NOT NULL,
  kind              text NOT NULL,
  -- e.g. api | file | scrape | survey
  intelligence_layer text,
  -- spor1 (education supply map) | spor2 (employer demand map) | nav (job market)
  base_url          text,
  owner_team        text,
  license_notes     text,
  default_refresh_frequency text,
  reliability_score numeric(4, 3),
  -- optional 0–1 prior for retrieval / scoring weighting
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_active         boolean NOT NULL DEFAULT true,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT sources_kind_nonempty CHECK (length(trim(kind)) > 0),
  CONSTRAINT sources_slug_nonempty CHECK (length(trim(slug)) > 0),
  CONSTRAINT sources_reliability_score_range CHECK (
    reliability_score IS NULL
    OR (reliability_score >= 0 AND reliability_score <= 1)
  )
);

COMMENT ON TABLE public.sources IS
  'Canonical registry of data origins (NIFU, SSB, NAV, trainee pages, reviews, etc.).';

CREATE INDEX IF NOT EXISTS idx_sources_intelligence_layer ON public.sources (intelligence_layer)
  WHERE intelligence_layer IS NOT NULL;

DROP TRIGGER IF EXISTS trg_sources_updated_at ON public.sources;
CREATE TRIGGER trg_sources_updated_at
  BEFORE UPDATE ON public.sources
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();


CREATE TABLE IF NOT EXISTS public.datasets (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id     uuid NOT NULL REFERENCES public.sources (id) ON DELETE CASCADE,
  external_id   text,
  title         text NOT NULL,
  description   text,
  access_method text,
  metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.datasets IS
  'Logical dataset (e.g. one SSB table product, Studiebarometeret wave, NAV feed product).';

CREATE INDEX IF NOT EXISTS idx_datasets_source_id ON public.datasets (source_id);
CREATE INDEX IF NOT EXISTS idx_datasets_external_id ON public.datasets (source_id, external_id);

DROP TRIGGER IF EXISTS trg_datasets_updated_at ON public.datasets;
CREATE TRIGGER trg_datasets_updated_at
  BEFORE UPDATE ON public.datasets
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();


CREATE TABLE IF NOT EXISTS public.dataset_versions (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id           uuid NOT NULL REFERENCES public.datasets (id) ON DELETE CASCADE,
  version_label        text,
  fetched_at           timestamptz NOT NULL DEFAULT now(),
  observed_at          timestamptz,
  -- when the pipeline observed this payload (may differ from fetched_at)
  period_start         date,
  period_end           date,
  storage_uri          text,
  checksum             text,
  row_count_estimate   integer,
  ingestion_status     text NOT NULL DEFAULT 'pending',
  -- pending | complete | failed | partial
  error_log_ref        text,
  schema_snapshot_ref  jsonb,
  metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
  confidence_category  text,
  -- optional quality / verification tier for this extract
  confidence_score     numeric(5, 4),
  valid_from           timestamptz,
  valid_to             timestamptz,
  stale_after          timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.dataset_versions IS
  'Immutable version of a fetch/extract; primary provenance anchor for downstream rows.';

CREATE INDEX IF NOT EXISTS idx_dataset_versions_dataset_id ON public.dataset_versions (dataset_id);
CREATE INDEX IF NOT EXISTS idx_dataset_versions_fetched_at ON public.dataset_versions (fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_dataset_versions_period ON public.dataset_versions (period_start, period_end);

DROP TRIGGER IF EXISTS trg_dataset_versions_updated_at ON public.dataset_versions;
CREATE TRIGGER trg_dataset_versions_updated_at
  BEFORE UPDATE ON public.dataset_versions
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- =============================================================================
-- TAXONOMY LAYER (shared stable IDs)
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.role_families (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  parent_id         uuid REFERENCES public.role_families (id) ON DELETE SET NULL,
  slug              text NOT NULL UNIQUE,
  label_nb          text NOT NULL,
  label_en          text,
  description       text,
  taxonomy_version  text NOT NULL DEFAULT '2026.1',
  status              text NOT NULL DEFAULT 'active',
  -- active | deprecated | provisional
  replaced_by_id    uuid REFERENCES public.role_families (id) ON DELETE SET NULL,
  valid_from        date,
  valid_to          date,
  synonyms_json     jsonb NOT NULL DEFAULT '[]'::jsonb,
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.role_families IS
  'Role family tree (consulting, technology, public_administration, …).';

CREATE INDEX IF NOT EXISTS idx_role_families_parent_id ON public.role_families (parent_id);
CREATE INDEX IF NOT EXISTS idx_role_families_status ON public.role_families (status);

DROP TRIGGER IF EXISTS trg_role_families_updated_at ON public.role_families;
CREATE TRIGGER trg_role_families_updated_at
  BEFORE UPDATE ON public.role_families
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();


CREATE TABLE IF NOT EXISTS public.competencies (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  parent_id         uuid REFERENCES public.competencies (id) ON DELETE SET NULL,
  slug              text NOT NULL UNIQUE,
  category          text,
  -- hard_skills | soft_skills | leadership_skills | … (see career-taxonomy-design.md)
  label_nb          text NOT NULL,
  label_en          text,
  description       text,
  taxonomy_version  text NOT NULL DEFAULT '2026.1',
  status            text NOT NULL DEFAULT 'active',
  replaced_by_id    uuid REFERENCES public.competencies (id) ON DELETE SET NULL,
  valid_from        date,
  valid_to          date,
  synonyms_json     jsonb NOT NULL DEFAULT '[]'::jsonb,
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.competencies IS
  'Competency nodes for cross-domain matching (supply, demand, NAV, candidate).';

CREATE INDEX IF NOT EXISTS idx_competencies_parent_id ON public.competencies (parent_id);
CREATE INDEX IF NOT EXISTS idx_competencies_category ON public.competencies (category);

DROP TRIGGER IF EXISTS trg_competencies_updated_at ON public.competencies;
CREATE TRIGGER trg_competencies_updated_at
  BEFORE UPDATE ON public.competencies
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();


CREATE TABLE IF NOT EXISTS public.industries (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  parent_id         uuid REFERENCES public.industries (id) ON DELETE SET NULL,
  slug              text NOT NULL UNIQUE,
  label_nb          text NOT NULL,
  label_en          text,
  nace_code         text,
  taxonomy_version  text NOT NULL DEFAULT '2026.1',
  status            text NOT NULL DEFAULT 'active',
  replaced_by_id    uuid REFERENCES public.industries (id) ON DELETE SET NULL,
  valid_from        date,
  valid_to          date,
  synonyms_json     jsonb NOT NULL DEFAULT '[]'::jsonb,
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.industries IS
  'Norway-oriented industry tree; optional NACE bridge via nace_code.';

CREATE INDEX IF NOT EXISTS idx_industries_parent_id ON public.industries (parent_id);

DROP TRIGGER IF EXISTS trg_industries_updated_at ON public.industries;
CREATE TRIGGER trg_industries_updated_at
  BEFORE UPDATE ON public.industries
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();


CREATE TABLE IF NOT EXISTS public.employer_types (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug              text NOT NULL UNIQUE,
  label_nb          text NOT NULL,
  label_en          text,
  description       text,
  taxonomy_version  text NOT NULL DEFAULT '2026.1',
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.employer_types IS
  'Employer archetypes (startup, enterprise, public, municipality, …).';

DROP TRIGGER IF EXISTS trg_employer_types_updated_at ON public.employer_types;
CREATE TRIGGER trg_employer_types_updated_at
  BEFORE UPDATE ON public.employer_types
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();


CREATE TABLE IF NOT EXISTS public.evidence_types (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug              text NOT NULL UNIQUE,
  label_nb          text NOT NULL,
  label_en          text,
  description       text,
  default_weight    smallint,
  -- default scoring weight hint (1–5 scale from scoring spec); nullable until calibrated
  taxonomy_version  text NOT NULL DEFAULT '2026.1',
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.evidence_types IS
  'Evidence taxonomy (certification, measurable_result, portfolio, …).';

DROP TRIGGER IF EXISTS trg_evidence_types_updated_at ON public.evidence_types;
CREATE TRIGGER trg_evidence_types_updated_at
  BEFORE UPDATE ON public.evidence_types
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();


CREATE TABLE IF NOT EXISTS public.selection_methods (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug              text NOT NULL UNIQUE,
  label_nb          text NOT NULL,
  label_en          text,
  description       text,
  taxonomy_version  text NOT NULL DEFAULT '2026.1',
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.selection_methods IS
  'Hiring / selection steps (case_interview, technical_assignment, …).';

DROP TRIGGER IF EXISTS trg_selection_methods_updated_at ON public.selection_methods;
CREATE TRIGGER trg_selection_methods_updated_at
  BEFORE UPDATE ON public.selection_methods
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();


CREATE TABLE IF NOT EXISTS public.recommendation_types (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug              text NOT NULL UNIQUE,
  label_nb          text NOT NULL,
  label_en          text,
  description       text,
  taxonomy_version  text NOT NULL DEFAULT '2026.1',
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.recommendation_types IS
  'Product-facing recommendation kinds (improve_evidence, gain_certification, …).';

DROP TRIGGER IF EXISTS trg_recommendation_types_updated_at ON public.recommendation_types;
CREATE TRIGGER trg_recommendation_types_updated_at
  BEFORE UPDATE ON public.recommendation_types
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- =============================================================================
-- SIGNAL LAYER
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.signals (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  signal_type            text NOT NULL,
  -- hard_signal | soft_signal | market_signal | trajectory_signal | selection_signal |
  -- evidence_signal | network_signal | risk_signal
  subject_type           text NOT NULL,
  -- candidate | market | employer | role_profile | dataset | other
  subject_id             uuid NOT NULL,
  payload_json           jsonb NOT NULL DEFAULT '{}'::jsonb,
  strength               smallint,
  -- 1–5 signal strength (scoring-and-signal-model.md)
  evidence_strength      smallint,
  -- 1–5 evidence-backed strength; nullable when unknown
  confidence_score       numeric(5, 4),
  confidence_category    text,
  -- verified_statistical | explicit_requirement | explicit_selection_criterion |
  -- inferred_pattern | llm_extracted | review_based | candidate_claim | weak_signal
  extraction_method      text,
  is_derived             boolean NOT NULL DEFAULT false,
  parent_signal_id       uuid REFERENCES public.signals (id) ON DELETE SET NULL,
  taxonomy_version       text,
  observed_at            timestamptz NOT NULL DEFAULT now(),
  valid_from             timestamptz,
  valid_to               timestamptz,
  stale_after            timestamptz,
  scoring_model_version  text,
  experiment_id          uuid,
  explain_json           jsonb,
  primary_dataset_version_id uuid REFERENCES public.dataset_versions (id) ON DELETE SET NULL,
  -- optional denormalized pointer when a single version dominates provenance
  metadata               jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT signals_strength_range CHECK (strength IS NULL OR (strength >= 1 AND strength <= 5)),
  CONSTRAINT signals_evidence_strength_range CHECK (
    evidence_strength IS NULL OR (evidence_strength >= 1 AND evidence_strength <= 5)
  )
);

COMMENT ON TABLE public.signals IS
  'Interpreted analytical unit; subject_id is polymorphic (no enforced FK until subject tables exist).';
COMMENT ON COLUMN public.signals.primary_dataset_version_id IS
  'Optional fast link to dominant dataset_version; full provenance in signal_sources.';
COMMENT ON COLUMN public.signals.subject_type IS
  'MVP: polymorphic subject discriminator (text); pairs with subject_id — no single FK by design.';
COMMENT ON COLUMN public.signals.subject_id IS
  'MVP: polymorphic subject UUID; meaning depends on subject_type — flexible until subject tables land.';

CREATE INDEX IF NOT EXISTS idx_signals_subject ON public.signals (subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_signals_signal_type ON public.signals (signal_type);
CREATE INDEX IF NOT EXISTS idx_signals_parent ON public.signals (parent_signal_id);
CREATE INDEX IF NOT EXISTS idx_signals_observed_at ON public.signals (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_primary_dataset_version ON public.signals (primary_dataset_version_id);

DROP TRIGGER IF EXISTS trg_signals_updated_at ON public.signals;
CREATE TRIGGER trg_signals_updated_at
  BEFORE UPDATE ON public.signals
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();


CREATE TABLE IF NOT EXISTS public.signal_sources (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  signal_id            uuid NOT NULL REFERENCES public.signals (id) ON DELETE CASCADE,
  dataset_version_id   uuid REFERENCES public.dataset_versions (id) ON DELETE SET NULL,
  source_uri           text,
  row_pointer            jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- e.g. { "row_id": "...", "line_start": 1, "line_end": 40 }
  weight               numeric(5, 4) NOT NULL DEFAULT 1.0,
  observed_at          timestamptz NOT NULL DEFAULT now(),
  confidence_category  text,
  confidence_score     numeric(5, 4),
  evidence_strength    smallint,
  metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at           timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT signal_sources_evidence_strength_range CHECK (
    evidence_strength IS NULL OR (evidence_strength >= 1 AND evidence_strength <= 5)
  )
);

COMMENT ON TABLE public.signal_sources IS
  'Many-to-many provenance: links each signal to dataset versions and/or raw URIs.';

CREATE INDEX IF NOT EXISTS idx_signal_sources_signal_id ON public.signal_sources (signal_id);
CREATE INDEX IF NOT EXISTS idx_signal_sources_dataset_version_id ON public.signal_sources (dataset_version_id);


CREATE TABLE IF NOT EXISTS public.signal_relationships (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  from_signal_id      uuid NOT NULL REFERENCES public.signals (id) ON DELETE CASCADE,
  to_signal_id        uuid NOT NULL REFERENCES public.signals (id) ON DELETE CASCADE,
  relationship_type   text NOT NULL,
  -- derived_from | contradicts | reinforces | supersedes | cascades_from
  note                text,
  metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT signal_relationships_no_self_loop CHECK (from_signal_id <> to_signal_id)
);

COMMENT ON TABLE public.signal_relationships IS
  'Lightweight graph edges between signals (derivation, conflict, reinforcement).';

CREATE INDEX IF NOT EXISTS idx_signal_rel_from ON public.signal_relationships (from_signal_id);
CREATE INDEX IF NOT EXISTS idx_signal_rel_to ON public.signal_relationships (to_signal_id);
CREATE INDEX IF NOT EXISTS idx_signal_rel_type ON public.signal_relationships (relationship_type);

-- =============================================================================
-- ANALYSIS LAYER
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.gaps (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  gap_type               text NOT NULL,
  -- role_gap | evidence_gap | certification_gap | process_gap | network_gap |
  -- industry_gap | trajectory_gap | competency_gap | experience_gap | market_mismatch | other
  subject_type           text NOT NULL,
  subject_id             uuid NOT NULL,
  target_context_json    jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- e.g. { "role_family_id": "...", "industry_id": "...", "employer_id": null }
  severity               numeric(5, 4),
  confidence_score       numeric(5, 4),
  confidence_category    text,
  evidence_strength      smallint,
  contributing_signal_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
  explain_json             jsonb,
  scoring_model_version    text,
  market_period_start      date,
  market_period_end        date,
  source_dataset_version_id uuid REFERENCES public.dataset_versions (id) ON DELETE SET NULL,
  observed_at              timestamptz NOT NULL DEFAULT now(),
  valid_from               timestamptz,
  valid_to                 timestamptz,
  stale_after              timestamptz,
  metadata                 jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT gaps_evidence_strength_range CHECK (
    evidence_strength IS NULL OR (evidence_strength >= 1 AND evidence_strength <= 5)
  )
);

COMMENT ON TABLE public.gaps IS
  'Detected mismatch vs target context; trace via contributing_signal_ids and explain_json.';
COMMENT ON COLUMN public.gaps.contributing_signal_ids IS
  'MVP: UUID array of signals.id contributors — intentionally NOT FK-enforced (orphans possible; explain_json is source of truth).';
COMMENT ON COLUMN public.gaps.subject_type IS
  'MVP: polymorphic subject discriminator; pairs with subject_id.';
COMMENT ON COLUMN public.gaps.subject_id IS
  'MVP: polymorphic subject UUID; meaning depends on subject_type.';

CREATE INDEX IF NOT EXISTS idx_gaps_subject ON public.gaps (subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_gaps_gap_type ON public.gaps (gap_type);
CREATE INDEX IF NOT EXISTS idx_gaps_market_period ON public.gaps (market_period_start, market_period_end);

DROP TRIGGER IF EXISTS trg_gaps_updated_at ON public.gaps;
CREATE TRIGGER trg_gaps_updated_at
  BEFORE UPDATE ON public.gaps
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();


CREATE TABLE IF NOT EXISTS public.overlaps (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  overlap_type           text NOT NULL,
  -- competency_overlap | role_overlap | industry_overlap | evidence_overlap |
  -- employer_overlap | trajectory_overlap | network_overlap | culture_overlap | other
  subject_type           text NOT NULL,
  subject_id             uuid NOT NULL,
  target_context_json    jsonb NOT NULL DEFAULT '{}'::jsonb,
  score                  numeric(5, 4),
  score_band_low         numeric(5, 4),
  score_band_high        numeric(5, 4),
  confidence_score       numeric(5, 4),
  confidence_category    text,
  evidence_strength        smallint,
  contributing_signal_ids  uuid[] NOT NULL DEFAULT '{}'::uuid[],
  explain_json             jsonb,
  scoring_model_version    text,
  market_period_start      date,
  market_period_end        date,
  source_dataset_version_id uuid REFERENCES public.dataset_versions (id) ON DELETE SET NULL,
  observed_at              timestamptz NOT NULL DEFAULT now(),
  valid_from               timestamptz,
  valid_to                 timestamptz,
  stale_after              timestamptz,
  metadata                 jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT overlaps_evidence_strength_range CHECK (
    evidence_strength IS NULL OR (evidence_strength >= 1 AND evidence_strength <= 5)
  )
);

COMMENT ON TABLE public.overlaps IS
  'Match / similarity artifact for a subject vs target context.';
COMMENT ON COLUMN public.overlaps.contributing_signal_ids IS
  'MVP: UUID array of signals.id contributors — intentionally NOT FK-enforced.';
COMMENT ON COLUMN public.overlaps.subject_type IS
  'MVP: polymorphic subject discriminator; pairs with subject_id.';
COMMENT ON COLUMN public.overlaps.subject_id IS
  'MVP: polymorphic subject UUID; meaning depends on subject_type.';

CREATE INDEX IF NOT EXISTS idx_overlaps_subject ON public.overlaps (subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_overlaps_overlap_type ON public.overlaps (overlap_type);

DROP TRIGGER IF EXISTS trg_overlaps_updated_at ON public.overlaps;
CREATE TRIGGER trg_overlaps_updated_at
  BEFORE UPDATE ON public.overlaps
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();


CREATE TABLE IF NOT EXISTS public.recommendations (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  candidate_profile_id     uuid NOT NULL,
  -- future FK to candidate_profiles; intentionally not referenced in 001
  recommendation_type_id uuid NOT NULL REFERENCES public.recommendation_types (id) ON DELETE RESTRICT,
  priority_class           text,
  -- high_impact_low_effort | critical_missing_evidence | selection_blocker | …
  urgency                  text,
  effort_estimate          text,
  impact_estimate          text,
  title                    text,
  body                     text,
  trigger_gap_ids          uuid[] NOT NULL DEFAULT '{}'::uuid[],
  trigger_overlap_ids      uuid[] NOT NULL DEFAULT '{}'::uuid[],
  status                   text NOT NULL DEFAULT 'active',
  -- active | dismissed | done | snoozed
  confidence_floor         text,
  confidence_score         numeric(5, 4),
  confidence_category      text,
  evidence_strength        smallint,
  scoring_model_version    text,
  valid_until                timestamptz,
  observed_at                timestamptz NOT NULL DEFAULT now(),
  valid_from                 timestamptz,
  valid_to                   timestamptz,
  stale_after                timestamptz,
  source_dataset_version_id  uuid REFERENCES public.dataset_versions (id) ON DELETE SET NULL,
  metadata                   jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at                 timestamptz NOT NULL DEFAULT now(),
  updated_at                 timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT recommendations_evidence_strength_range CHECK (
    evidence_strength IS NULL OR (evidence_strength >= 1 AND evidence_strength <= 5)
  )
);

COMMENT ON TABLE public.recommendations IS
  'Action items for a candidate profile; candidate_profile_id FK deferred until candidate layer migration.';
COMMENT ON COLUMN public.recommendations.trigger_gap_ids IS
  'Array of gaps.id that caused this recommendation (explainability). MVP: intentionally NOT FK-enforced to gaps.id (orphans/deletes possible).';
COMMENT ON COLUMN public.recommendations.trigger_overlap_ids IS
  'MVP: UUID array — intentionally NOT FK-enforced to overlaps.id.';

CREATE INDEX IF NOT EXISTS idx_recommendations_candidate ON public.recommendations (candidate_profile_id);
CREATE INDEX IF NOT EXISTS idx_recommendations_type ON public.recommendations (recommendation_type_id);
CREATE INDEX IF NOT EXISTS idx_recommendations_status ON public.recommendations (status);

DROP TRIGGER IF EXISTS trg_recommendations_updated_at ON public.recommendations;
CREATE TRIGGER trg_recommendations_updated_at
  BEFORE UPDATE ON public.recommendations
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();


CREATE TABLE IF NOT EXISTS public.readiness_scores (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  candidate_profile_id   uuid NOT NULL,
  -- future FK to candidate_profiles
  readiness_stage        text NOT NULL,
  -- exploring | early_ready | application_ready | interview_ready | high_probability | transition_ready
  score_json               jsonb NOT NULL DEFAULT '{}'::jsonb,
  overall_score            numeric(5, 4),
  explain_json             jsonb,
  target_context_json      jsonb NOT NULL DEFAULT '{}'::jsonb,
  confidence_score         numeric(5, 4),
  confidence_category      text,
  evidence_strength        smallint,
  scoring_model_version    text,
  computed_at              timestamptz NOT NULL DEFAULT now(),
  observed_at              timestamptz NOT NULL DEFAULT now(),
  valid_from               timestamptz,
  valid_to                 timestamptz,
  stale_after              timestamptz,
  source_dataset_version_id uuid REFERENCES public.dataset_versions (id) ON DELETE SET NULL,
  metadata                 jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT readiness_scores_evidence_strength_range CHECK (
    evidence_strength IS NULL OR (evidence_strength >= 1 AND evidence_strength <= 5)
  )
);

COMMENT ON TABLE public.readiness_scores IS
  'Snapshot of readiness; candidate_profile_id FK deferred until candidate layer migration.';

CREATE INDEX IF NOT EXISTS idx_readiness_candidate ON public.readiness_scores (candidate_profile_id);
CREATE INDEX IF NOT EXISTS idx_readiness_stage ON public.readiness_scores (readiness_stage);
CREATE INDEX IF NOT EXISTS idx_readiness_computed_at ON public.readiness_scores (computed_at DESC);

DROP TRIGGER IF EXISTS trg_readiness_scores_updated_at ON public.readiness_scores;
CREATE TRIGGER trg_readiness_scores_updated_at
  BEFORE UPDATE ON public.readiness_scores
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- =============================================================================
-- Optional: GIN indexes for JSONB hot paths (comment out if unused early)
-- =============================================================================
-- CREATE INDEX IF NOT EXISTS idx_signals_payload_gin ON public.signals USING gin (payload_json);
-- CREATE INDEX IF NOT EXISTS idx_gaps_target_context_gin ON public.gaps USING gin (target_context_json);

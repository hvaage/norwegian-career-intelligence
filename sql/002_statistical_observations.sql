-- =============================================================================
-- Norwegian Career Intelligence — statistical observation layer (002)
-- =============================================================================
-- This migration creates the normalized statistical foundation layer that sits
-- between raw public statistical data and later interpreted intelligence.
--
-- Included in this file:
--   - statistical_datasets
--   - statistical_dimensions
--   - statistical_dimension_values
--   - statistical_observations
--
-- Explicitly NOT included:
--   - signals
--   - gaps / overlaps / recommendations
--   - RAG / vector tables
--   - data import logic
--
-- MVP orientation:
--   - pragmatic, explainable, append-oriented for observations
--   - partial denormalization allowed for traceability/debugging
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Extension support for gen_random_uuid()
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- -----------------------------------------------------------------------------
-- updated_at trigger support (reused from 001 if present)
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
-- A) statistical_datasets
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.statistical_datasets (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id            uuid REFERENCES public.sources (id) ON DELETE SET NULL,
  dataset_id           uuid REFERENCES public.datasets (id) ON DELETE SET NULL,
  slug                 text NOT NULL UNIQUE,
  external_id          text,
  title                text NOT NULL,
  provider             text,
  dataset_type         text NOT NULL DEFAULT 'statistical_dataset',
  source_system        text,
  table_id             text,
  description          text,
  language             text,
  license_note         text,
  access_url           text,
  metadata_json        jsonb NOT NULL DEFAULT '{}'::jsonb,
  classification_json  jsonb NOT NULL DEFAULT '{}'::jsonb,
  confidence_category  text NOT NULL DEFAULT 'verified_statistical',
  confidence_score     numeric(5, 4),
  status               text NOT NULL DEFAULT 'active',
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT statistical_datasets_slug_nonempty CHECK (length(trim(slug)) > 0),
  CONSTRAINT statistical_datasets_title_nonempty CHECK (length(trim(title)) > 0),
  CONSTRAINT statistical_datasets_confidence_score_range CHECK (
    confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)
  ),
  CONSTRAINT statistical_datasets_status_check CHECK (
    status IN ('active', 'deprecated', 'archived')
  ),
  CONSTRAINT statistical_datasets_dataset_type_check CHECK (
    dataset_type IN (
      'statistical_dataset',
      'survey_dataset',
      'metadata_dataset',
      'codebook',
      'dimensional_dataset',
      'lookup_dataset'
    )
  )
);

COMMENT ON TABLE public.statistical_datasets IS
  'Canonical statistical datasets (e.g. SSB table series, Studiebarometeret yearly files) with lineage and confidence context.';

CREATE INDEX IF NOT EXISTS idx_statistical_datasets_slug
  ON public.statistical_datasets (slug);
CREATE INDEX IF NOT EXISTS idx_statistical_datasets_table_id
  ON public.statistical_datasets (table_id);

DROP TRIGGER IF EXISTS trg_statistical_datasets_updated_at ON public.statistical_datasets;
CREATE TRIGGER trg_statistical_datasets_updated_at
  BEFORE UPDATE ON public.statistical_datasets
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- =============================================================================
-- B) statistical_dimensions
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.statistical_dimensions (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug                  text NOT NULL UNIQUE,
  dimension_code        text,
  canonical_name        text,
  label_no              text,
  label_en              text,
  dimension_type        text,
  source_system         text,
  description           text,
  hierarchy_supported   boolean NOT NULL DEFAULT false,
  metadata_json         jsonb NOT NULL DEFAULT '{}'::jsonb,
  aliases_json          jsonb NOT NULL DEFAULT '[]'::jsonb,
  taxonomy_mapping_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  status                text NOT NULL DEFAULT 'active',
  valid_from            timestamptz,
  valid_to              timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT statistical_dimensions_slug_nonempty CHECK (length(trim(slug)) > 0),
  CONSTRAINT statistical_dimensions_status_check CHECK (
    status IN ('active', 'deprecated', 'archived')
  )
);

COMMENT ON TABLE public.statistical_dimensions IS
  'Reusable statistical dimensions (e.g. Tid, Region, UtdNivaa, Yrke, NACE2007) across datasets.';

CREATE INDEX IF NOT EXISTS idx_statistical_dimensions_slug
  ON public.statistical_dimensions (slug);
CREATE INDEX IF NOT EXISTS idx_statistical_dimensions_dimension_code
  ON public.statistical_dimensions (dimension_code);

DROP TRIGGER IF EXISTS trg_statistical_dimensions_updated_at ON public.statistical_dimensions;
CREATE TRIGGER trg_statistical_dimensions_updated_at
  BEFORE UPDATE ON public.statistical_dimensions
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- =============================================================================
-- C) statistical_dimension_values
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.statistical_dimension_values (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dimension_id          uuid NOT NULL REFERENCES public.statistical_dimensions (id) ON DELETE CASCADE,
  value_code            text NOT NULL,
  label_no              text,
  label_en              text,
  parent_value_id       uuid REFERENCES public.statistical_dimension_values (id) ON DELETE SET NULL,
  sort_order            integer,
  is_total              boolean NOT NULL DEFAULT false,
  is_deprecated         boolean NOT NULL DEFAULT false,
  metadata_json         jsonb NOT NULL DEFAULT '{}'::jsonb,
  aliases_json          jsonb NOT NULL DEFAULT '[]'::jsonb,
  taxonomy_mapping_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  valid_from            timestamptz,
  valid_to              timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT statistical_dimension_values_value_code_nonempty CHECK (length(trim(value_code)) > 0),
  CONSTRAINT uq_statistical_dimension_values_dimension_code UNIQUE (dimension_id, value_code)
);

COMMENT ON TABLE public.statistical_dimension_values IS
  'Dimension values/codes (e.g. 2024, Oslo, 0-9, 3-5) with optional hierarchy and lifecycle metadata.';

CREATE INDEX IF NOT EXISTS idx_statistical_dimension_values_dimension_value_code
  ON public.statistical_dimension_values (dimension_id, value_code);

DROP TRIGGER IF EXISTS trg_statistical_dimension_values_updated_at ON public.statistical_dimension_values;
CREATE TRIGGER trg_statistical_dimension_values_updated_at
  BEFORE UPDATE ON public.statistical_dimension_values
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- =============================================================================
-- D) statistical_observations
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.statistical_observations (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  statistical_dataset_id uuid NOT NULL REFERENCES public.statistical_datasets (id) ON DELETE CASCADE,
  dataset_version_id     uuid REFERENCES public.dataset_versions (id) ON DELETE SET NULL,
  source_id              uuid REFERENCES public.sources (id) ON DELETE SET NULL,
  table_id               text,
  source_file            text,
  period                 text,
  period_start           date,
  period_end             date,
  value                  numeric,
  unit                   text,
  contents_code          text,
  dimensions_json        jsonb NOT NULL DEFAULT '{}'::jsonb,
  dimension_labels_json  jsonb NOT NULL DEFAULT '{}'::jsonb,
  dimension_value_ids    uuid[] NOT NULL DEFAULT '{}'::uuid[],
  metadata_json          jsonb NOT NULL DEFAULT '{}'::jsonb,
  raw_observation_json   jsonb,
  confidence_category    text NOT NULL DEFAULT 'verified_statistical',
  confidence_score       numeric(5, 4),
  observed_at            timestamptz,
  valid_from             timestamptz,
  valid_to               timestamptz,
  stale_after            timestamptz,
  ingestion_batch_id     text,
  transformation_version text,
  normalization_version  text,
  created_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT statistical_observations_confidence_score_range CHECK (
    confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)
  )
);

COMMENT ON TABLE public.statistical_observations IS
  'One row = one measured statistical fact. Append-oriented MVP observation layer with explicit provenance and temporal context.';
COMMENT ON COLUMN public.statistical_observations.dimension_value_ids IS
  'MVP: UUID array intentionally NOT FK-enforced per element; supports flexible, append-oriented mapping while dimension registries stabilize.';
COMMENT ON COLUMN public.statistical_observations.dimensions_json IS
  'Preserves original dimension codes for explainability/debugging; do not overwrite with interpretive mappings.';
COMMENT ON COLUMN public.statistical_observations.dimension_labels_json IS
  'Preserves source labels as observed at ingest time for reproducibility and audit.';
COMMENT ON COLUMN public.statistical_observations.metadata_json IS
  'Holds normalization/debug context; interpretive mappings are separate layers and should not rewrite measured observations.';

CREATE INDEX IF NOT EXISTS idx_statistical_observations_statistical_dataset_id
  ON public.statistical_observations (statistical_dataset_id);
CREATE INDEX IF NOT EXISTS idx_statistical_observations_table_id
  ON public.statistical_observations (table_id);
CREATE INDEX IF NOT EXISTS idx_statistical_observations_period
  ON public.statistical_observations (period);
CREATE INDEX IF NOT EXISTS idx_statistical_observations_source_id
  ON public.statistical_observations (source_id);
CREATE INDEX IF NOT EXISTS idx_statistical_observations_dataset_version_id
  ON public.statistical_observations (dataset_version_id);
CREATE INDEX IF NOT EXISTS idx_statistical_observations_contents_code
  ON public.statistical_observations (contents_code);

-- Optional JSONB indexes for inspection-heavy MVP usage; keep disabled until needed.
-- CREATE INDEX IF NOT EXISTS idx_statistical_observations_dimensions_json_gin
--   ON public.statistical_observations USING gin (dimensions_json);
-- CREATE INDEX IF NOT EXISTS idx_statistical_observations_metadata_json_gin
--   ON public.statistical_observations USING gin (metadata_json);


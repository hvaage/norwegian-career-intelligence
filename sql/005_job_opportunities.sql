-- =============================================================================
-- Norwegian Career Intelligence — NAV job opportunities (005)
-- =============================================================================
-- First persistence table for imported job vacancies (NAV pam-stilling-feed MVP).
-- Populated by supabase/functions/nav-feed Edge Function.
--
-- Re-runs: CREATE TABLE/INDEX IF NOT EXISTS; DROP TRIGGER IF EXISTS before CREATE.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

-- =============================================================================
-- job_opportunities
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.job_opportunities (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source          text NOT NULL,
  external_id     text NOT NULL,
  title           text,
  company_name    text,
  location        text,
  status          text,
  url             text,
  date_modified   timestamptz,
  raw_payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
  imported_at     timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_job_opportunities_source_external_id UNIQUE (source, external_id),
  CONSTRAINT job_opportunities_source_nonempty CHECK (length(trim(source)) > 0),
  CONSTRAINT job_opportunities_external_id_nonempty CHECK (length(trim(external_id)) > 0)
);

COMMENT ON TABLE public.job_opportunities IS
  'Imported job vacancies from external feeds (NAV MVP: source=nav). Upsert on (source, external_id).';

COMMENT ON COLUMN public.job_opportunities.raw_payload IS
  'Full feed item JSON from source API for audit and reprocessing.';

CREATE INDEX IF NOT EXISTS idx_job_opportunities_source
  ON public.job_opportunities (source);

CREATE INDEX IF NOT EXISTS idx_job_opportunities_status
  ON public.job_opportunities (status);

CREATE INDEX IF NOT EXISTS idx_job_opportunities_imported_at
  ON public.job_opportunities (imported_at DESC);

DROP TRIGGER IF EXISTS trg_job_opportunities_updated_at ON public.job_opportunities;
CREATE TRIGGER trg_job_opportunities_updated_at
  BEFORE UPDATE ON public.job_opportunities
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

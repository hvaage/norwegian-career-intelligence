-- =============================================================================
-- Norwegian Career Intelligence — NAV ad date columns (007)
-- =============================================================================
-- Mirror of supabase/migrations/20260518150000_add_nav_ad_dates.sql
-- =============================================================================

ALTER TABLE public.job_opportunities
  ADD COLUMN IF NOT EXISTS published_at timestamptz,
  ADD COLUMN IF NOT EXISTS expires_at timestamptz,
  ADD COLUMN IF NOT EXISTS application_due date,
  ADD COLUMN IF NOT EXISTS nav_event_modified_at timestamptz;

COMMENT ON COLUMN public.job_opportunities.published_at IS
  'NAV ad_content.published (ACTIVE detail).';

COMMENT ON COLUMN public.job_opportunities.expires_at IS
  'NAV ad_content.expires (ACTIVE detail).';

COMMENT ON COLUMN public.job_opportunities.application_due IS
  'NAV ad_content.applicationDue (ACTIVE detail, date only).';

COMMENT ON COLUMN public.job_opportunities.nav_event_modified_at IS
  'NAV event timestamp: ad_content.updated, detail.sistEndret, or feed item.date_modified.';

CREATE INDEX IF NOT EXISTS idx_job_opportunities_source_status
  ON public.job_opportunities (source, status);

CREATE INDEX IF NOT EXISTS idx_job_opportunities_published_at
  ON public.job_opportunities (published_at);

CREATE INDEX IF NOT EXISTS idx_job_opportunities_application_due
  ON public.job_opportunities (application_due);

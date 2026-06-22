-- =============================================================================
-- Norwegian Career Intelligence — NAV job views (008)
-- =============================================================================
-- Presentation/filtering only. Does not change import or job_opportunities data.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- valid_nav_jobs — current, relevant ACTIVE NAV postings
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW public.valid_nav_jobs AS
SELECT
  id,
  external_id,
  title,
  company_name,
  location,
  published_at,
  application_due,
  expires_at,
  url,
  raw_payload
FROM public.job_opportunities
WHERE source = 'nav'
  AND status = 'ACTIVE'
  AND published_at IS NOT NULL
  AND (
    application_due IS NULL
    OR application_due >= CURRENT_DATE
  )
ORDER BY published_at DESC;

COMMENT ON VIEW public.valid_nav_jobs IS
  'ACTIVE NAV jobs with known publish date and open or unknown application deadline.';

-- -----------------------------------------------------------------------------
-- stale_nav_jobs — ACTIVE NAV rows that should not be shown as live postings
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW public.stale_nav_jobs AS
SELECT
  id,
  external_id,
  title,
  company_name,
  location,
  published_at,
  application_due,
  expires_at,
  url,
  nav_event_modified_at,
  raw_payload
FROM public.job_opportunities
WHERE source = 'nav'
  AND status = 'ACTIVE'
  AND (
    published_at IS NULL
    OR application_due < CURRENT_DATE
    OR nav_event_modified_at < NOW() - INTERVAL '90 days'
  );

COMMENT ON VIEW public.stale_nav_jobs IS
  'ACTIVE NAV jobs excluded from valid_nav_jobs: missing publish date, past deadline, or no feed activity in 90 days.';

-- -----------------------------------------------------------------------------
-- Statistics (ad hoc)
-- -----------------------------------------------------------------------------
-- SELECT
--   (SELECT count(*)::bigint FROM public.valid_nav_jobs)   AS valid_active_jobs,
--   (SELECT count(*)::bigint FROM public.stale_nav_jobs)   AS stale_active_jobs,
--   (
--     SELECT count(*)::bigint
--     FROM public.job_opportunities
--     WHERE source = 'nav'
--       AND status = 'INACTIVE'
--   ) AS inactive_jobs;

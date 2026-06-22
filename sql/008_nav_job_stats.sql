-- =============================================================================
-- NAV job statistics — run in SQL editor
-- =============================================================================

SELECT
  (SELECT count(*)::bigint FROM public.valid_nav_jobs) AS valid_active_jobs,
  (SELECT count(*)::bigint FROM public.stale_nav_jobs) AS stale_active_jobs,
  (
    SELECT count(*)::bigint
    FROM public.job_opportunities
    WHERE source = 'nav'
      AND status = 'INACTIVE'
  ) AS inactive_jobs;

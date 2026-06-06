-- =============================================================================
-- NAV sync health checks (Job Buddy)
-- =============================================================================

-- Latest scheduled sync summary
SELECT * FROM public.nav_sync_status;

-- Last 10 sync runs
SELECT
  id,
  started_at,
  finished_at,
  status,
  mode,
  pages_fetched,
  fetched_count,
  active_count,
  inserted_count,
  updated_count,
  error
FROM public.nav_sync_run_log
ORDER BY started_at DESC
LIMIT 10;

-- Valid NAV jobs (current listings)
SELECT count(*)::bigint AS valid_nav_jobs_count
FROM public.valid_nav_jobs;

-- NAV jobs published in the last 24 hours
SELECT count(*)::bigint AS nav_published_last_24h
FROM public.valid_nav_jobs
WHERE published_at >= now() - interval '24 hours';

-- Pause cron (run manually when needed):
-- SELECT cron.unschedule(jobid) FROM cron.job WHERE jobname = 'nav_feed_sync_every_15min';

-- Resume cron (after vault secret is configured):
-- SELECT cron.schedule(
--   'nav_feed_sync_every_15min',
--   '*/15 * * * *',
--   $$SELECT public.invoke_nav_feed_sync();$$
-- );

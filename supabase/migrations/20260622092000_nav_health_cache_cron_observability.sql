-- Include the background health snapshot refresh in the existing read-only
-- cron diagnostics contract.
CREATE OR REPLACE FUNCTION public.get_nav_source_cron_health()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, cron, pg_temp
AS $$
  SELECT COALESCE(
    jsonb_agg(
      jsonb_build_object(
        'jobid', job.jobid,
        'jobname', job.jobname,
        'schedule', job.schedule,
        'active', job.active,
        'latest_run', (
          SELECT jsonb_build_object(
            'runid', run.runid,
            'status', run.status,
            'start_time', run.start_time,
            'end_time', run.end_time,
            'return_message', left(run.return_message, 500)
          )
          FROM cron.job_run_details AS run
          WHERE run.jobid = job.jobid
          ORDER BY run.start_time DESC
          LIMIT 1
        )
      )
      ORDER BY job.jobname
    ),
    '[]'::jsonb
  )
  FROM cron.job AS job
  WHERE job.jobname IN (
    'nav_feed_sync_every_15min',
    'nav_feed_detail_retry_every_30min',
    'nav_feed_reconcile_resume_every_10min',
    'nav_source_health_cache_every_5min'
  );
$$;

ALTER FUNCTION public.get_nav_source_cron_health() OWNER TO postgres;
REVOKE ALL ON FUNCTION public.get_nav_source_cron_health()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_nav_source_cron_health() TO service_role;

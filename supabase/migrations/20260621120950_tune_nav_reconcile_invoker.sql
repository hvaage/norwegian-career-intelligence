-- Start conservatively; increase only after measured production timings.
CREATE OR REPLACE FUNCTION public.invoke_nav_reconcile(
  p_run_id uuid DEFAULT NULL,
  p_start_fresh boolean DEFAULT false
)
RETURNS jsonb
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT public._invoke_nav_feed_body(
    jsonb_strip_nulls(jsonb_build_object(
      'mode', 'reconcile',
      'runId', p_run_id,
      'startFresh', p_start_fresh,
      'maxPages', 2,
      'maxDetails', 20
    ))
  );
$$;

REVOKE ALL ON FUNCTION public.invoke_nav_reconcile(uuid, boolean)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.invoke_nav_reconcile(uuid, boolean)
  TO postgres, service_role;

CREATE OR REPLACE FUNCTION public.get_nav_source_cron_health()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, cron
AS $$
  SELECT COALESCE(
    jsonb_agg(jsonb_build_object(
      'jobid', jobid,
      'jobname', jobname,
      'schedule', schedule,
      'active', active
    ) ORDER BY jobname),
    '[]'::jsonb
  )
  FROM cron.job
  WHERE jobname IN (
    'nav_feed_sync_every_15min',
    'nav_feed_detail_retry_every_30min',
    'nav_feed_reconcile_resume_every_10min'
  );
$$;

ALTER FUNCTION public.get_nav_source_cron_health() OWNER TO postgres;
REVOKE ALL ON FUNCTION public.get_nav_source_cron_health()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_nav_source_cron_health() TO service_role;

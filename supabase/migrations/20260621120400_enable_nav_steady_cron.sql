-- Re-enable steady polling only after the repaired Edge Functions are live.
DO $cron$
DECLARE
  existing_job_id bigint;
BEGIN
  SELECT jobid INTO existing_job_id
  FROM cron.job
  WHERE jobname = 'nav_feed_sync_every_15min'
  LIMIT 1;
  IF existing_job_id IS NOT NULL THEN
    PERFORM cron.unschedule(existing_job_id);
  END IF;

  PERFORM cron.schedule(
    'nav_feed_sync_every_15min',
    '*/15 * * * *',
    $job$SELECT public.invoke_nav_feed_sync();$job$
  );
END;
$cron$;

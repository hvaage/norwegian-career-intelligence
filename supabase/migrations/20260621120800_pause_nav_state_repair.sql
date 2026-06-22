DO $pause$
DECLARE
  job record;
BEGIN
  FOR job IN
    SELECT jobid
    FROM cron.job
    WHERE jobname IN (
      'nav_feed_sync_every_15min',
      'nav_feed_detail_retry_every_30min',
      'nav_feed_reconcile_resume_every_10min'
    )
  LOOP
    PERFORM cron.unschedule(job.jobid);
  END LOOP;
END;
$pause$;

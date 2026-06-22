DO $cron$
BEGIN
  PERFORM cron.schedule(
    'nav_feed_sync_every_15min',
    '*/15 * * * *',
    $job$SELECT public.invoke_nav_feed_sync();$job$
  );
  PERFORM cron.schedule(
    'nav_feed_detail_retry_every_30min',
    '8,38 * * * *',
    $job$SELECT public.invoke_nav_detail_retry();$job$
  );
  PERFORM cron.schedule(
    'nav_feed_reconcile_resume_every_10min',
    '3,13,23,33,43,53 * * * *',
    $job$SELECT public.invoke_nav_reconcile_resume();$job$
  );
END;
$cron$;

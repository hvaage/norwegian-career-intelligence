-- =============================================================================
-- Scheduled NAV feed sync (every 15 minutes) via pg_cron + pg_net
-- =============================================================================
-- Requires Vault secret (run once in SQL editor, not in git):
--
--   SELECT vault.create_secret(
--     '<SUPABASE_SERVICE_ROLE_KEY>',  -- raw key only, no "Bearer " prefix
--     'nav_feed_service_role_key',
--     'nav-feed cron (Job Buddy)'
--   );
--   Read via: SELECT decrypted_secret FROM vault.decrypted_secrets
--             WHERE name = 'nav_feed_service_role_key';
--
-- Pause / disable:
--   SELECT cron.unschedule(jobid) FROM cron.job WHERE jobname = 'nav_feed_sync_every_15min';
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;

CREATE OR REPLACE FUNCTION public.invoke_nav_feed_sync()
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions
AS $$
DECLARE
  service_key text;
  request_id bigint;
  nav_feed_url constant text :=
    'https://rcqnuzplpncnkjmldwqs.supabase.co/functions/v1/nav-feed';
BEGIN
  SELECT decrypted_secret INTO service_key
  FROM vault.decrypted_secrets
  WHERE name = 'nav_feed_service_role_key'
  LIMIT 1;

  IF service_key IS NULL OR btrim(service_key) = '' THEN
    RAISE EXCEPTION
      'Vault secret nav_feed_service_role_key is missing. Run vault.create_secret with service role key.';
  END IF;

  SELECT net.http_post(
    url := nav_feed_url,
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || service_key
    ),
    body := '{"mode":"sync","maxPages":5}'::jsonb
  ) INTO request_id;

  RETURN request_id;
END;
$$;

COMMENT ON FUNCTION public.invoke_nav_feed_sync() IS
  'POST nav-feed sync (mode=sync, maxPages=5). Called by pg_cron every 15 minutes.';

REVOKE ALL ON FUNCTION public.invoke_nav_feed_sync() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.invoke_nav_feed_sync() TO postgres, service_role;

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
    $$SELECT public.invoke_nav_feed_sync();$$
  );
END;
$cron$;

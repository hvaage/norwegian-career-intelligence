-- =============================================================================
-- Test invoke_nav_feed_sync + inspect pg_net response
-- =============================================================================
-- 1. Ensure Vault has the raw key value (not the secret name):
--
--   SELECT vault.create_secret(
--     '<SUPABASE_SERVICE_ROLE_KEY from Dashboard → API keys>',
--     'nav_feed_service_role_key',
--     'nav-feed cron (Job Buddy)'
--   );
--
-- For new projects the key often starts with sb_secret_ (use apikey header, not Bearer).
-- Legacy keys start with eyJ (JWT).
--
-- 2. Invoke sync:
-- =============================================================================

SELECT public.invoke_nav_feed_sync();

-- 3. Wait a few seconds, then check HTTP result (status_code should be 200):
SELECT
  id,
  status_code,
  timed_out,
  error_msg,
  left(content, 500) AS content_preview,
  created
FROM net._http_response
ORDER BY created DESC
LIMIT 3;

-- 4. Verify sync log:
SELECT * FROM public.nav_sync_status;

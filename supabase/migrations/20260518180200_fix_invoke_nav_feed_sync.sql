-- =============================================================================
-- Fix invoke_nav_feed_sync: correct Vault read + apikey for sb_secret_* keys
-- =============================================================================
-- Vault: use vault.decrypted_secrets.decrypted_secret (never vault.secrets.secret).
-- New API keys (sb_secret_*) must use "apikey" header, not Bearer JWT.
-- Legacy JWT (eyJ*) may use both apikey and Authorization: Bearer <JWT>.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.invoke_nav_feed_sync()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions, net, vault
AS $$
DECLARE
  raw_secret text;
  service_key text;
  request_id bigint;
  headers jsonb;
  nav_feed_url constant text :=
    'https://rcqnuzplpncnkjmldwqs.supabase.co/functions/v1/nav-feed';
BEGIN
  SELECT decrypted_secret INTO raw_secret
  FROM vault.decrypted_secrets
  WHERE name = 'nav_feed_service_role_key'
  ORDER BY updated_at DESC NULLS LAST
  LIMIT 1;

  IF raw_secret IS NULL OR btrim(raw_secret) = '' THEN
    RAISE EXCEPTION
      'Vault secret nav_feed_service_role_key missing or empty. '
      'Run: SELECT vault.create_secret(''<KEY>'', ''nav_feed_service_role_key'', ''nav-feed cron'');';
  END IF;

  -- Normalize: decrypted_secret only; strip accidental Bearer prefix / quotes
  service_key := btrim(raw_secret);
  IF service_key LIKE '"%' AND right(service_key, 1) = '"' THEN
    service_key := btrim(service_key, '"');
  END IF;
  IF lower(left(service_key, 7)) = 'bearer ' THEN
    service_key := btrim(substring(service_key from 8));
  END IF;

  IF service_key IS NULL OR service_key = '' THEN
    RAISE EXCEPTION 'nav_feed_service_role_key normalized to empty string';
  END IF;

  headers := jsonb_build_object('Content-Type', 'application/json');

  IF service_key LIKE 'eyJ%' THEN
    headers := headers || jsonb_build_object(
      'Authorization', 'Bearer ' || service_key,
      'apikey', service_key
    );
  ELSIF service_key LIKE 'sb_secret_%' OR service_key LIKE 'sb_publishable_%' THEN
    headers := headers || jsonb_build_object('apikey', service_key);
  ELSE
    headers := headers || jsonb_build_object('apikey', service_key);
  END IF;

  SELECT net.http_post(
    url := nav_feed_url,
    headers := headers,
    body := '{"mode":"sync","maxPages":5}'::jsonb
  ) INTO request_id;

  RETURN jsonb_build_object(
    'request_id', request_id,
    'token_length', length(service_key),
    'token_starts_with_eyJ', service_key LIKE 'eyJ%',
    'token_starts_with_sb_secret', service_key LIKE 'sb_secret_%',
    'auth_header_mode', CASE
      WHEN service_key LIKE 'eyJ%' THEN 'bearer_and_apikey'
      WHEN service_key LIKE 'sb_secret_%' OR service_key LIKE 'sb_publishable_%' THEN 'apikey_only'
      ELSE 'apikey_only_fallback'
    END
  );
END;
$$;

COMMENT ON FUNCTION public.invoke_nav_feed_sync() IS
  'POST nav-feed sync. Reads vault.decrypted_secrets by name. Returns safe debug metadata (never the key).';

REVOKE ALL ON FUNCTION public.invoke_nav_feed_sync() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.invoke_nav_feed_sync() TO postgres, service_role;

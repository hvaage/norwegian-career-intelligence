-- Keep pg_net connected long enough to record the Edge Function response.
CREATE OR REPLACE FUNCTION public._invoke_nav_feed_body(p_body jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions, net, vault, pg_temp
AS $$
DECLARE
  raw_secret text;
  service_key text;
  headers jsonb;
  request_id bigint;
  nav_feed_url constant text :=
    'https://rcqnuzplpncnkjmldwqs.supabase.co/functions/v1/nav-feed';
BEGIN
  SELECT decrypted_secret INTO raw_secret
  FROM vault.decrypted_secrets
  WHERE name = 'nav_feed_service_role_key'
  ORDER BY updated_at DESC NULLS LAST
  LIMIT 1;

  IF raw_secret IS NULL OR btrim(raw_secret) = '' THEN
    RAISE EXCEPTION 'Vault secret nav_feed_service_role_key is missing';
  END IF;

  service_key := btrim(raw_secret);
  IF service_key LIKE '"%' AND right(service_key, 1) = '"' THEN
    service_key := btrim(service_key, '"');
  END IF;
  IF lower(left(service_key, 7)) = 'bearer ' THEN
    service_key := btrim(substring(service_key from 8));
  END IF;

  headers := jsonb_build_object(
    'Content-Type', 'application/json',
    'apikey', service_key
  );
  IF service_key LIKE 'eyJ%' THEN
    headers := headers || jsonb_build_object(
      'Authorization', 'Bearer ' || service_key
    );
  END IF;

  SELECT net.http_post(
    url := nav_feed_url,
    headers := headers,
    body := COALESCE(p_body, '{}'::jsonb),
    timeout_milliseconds := 150000
  ) INTO request_id;

  RETURN jsonb_build_object('request_id', request_id);
END;
$$;

ALTER FUNCTION public._invoke_nav_feed_body(jsonb) OWNER TO postgres;
REVOKE ALL ON FUNCTION public._invoke_nav_feed_body(jsonb)
  FROM PUBLIC, anon, authenticated;

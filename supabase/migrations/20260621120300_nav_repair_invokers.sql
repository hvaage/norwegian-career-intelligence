-- =============================================================================
-- NAV steady/reconcile/detail-retry invokers
-- =============================================================================

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
    body := COALESCE(p_body, '{}'::jsonb)
  ) INTO request_id;

  RETURN jsonb_build_object('request_id', request_id);
END;
$$;

CREATE OR REPLACE FUNCTION public.invoke_nav_feed_sync()
RETURNS jsonb
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT public._invoke_nav_feed_body(
    '{"mode":"sync","maxPages":5,"maxDetails":100}'::jsonb
  );
$$;

CREATE OR REPLACE FUNCTION public.invoke_nav_detail_retry()
RETURNS jsonb
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT public._invoke_nav_feed_body(
    '{"mode":"enrich_active","maxRows":100}'::jsonb
  );
$$;

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
      'maxPages', 10,
      'maxDetails', 100
    ))
  );
$$;

CREATE OR REPLACE FUNCTION public.invoke_nav_reconcile_resume()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_run_id uuid;
BEGIN
  SELECT run_id INTO v_run_id
  FROM public.nav_reconcile_runs
  WHERE status IN ('running', 'snapshot_complete', 'closing')
  ORDER BY started_at DESC
  LIMIT 1;

  IF v_run_id IS NULL THEN
    RETURN jsonb_build_object('skipped', true, 'reason', 'no_active_reconcile');
  END IF;
  RETURN public.invoke_nav_reconcile(v_run_id, false);
END;
$$;

COMMENT ON FUNCTION public.invoke_nav_reconcile(uuid, boolean) IS
  'Starts or resumes the six-month NAV reconciliation. A fresh run must be started explicitly.';
COMMENT ON FUNCTION public.invoke_nav_reconcile_resume() IS
  'Cron-safe reconcile resume. Does nothing when no active reconciliation exists.';

REVOKE ALL ON FUNCTION public._invoke_nav_feed_body(jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.invoke_nav_feed_sync() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.invoke_nav_detail_retry() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.invoke_nav_reconcile(uuid, boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.invoke_nav_reconcile_resume() FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.invoke_nav_feed_sync() TO postgres, service_role;
GRANT EXECUTE ON FUNCTION public.invoke_nav_detail_retry() TO postgres, service_role;
GRANT EXECUTE ON FUNCTION public.invoke_nav_reconcile(uuid, boolean) TO postgres, service_role;
GRANT EXECUTE ON FUNCTION public.invoke_nav_reconcile_resume() TO postgres, service_role;

DO $cron$
DECLARE
  existing_job_id bigint;
BEGIN
  SELECT jobid INTO existing_job_id
  FROM cron.job
  WHERE jobname = 'nav_feed_detail_retry_every_30min'
  LIMIT 1;
  IF existing_job_id IS NOT NULL THEN
    PERFORM cron.unschedule(existing_job_id);
  END IF;
  PERFORM cron.schedule(
    'nav_feed_detail_retry_every_30min',
    '8,38 * * * *',
    $job$SELECT public.invoke_nav_detail_retry();$job$
  );

  SELECT jobid INTO existing_job_id
  FROM cron.job
  WHERE jobname = 'nav_feed_reconcile_resume_every_10min'
  LIMIT 1;
  IF existing_job_id IS NOT NULL THEN
    PERFORM cron.unschedule(existing_job_id);
  END IF;
  PERFORM cron.schedule(
    'nav_feed_reconcile_resume_every_10min',
    '3,13,23,33,43,53 * * * *',
    $job$SELECT public.invoke_nav_reconcile_resume();$job$
  );
END;
$cron$;

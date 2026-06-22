-- Exact inventory counts are useful operationally but should not run inside an
-- interactive health request while reconciliation is writing. Refresh them in
-- the background and serve the latest internally consistent snapshot.
CREATE TABLE IF NOT EXISTS public.nav_source_health_cache (
  source text PRIMARY KEY,
  total bigint NOT NULL,
  active bigint NOT NULL,
  inactive bigint NOT NULL,
  active_with_detail bigint NOT NULL,
  active_missing_detail bigint NOT NULL,
  active_expired bigint NOT NULL,
  active_without_expiry bigint NOT NULL,
  latest_source_event_at timestamptz,
  retry_pending bigint NOT NULL,
  retry_abandoned bigint NOT NULL,
  refreshed_at timestamptz NOT NULL
);

ALTER TABLE public.nav_source_health_cache ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.nav_source_health_cache
  FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.nav_source_health_cache
  TO service_role;

CREATE OR REPLACE FUNCTION public.refresh_nav_source_health_cache()
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
SET statement_timeout = '120s'
AS $$
  INSERT INTO public.nav_source_health_cache (
    source,
    total,
    active,
    inactive,
    active_with_detail,
    active_missing_detail,
    active_expired,
    active_without_expiry,
    latest_source_event_at,
    retry_pending,
    retry_abandoned,
    refreshed_at
  )
  SELECT
    'nav',
    count(*),
    count(*) FILTER (WHERE opportunity.status = 'ACTIVE'),
    count(*) FILTER (WHERE opportunity.status = 'INACTIVE'),
    count(*) FILTER (
      WHERE opportunity.status = 'ACTIVE'
        AND opportunity.raw_payload -> 'nav_detail' IS NOT NULL
    ),
    count(*) FILTER (
      WHERE opportunity.status = 'ACTIVE'
        AND opportunity.raw_payload -> 'nav_detail' IS NULL
    ),
    count(*) FILTER (
      WHERE opportunity.status = 'ACTIVE'
        AND opportunity.expires_at < now()
    ),
    count(*) FILTER (
      WHERE opportunity.status = 'ACTIVE'
        AND opportunity.expires_at IS NULL
    ),
    (
      SELECT latest.source_event_version
      FROM public.job_opportunities AS latest
      WHERE latest.source = 'nav'
        AND latest.source_event_version IS NOT NULL
      ORDER BY latest.source_event_version DESC, latest.external_id DESC
      LIMIT 1
    ),
    (
      SELECT count(*)
      FROM public.nav_detail_retry_queue
      WHERE status = 'pending'
    ),
    (
      SELECT count(*)
      FROM public.nav_detail_retry_queue
      WHERE status = 'abandoned'
    ),
    clock_timestamp()
  FROM public.job_opportunities AS opportunity
  WHERE opportunity.source = 'nav'
  ON CONFLICT (source) DO UPDATE
  SET
    total = EXCLUDED.total,
    active = EXCLUDED.active,
    inactive = EXCLUDED.inactive,
    active_with_detail = EXCLUDED.active_with_detail,
    active_missing_detail = EXCLUDED.active_missing_detail,
    active_expired = EXCLUDED.active_expired,
    active_without_expiry = EXCLUDED.active_without_expiry,
    latest_source_event_at = EXCLUDED.latest_source_event_at,
    retry_pending = EXCLUDED.retry_pending,
    retry_abandoned = EXCLUDED.retry_abandoned,
    refreshed_at = EXCLUDED.refreshed_at;
$$;

ALTER FUNCTION public.refresh_nav_source_health_cache() OWNER TO postgres;
REVOKE ALL ON FUNCTION public.refresh_nav_source_health_cache()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.refresh_nav_source_health_cache()
  TO postgres, service_role;

CREATE OR REPLACE FUNCTION public.get_nav_source_health()
RETURNS jsonb
LANGUAGE sql
STABLE
PARALLEL SAFE
SECURITY DEFINER
SET search_path = public
AS $$
  WITH inventory AS (
    SELECT *
    FROM public.nav_source_health_cache
    WHERE source = 'nav'
  ),
  sync_state AS (
    SELECT to_jsonb(s) AS value
    FROM (
      SELECT
        id,
        feed_url,
        feed_etag IS NOT NULL AS has_etag,
        feed_last_modified,
        tail_reached_at,
        last_http_status,
        heartbeat_at,
        pages_fetched,
        total_fetched,
        error
      FROM public.nav_feed_sync_state
      WHERE source = 'nav' AND mode = 'sync' AND archived_at IS NULL
      ORDER BY started_at DESC
      LIMIT 1
    ) s
  ),
  reconcile AS (
    SELECT to_jsonb(r) AS value
    FROM (
      SELECT
        run_id,
        status,
        window_started_at,
        cutoff_event_ts,
        current_feed_url,
        pages_fetched,
        events_seen,
        active_seen,
        inactive_seen,
        detail_success,
        detail_failure,
        feed_tail_reached,
        last_http_status,
        started_at,
        updated_at,
        finished_at,
        error
      FROM public.nav_reconcile_runs
      ORDER BY started_at DESC
      LIMIT 1
    ) r
  )
  SELECT jsonb_build_object(
    'inventory', jsonb_build_object(
      'total', i.total,
      'active', i.active,
      'inactive', i.inactive,
      'active_with_detail', i.active_with_detail,
      'active_missing_detail', i.active_missing_detail,
      'active_expired', i.active_expired,
      'active_without_expiry', i.active_without_expiry,
      'latest_source_event_at', i.latest_source_event_at,
      'source_event_lag_seconds', CASE
        WHEN i.latest_source_event_at IS NULL THEN NULL
        ELSE EXTRACT(epoch FROM now() - i.latest_source_event_at)::bigint
      END,
      'duplicate_external_ids', 0,
      'snapshot_at', i.refreshed_at,
      'snapshot_age_seconds',
        EXTRACT(epoch FROM now() - i.refreshed_at)::bigint,
      'snapshot_stale', i.refreshed_at < now() - interval '15 minutes'
    ),
    'steady_state', COALESCE(s.value, 'null'::jsonb),
    'reconciliation', COALESCE(r.value, 'null'::jsonb),
    'detail_retry', jsonb_build_object(
      'pending', i.retry_pending,
      'abandoned', i.retry_abandoned
    ),
    'leases', COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'lock_name', lock_name,
        'mode', mode,
        'run_id', run_id,
        'heartbeat_at', heartbeat_at,
        'locked_until', locked_until,
        'active', locked_until > now()
      ) ORDER BY lock_name)
      FROM public.nav_feed_leases
    ), '[]'::jsonb)
  )
  FROM inventory i
  LEFT JOIN sync_state s ON true
  LEFT JOIN reconcile r ON true;
$$;

ALTER FUNCTION public.get_nav_source_health() OWNER TO postgres;
REVOKE ALL ON FUNCTION public.get_nav_source_health()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_nav_source_health() TO service_role;

COMMENT ON FUNCTION public.get_nav_source_health() IS
  'Read-only NAV source health backed by an exact background-refreshed inventory snapshot.';

-- Seed the cache before switching interactive reads to it.
SELECT public.refresh_nav_source_health_cache();

DO $cron$
DECLARE
  existing_job_id bigint;
BEGIN
  SELECT jobid INTO existing_job_id
  FROM cron.job
  WHERE jobname = 'nav_source_health_cache_every_5min'
  LIMIT 1;

  IF existing_job_id IS NOT NULL THEN
    PERFORM cron.unschedule(existing_job_id);
  END IF;

  PERFORM cron.schedule(
    'nav_source_health_cache_every_5min',
    '6,16,26,36,46,56 * * * *',
    $job$SELECT public.refresh_nav_source_health_cache();$job$
  );
END;
$cron$;

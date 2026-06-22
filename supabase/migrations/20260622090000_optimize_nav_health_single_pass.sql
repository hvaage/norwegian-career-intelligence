-- Keep the service-role health contract responsive while reconciliation writes.
-- Inventory and ACTIVE-quality metrics are computed in one pass instead of
-- issuing several exact counts over the same NAV population.
CREATE OR REPLACE FUNCTION public.get_nav_source_health()
RETURNS jsonb
LANGUAGE sql
STABLE
PARALLEL SAFE
SECURITY DEFINER
SET search_path = public
AS $$
  WITH inventory AS (
    SELECT
      count(*) AS total,
      count(*) FILTER (WHERE status = 'ACTIVE') AS active,
      count(*) FILTER (WHERE status = 'INACTIVE') AS inactive,
      count(*) FILTER (
        WHERE status = 'ACTIVE' AND raw_payload -> 'nav_detail' IS NOT NULL
      ) AS active_with_detail,
      count(*) FILTER (
        WHERE status = 'ACTIVE' AND raw_payload -> 'nav_detail' IS NULL
      ) AS active_missing_detail,
      count(*) FILTER (
        WHERE status = 'ACTIVE' AND expires_at < now()
      ) AS active_expired,
      count(*) FILTER (
        WHERE status = 'ACTIVE' AND expires_at IS NULL
      ) AS active_without_expiry
    FROM public.job_opportunities
    WHERE source = 'nav'
  ),
  latest_event AS (
    SELECT source_event_version AS value
    FROM public.job_opportunities
    WHERE source = 'nav' AND source_event_version IS NOT NULL
    ORDER BY source_event_version DESC, external_id DESC
    LIMIT 1
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
  ),
  retries AS (
    SELECT
      count(*) FILTER (WHERE status = 'pending') AS pending,
      count(*) FILTER (WHERE status = 'abandoned') AS abandoned
    FROM public.nav_detail_retry_queue
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
      'latest_source_event_at', e.value,
      'source_event_lag_seconds', CASE
        WHEN e.value IS NULL THEN NULL
        ELSE EXTRACT(epoch FROM now() - e.value)::bigint
      END,
      'duplicate_external_ids', 0
    ),
    'steady_state', COALESCE(s.value, 'null'::jsonb),
    'reconciliation', COALESCE(r.value, 'null'::jsonb),
    'detail_retry', jsonb_build_object(
      'pending', d.pending,
      'abandoned', d.abandoned
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
  LEFT JOIN latest_event e ON true
  LEFT JOIN sync_state s ON true
  LEFT JOIN reconcile r ON true
  CROSS JOIN retries d;
$$;

ALTER FUNCTION public.get_nav_source_health() OWNER TO postgres;
REVOKE ALL ON FUNCTION public.get_nav_source_health()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_nav_source_health() TO service_role;

COMMENT ON FUNCTION public.get_nav_source_health() IS
  'Read-only NAV source health. Exact inventory metrics are aggregated in one pass.';

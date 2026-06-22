-- =============================================================================
-- Frozen downstream NAV source contracts
-- =============================================================================

DROP FUNCTION IF EXISTS public.list_nav_opportunities_since(timestamptz, text, int);

CREATE FUNCTION public.list_nav_opportunities_since(
  p_since timestamptz,
  p_after_external_id text DEFAULT '',
  p_limit int DEFAULT 500
)
RETURNS TABLE (
  external_id text,
  title text,
  company_name text,
  location text,
  url text,
  published_at timestamptz,
  expires_at timestamptz,
  application_due date,
  status text,
  date_modified timestamptz,
  nav_event_modified_at timestamptz,
  updated_at timestamptz,
  raw_payload jsonb,
  source_event_version timestamptz,
  source_payload_hash text,
  source_event_id text,
  changed_at timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  WITH base AS (
    SELECT
      jo.external_id,
      jo.title,
      jo.company_name,
      jo.location,
      jo.url,
      jo.published_at,
      jo.expires_at,
      jo.application_due,
      jo.status,
      jo.date_modified,
      jo.nav_event_modified_at,
      jo.updated_at,
      jo.raw_payload,
      COALESCE(jo.source_event_version, public._nav_compute_event_version_row(jo)) AS source_event_version,
      COALESCE(jo.source_payload_hash, public._nav_compute_payload_hash_row(jo)) AS source_payload_hash,
      jo.source_event_id,
      GREATEST(
        COALESCE(jo.updated_at, '-infinity'::timestamptz),
        COALESCE(jo.date_modified, '-infinity'::timestamptz),
        COALESCE(jo.nav_event_modified_at, '-infinity'::timestamptz),
        COALESCE(jo.imported_at, '-infinity'::timestamptz)
      ) AS changed_at
    FROM public.job_opportunities jo
    WHERE jo.source = 'nav'
  )
  SELECT
    b.external_id,
    b.title,
    b.company_name,
    b.location,
    b.url,
    b.published_at,
    b.expires_at,
    b.application_due,
    b.status,
    b.date_modified,
    b.nav_event_modified_at,
    b.updated_at,
    b.raw_payload,
    b.source_event_version,
    b.source_payload_hash,
    b.source_event_id,
    b.changed_at
  FROM base b
  WHERE ROW(b.changed_at, b.external_id) > ROW(
    COALESCE(p_since, '-infinity'::timestamptz),
    COALESCE(p_after_external_id, '')
  )
  ORDER BY b.changed_at, b.external_id
  LIMIT LEAST(GREATEST(COALESCE(p_limit, 500), 1), 1000);
$$;

ALTER FUNCTION public.list_nav_opportunities_since(timestamptz, text, int)
  OWNER TO postgres;
COMMENT ON FUNCTION public.list_nav_opportunities_since(timestamptz, text, int) IS
  'Read-only NAV opportunity cursor API for downstream sync. Returns ACTIVE and INACTIVE rows sorted by (changed_at, external_id).';
REVOKE ALL ON FUNCTION public.list_nav_opportunities_since(timestamptz, text, int)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.list_nav_opportunities_since(timestamptz, text, int)
  TO service_role;

CREATE OR REPLACE FUNCTION public.list_nav_opportunities_by_external_ids(
  p_ids text[]
)
RETURNS TABLE (
  external_id text,
  title text,
  company_name text,
  location text,
  url text,
  published_at timestamptz,
  expires_at timestamptz,
  application_due date,
  status text,
  date_modified timestamptz,
  nav_event_modified_at timestamptz,
  raw_payload jsonb,
  source_event_version timestamptz,
  source_payload_hash text,
  source_event_id text
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_ids text[];
BEGIN
  SELECT COALESCE(array_agg(id ORDER BY id), ARRAY[]::text[])
  INTO v_ids
  FROM (
    SELECT DISTINCT btrim(value) AS id
    FROM unnest(COALESCE(p_ids, ARRAY[]::text[])) AS input(value)
    WHERE value IS NOT NULL AND btrim(value) <> ''
  ) AS normalized;

  IF cardinality(v_ids) > 500 THEN
    RAISE EXCEPTION 'maximum 500 unique external IDs per call' USING ERRCODE = '22023';
  END IF;

  RETURN QUERY
  SELECT
    jo.external_id,
    jo.title,
    jo.company_name,
    jo.location,
    jo.url,
    jo.published_at,
    jo.expires_at,
    jo.application_due,
    jo.status,
    jo.date_modified,
    jo.nav_event_modified_at,
    jo.raw_payload,
    COALESCE(jo.source_event_version, public._nav_compute_event_version_row(jo)),
    COALESCE(jo.source_payload_hash, public._nav_compute_payload_hash_row(jo)),
    jo.source_event_id
  FROM public.job_opportunities jo
  WHERE jo.source = 'nav'
    AND jo.external_id = ANY(v_ids)
  ORDER BY jo.external_id;
END;
$$;

ALTER FUNCTION public.list_nav_opportunities_by_external_ids(text[])
  OWNER TO postgres;
COMMENT ON FUNCTION public.list_nav_opportunities_by_external_ids(text[]) IS
  'Read-only service-role lookup for at most 500 unique NAV external IDs. Used by controlled downstream reconciliation.';
REVOKE ALL ON FUNCTION public.list_nav_opportunities_by_external_ids(text[])
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.list_nav_opportunities_by_external_ids(text[])
  TO service_role;

CREATE OR REPLACE FUNCTION public.get_nav_source_health()
RETURNS jsonb
LANGUAGE sql
STABLE
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
      ) AS active_without_expiry,
      max(COALESCE(source_event_version, public._nav_compute_event_version_row(jo))) AS latest_source_event_at
    FROM public.job_opportunities jo
    WHERE source = 'nav'
  ),
  duplicate_count AS (
    SELECT count(*) AS count
    FROM (
      SELECT external_id
      FROM public.job_opportunities
      WHERE source = 'nav'
      GROUP BY external_id
      HAVING count(*) > 1
    ) duplicates
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
      'latest_source_event_at', i.latest_source_event_at,
      'source_event_lag_seconds', CASE
        WHEN i.latest_source_event_at IS NULL THEN NULL
        ELSE EXTRACT(epoch FROM now() - i.latest_source_event_at)::bigint
      END,
      'duplicate_external_ids', d.count
    ),
    'steady_state', COALESCE(s.value, 'null'::jsonb),
    'reconciliation', COALESCE(r.value, 'null'::jsonb),
    'detail_retry', jsonb_build_object('pending', q.pending, 'abandoned', q.abandoned),
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
  CROSS JOIN duplicate_count d
  CROSS JOIN retries q
  LEFT JOIN sync_state s ON true
  LEFT JOIN reconcile r ON true;
$$;

ALTER FUNCTION public.get_nav_source_health() OWNER TO postgres;
COMMENT ON FUNCTION public.get_nav_source_health() IS
  'Read-only service-role health report for the NAV source mirror, steady cursor, reconciliation and detail retry queue.';
REVOKE ALL ON FUNCTION public.get_nav_source_health() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_nav_source_health() TO service_role;

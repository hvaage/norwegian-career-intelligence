-- Older NAV rows predate source_event_version. Materialize their existing
-- deterministic source timestamp in small batches so closeout never performs
-- a raw-payload timestamp calculation across the whole active mirror.

CREATE OR REPLACE FUNCTION public.closeout_nav_reconciliation(
  p_run_id uuid,
  p_limit integer DEFAULT 100
)
RETURNS TABLE (closed_count integer, remaining_count bigint, completed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_cutoff timestamptz;
  v_status text;
  v_tail boolean;
  v_limit integer := LEAST(GREATEST(COALESCE(p_limit, 100), 1), 250);
BEGIN
  SELECT status, cutoff_event_ts, feed_tail_reached
  INTO v_status, v_cutoff, v_tail
  FROM public.nav_reconcile_runs
  WHERE run_id = p_run_id
  FOR UPDATE;

  IF NOT FOUND OR v_status NOT IN ('snapshot_complete', 'closing') OR NOT v_tail THEN
    RAISE EXCEPTION 'reconciliation snapshot is not ready for closeout' USING ERRCODE = '55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.nav_feed_leases
    WHERE lock_name = 'nav_writer' AND run_id = p_run_id AND locked_until > now()
  ) OR NOT EXISTS (
    SELECT 1 FROM public.nav_feed_leases
    WHERE lock_name = 'nav_reconcile' AND run_id = p_run_id AND locked_until > now()
  ) THEN
    RAISE EXCEPTION 'active reconciliation leases are required' USING ERRCODE = '55000';
  END IF;

  UPDATE public.nav_reconcile_runs
  SET status = 'closing', updated_at = now()
  WHERE run_id = p_run_id;

  WITH modern AS MATERIALIZED (
    SELECT
      j.id, j.external_id, j.title, j.company_name, j.location, j.url,
      j.published_at, j.expires_at, j.application_due, j.date_modified,
      j.nav_event_modified_at, j.raw_payload, j.source_event_version,
      COALESCE(j.source_payload_hash, public._nav_compute_payload_hash_row(j)) AS source_payload_hash,
      public._nav_compute_payload_hash_values(
        j.title, j.company_name, j.location, j.url, 'INACTIVE',
        j.published_at, j.expires_at, j.application_due,
        j.date_modified, j.nav_event_modified_at, j.raw_payload
      ) AS inactive_payload_hash
    FROM public.job_opportunities j
    WHERE j.source = 'nav'
      AND j.status = 'ACTIVE'
      AND j.source_event_version <= v_cutoff
      AND NOT EXISTS (
        SELECT 1 FROM public.nav_reconcile_snapshot s
        WHERE s.run_id = p_run_id AND s.external_id = j.external_id
          AND s.final_status = 'ACTIVE'
      )
    ORDER BY j.external_id
    LIMIT v_limit
    FOR UPDATE SKIP LOCKED
  ), legacy_source AS MATERIALIZED (
    SELECT
      j.id, j.external_id, j.title, j.company_name, j.location, j.url,
      j.published_at, j.expires_at, j.application_due, j.date_modified,
      j.nav_event_modified_at, j.raw_payload,
      public._nav_compute_event_version_row(j) AS source_event_version,
      COALESCE(j.source_payload_hash, public._nav_compute_payload_hash_row(j)) AS source_payload_hash,
      public._nav_compute_payload_hash_values(
        j.title, j.company_name, j.location, j.url, 'INACTIVE',
        j.published_at, j.expires_at, j.application_due,
        j.date_modified, j.nav_event_modified_at, j.raw_payload
      ) AS inactive_payload_hash
    FROM public.job_opportunities j
    WHERE j.source = 'nav'
      AND j.status = 'ACTIVE'
      AND j.source_event_version IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM public.nav_reconcile_snapshot s
        WHERE s.run_id = p_run_id AND s.external_id = j.external_id
          AND s.final_status = 'ACTIVE'
      )
    ORDER BY j.external_id
    LIMIT GREATEST(v_limit - (SELECT count(*) FROM modern), 0)
    FOR UPDATE SKIP LOCKED
  ), legacy_backfilled AS (
    UPDATE public.job_opportunities j
    SET source_event_version = l.source_event_version
    FROM legacy_source l
    WHERE j.id = l.id AND l.source_event_version IS NOT NULL
    RETURNING j.id
  ), candidates AS MATERIALIZED (
    SELECT * FROM modern
    UNION ALL
    SELECT l.*
    FROM legacy_source l
    JOIN legacy_backfilled b ON b.id = l.id
    WHERE l.source_event_version <= v_cutoff
  ), updated AS (
    UPDATE public.job_opportunities j
    SET
      status = 'INACTIVE',
      source_payload_hash = c.inactive_payload_hash,
      last_reconciled_run_id = p_run_id,
      last_reconciled_at = now(),
      reconciliation_status = 'absent'
    FROM candidates c
    WHERE j.id = c.id
    RETURNING j.id
  ), audited AS (
    INSERT INTO public.nav_event_audit (
      run_id, run_mode, external_id, change_kind, previous_values, new_values
    )
    SELECT
      p_run_id, 'reconcile', c.external_id, 'closeout',
      jsonb_build_object(
        'title', c.title, 'company_name', c.company_name,
        'location', c.location, 'url', c.url, 'status', 'ACTIVE',
        'published_at', c.published_at, 'expires_at', c.expires_at,
        'application_due', c.application_due,
        'source_event_version', c.source_event_version,
        'source_payload_hash', c.source_payload_hash
      ),
      jsonb_build_object(
        'title', c.title, 'company_name', c.company_name,
        'location', c.location, 'url', c.url, 'status', 'INACTIVE',
        'published_at', c.published_at, 'expires_at', c.expires_at,
        'application_due', c.application_due,
        'source_event_version', c.source_event_version,
        'source_payload_hash', c.inactive_payload_hash,
        'reconciliation_status', 'absent'
      )
    FROM candidates c
    JOIN updated u ON u.id = c.id
  )
  SELECT count(*)::integer INTO closed_count FROM updated;

  PERFORM public._nav_increment_counter(p_run_id, 'reconcile', 'closeout', closed_count);

  SELECT count(*) INTO remaining_count
  FROM public.job_opportunities j
  WHERE j.source = 'nav'
    AND j.status = 'ACTIVE'
    AND (j.source_event_version IS NULL OR j.source_event_version <= v_cutoff)
    AND NOT EXISTS (
      SELECT 1 FROM public.nav_reconcile_snapshot s
      WHERE s.run_id = p_run_id AND s.external_id = j.external_id
        AND s.final_status = 'ACTIVE'
    );

  completed := remaining_count = 0;
  IF completed THEN
    UPDATE public.nav_reconcile_runs
    SET status = 'completed', finished_at = now(), updated_at = now()
    WHERE run_id = p_run_id;
  END IF;
  RETURN NEXT;
END;
$$;

REVOKE ALL ON FUNCTION public.closeout_nav_reconciliation(uuid, integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.closeout_nav_reconciliation(uuid, integer) TO service_role;

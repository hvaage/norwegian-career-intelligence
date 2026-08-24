-- Split legacy source-version materialisation from closeout. This keeps both
-- RPCs bounded and lets the closeout candidate index take effect immediately.

CREATE OR REPLACE FUNCTION public.nav_backfill_reconcile_source_versions(
  p_run_id uuid,
  p_limit integer DEFAULT 100
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_status text;
  v_tail boolean;
  v_updated integer;
BEGIN
  SELECT status, feed_tail_reached
  INTO v_status, v_tail
  FROM public.nav_reconcile_runs
  WHERE run_id = p_run_id
  FOR UPDATE;

  IF NOT FOUND OR v_status NOT IN ('snapshot_complete', 'closing') OR NOT v_tail THEN
    RAISE EXCEPTION 'reconciliation snapshot is not ready for source-version backfill' USING ERRCODE = '55000';
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

  WITH candidates AS MATERIALIZED (
    SELECT j.id, public._nav_compute_event_version_row(j) AS source_event_version
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
    LIMIT LEAST(GREATEST(COALESCE(p_limit, 100), 1), 100)
    FOR UPDATE SKIP LOCKED
  ), updated AS (
    UPDATE public.job_opportunities j
    SET source_event_version = c.source_event_version
    FROM candidates c
    WHERE j.id = c.id AND c.source_event_version IS NOT NULL
    RETURNING j.id
  )
  SELECT count(*)::integer INTO v_updated FROM updated;

  RETURN v_updated;
END;
$$;

REVOKE ALL ON FUNCTION public.nav_backfill_reconcile_source_versions(uuid, integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.nav_backfill_reconcile_source_versions(uuid, integer) TO service_role;

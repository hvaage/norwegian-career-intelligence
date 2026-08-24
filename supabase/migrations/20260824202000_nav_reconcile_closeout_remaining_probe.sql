-- The worker only needs to know whether another batch is needed. Counting the
-- complete anti-join can exceed statement_timeout on a large snapshot, so use
-- an indexed existence probe and retain 0/positive return semantics.

DO $$
DECLARE
  v_definition text;
  v_start integer;
  v_end integer;
  v_replacement text := $replacement$
  remaining_count := CASE WHEN EXISTS (
    SELECT 1
    FROM public.job_opportunities j
    WHERE j.source = 'nav'
      AND j.status = 'ACTIVE'
      AND (j.source_event_version IS NULL OR j.source_event_version <= v_cutoff)
      AND NOT EXISTS (
        SELECT 1 FROM public.nav_reconcile_snapshot s
        WHERE s.run_id = p_run_id AND s.external_id = j.external_id
          AND s.final_status = 'ACTIVE'
      )
    LIMIT 1
  ) THEN 1 ELSE 0 END;

$replacement$;
BEGIN
  SELECT pg_get_functiondef('public.closeout_nav_reconciliation(uuid,integer)'::regprocedure)
  INTO v_definition;
  v_start := position('  SELECT count(*) INTO remaining_count' IN v_definition);
  v_end := position('  completed := remaining_count = 0;' IN v_definition);
  IF v_start = 0 OR v_end = 0 THEN
    RAISE EXCEPTION 'closeout_nav_reconciliation body did not match the expected remaining-count block';
  END IF;
  v_definition := left(v_definition, v_start - 1) || v_replacement || substring(v_definition FROM v_end);
  EXECUTE v_definition;
END;
$$;

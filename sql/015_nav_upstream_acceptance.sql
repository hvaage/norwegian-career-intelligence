-- Transactional acceptance checks for the NAV conditional merge contract.
-- Run after migrations and roll back all synthetic rows.
BEGIN;

DO $$
DECLARE
  v_run_id uuid := gen_random_uuid();
  v_external_id text := 'test-nav-upstream-' || gen_random_uuid()::text;
  v_result record;
  v_updated_at timestamptz;
  v_replayed_at timestamptz;
  v_ctid tid;
  v_replayed_ctid tid;
BEGIN
  IF NOT public.claim_nav_feed_lease('nav_writer', 'shared_writer', v_run_id, 300)
     OR NOT public.claim_nav_feed_lease('nav_steady', 'sync', v_run_id, 300) THEN
    RAISE EXCEPTION 'could not claim test leases';
  END IF;

  SELECT * INTO v_result
  FROM public.apply_nav_opportunity_events(
    jsonb_build_array(jsonb_build_object(
      'external_id', v_external_id,
      'title', 'Rich title',
      'company_name', 'Example AS',
      'location', 'Oslo',
      'status', 'ACTIVE',
      'date_modified', '2026-06-21T10:00:00Z',
      'nav_event_modified_at', '2026-06-21T10:00:00Z',
      'raw_payload', jsonb_build_object(
        '_feed_entry', jsonb_build_object('status', 'ACTIVE', 'sistEndret', '2026-06-21T10:00:00Z'),
        'nav_detail', jsonb_build_object('uuid', v_external_id, 'status', 'ACTIVE')
      ),
      'source_event_id', v_external_id || ':ACTIVE:2026-06-21T10:00:00.000Z'
    )),
    v_run_id,
    'sync',
    NULL
  );
  IF v_result.inserted_count <> 1 THEN
    RAISE EXCEPTION 'expected one insert, got %', v_result.inserted_count;
  END IF;

  SELECT updated_at, ctid INTO v_updated_at, v_ctid
  FROM public.job_opportunities
  WHERE source = 'nav' AND external_id = v_external_id;

  SELECT * INTO v_result
  FROM public.apply_nav_opportunity_events(
    jsonb_build_array(jsonb_build_object(
      'external_id', v_external_id,
      'title', 'Rich title',
      'company_name', 'Example AS',
      'location', 'Oslo',
      'status', 'ACTIVE',
      'date_modified', '2026-06-21T10:00:00Z',
      'nav_event_modified_at', '2026-06-21T10:00:00Z',
      'raw_payload', jsonb_build_object(
        '_feed_entry', jsonb_build_object('status', 'ACTIVE', 'sistEndret', '2026-06-21T10:00:00Z'),
        'nav_detail', jsonb_build_object('uuid', v_external_id, 'status', 'ACTIVE')
      ),
      'source_event_id', v_external_id || ':ACTIVE:2026-06-21T10:00:00.000Z'
    )),
    v_run_id,
    'sync',
    NULL
  );
  SELECT updated_at, ctid INTO v_replayed_at, v_replayed_ctid
  FROM public.job_opportunities
  WHERE source = 'nav' AND external_id = v_external_id;
  IF v_result.no_op_count <> 1
     OR v_replayed_at <> v_updated_at
     OR v_replayed_ctid <> v_ctid THEN
    RAISE EXCEPTION 'identical replay was not a physical no-op';
  END IF;

  SELECT * INTO v_result
  FROM public.apply_nav_opportunity_events(
    jsonb_build_array(jsonb_build_object(
      'external_id', v_external_id,
      'status', 'INACTIVE',
      'date_modified', '2026-06-21T09:00:00Z',
      'raw_payload', jsonb_build_object(
        '_feed_entry', jsonb_build_object('status', 'INACTIVE', 'sistEndret', '2026-06-21T09:00:00Z')
      )
    )),
    v_run_id,
    'sync',
    NULL
  );
  IF v_result.stale_ignored_count <> 1 THEN
    RAISE EXCEPTION 'older event was not ignored';
  END IF;

  SELECT * INTO v_result
  FROM public.apply_nav_opportunity_events(
    jsonb_build_array(jsonb_build_object(
      'external_id', v_external_id,
      'status', 'INACTIVE',
      'date_modified', '2026-06-21T11:00:00Z',
      'raw_payload', jsonb_build_object(
        '_feed_entry', jsonb_build_object('status', 'INACTIVE', 'sistEndret', '2026-06-21T11:00:00Z')
      )
    )),
    v_run_id,
    'sync',
    NULL
  );
  IF v_result.merged_count <> 1 OR NOT EXISTS (
    SELECT 1 FROM public.job_opportunities
    WHERE source = 'nav'
      AND external_id = v_external_id
      AND status = 'INACTIVE'
      AND title = 'Rich title'
      AND raw_payload -> 'nav_detail' IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'sparse INACTIVE merge did not preserve rich content';
  END IF;

  PERFORM public.release_nav_feed_lease('nav_steady', v_run_id);
  PERFORM public.release_nav_feed_lease('nav_writer', v_run_id);
END;
$$;

ROLLBACK;

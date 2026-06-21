-- =============================================================================
-- NAV upstream repair: event identity, conditional merge, leases and reconcile
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

ALTER TABLE public.job_opportunities
  ADD COLUMN IF NOT EXISTS source_event_version timestamptz,
  ADD COLUMN IF NOT EXISTS source_payload_hash text,
  ADD COLUMN IF NOT EXISTS source_event_id text,
  ADD COLUMN IF NOT EXISTS last_reconciled_run_id uuid,
  ADD COLUMN IF NOT EXISTS last_reconciled_at timestamptz,
  ADD COLUMN IF NOT EXISTS reconciliation_status text;

ALTER TABLE public.job_opportunities
  DROP CONSTRAINT IF EXISTS job_opportunities_reconciliation_status_check;
ALTER TABLE public.job_opportunities
  ADD CONSTRAINT job_opportunities_reconciliation_status_check
  CHECK (reconciliation_status IS NULL OR reconciliation_status IN ('present', 'absent'));

COMMENT ON COLUMN public.job_opportunities.source_event_version IS
  'Newest trustworthy timestamp supplied by NAV for this source row; never derived from local observation time.';
COMMENT ON COLUMN public.job_opportunities.source_payload_hash IS
  'Deterministic hash of the final persisted NAV scalar fields and canonical raw payload.';
COMMENT ON COLUMN public.job_opportunities.source_event_id IS
  'Deterministic source event identity used for replay diagnostics.';
COMMENT ON COLUMN public.job_opportunities.reconciliation_status IS
  'Result of the latest six-month NAV reconciliation: present or absent.';

CREATE INDEX IF NOT EXISTS idx_job_opportunities_nav_event_version
  ON public.job_opportunities (source_event_version, external_id)
  WHERE source = 'nav';

CREATE OR REPLACE FUNCTION public._nav_safe_to_timestamptz(p_value text)
RETURNS timestamptz
LANGUAGE plpgsql
STABLE
SET search_path = pg_catalog
AS $$
BEGIN
  IF p_value IS NULL OR btrim(p_value) = '' THEN
    RETURN NULL;
  END IF;
  RETURN p_value::timestamptz;
EXCEPTION WHEN others THEN
  RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION public._nav_canonicalize_payload(p_value jsonb)
RETURNS jsonb
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog, public
AS $$
  SELECT CASE jsonb_typeof(p_value)
    WHEN 'object' THEN COALESCE(
      (
        SELECT jsonb_object_agg(e.key, public._nav_canonicalize_payload(e.value) ORDER BY e.key)
        FROM jsonb_each(p_value) AS e
        WHERE e.key <> ALL (ARRAY[
          'imported_at', 'mirror_observed_at', 'last_polled_at',
          'nav_inactive_event', 'nav_lifecycle_events', 'careerjet_lifecycle_events',
          '_run_id', '_etag', '_ingested_at', 'downstream_counters'
        ]::text[])
      ),
      '{}'::jsonb
    )
    WHEN 'array' THEN COALESCE(
      (
        SELECT jsonb_agg(public._nav_canonicalize_payload(a.value) ORDER BY a.ordinality)
        FROM jsonb_array_elements(p_value) WITH ORDINALITY AS a(value, ordinality)
      ),
      '[]'::jsonb
    )
    ELSE p_value
  END;
$$;

CREATE OR REPLACE FUNCTION public._nav_jsonb_rich_merge(
  p_stored jsonb,
  p_incoming jsonb
)
RETURNS jsonb
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog, public
AS $$
  SELECT CASE
    WHEN p_incoming IS NULL OR p_incoming = 'null'::jsonb THEN COALESCE(p_stored, '{}'::jsonb)
    WHEN p_stored IS NULL OR p_stored = 'null'::jsonb THEN p_incoming
    WHEN jsonb_typeof(p_stored) = 'object' AND jsonb_typeof(p_incoming) = 'object' THEN
      COALESCE(
        (
          SELECT jsonb_object_agg(
            keys.key,
            public._nav_jsonb_rich_merge(p_stored -> keys.key, p_incoming -> keys.key)
            ORDER BY keys.key
          )
          FROM (
            SELECT jsonb_object_keys(p_stored) AS key
            UNION
            SELECT jsonb_object_keys(p_incoming) AS key
          ) AS keys
        ),
        '{}'::jsonb
      )
    WHEN jsonb_typeof(p_incoming) = 'string' AND p_incoming = '""'::jsonb THEN p_stored
    WHEN jsonb_typeof(p_incoming) = 'array' AND p_incoming = '[]'::jsonb THEN p_stored
    WHEN jsonb_typeof(p_incoming) = 'object' AND p_incoming = '{}'::jsonb THEN p_stored
    ELSE p_incoming
  END;
$$;

CREATE OR REPLACE FUNCTION public._nav_compute_event_version_values(
  p_date_modified timestamptz,
  p_nav_event_modified_at timestamptz,
  p_raw_payload jsonb
)
RETURNS timestamptz
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $$
  SELECT NULLIF(
    GREATEST(
      COALESCE(p_nav_event_modified_at, '-infinity'::timestamptz),
      COALESCE(p_date_modified, '-infinity'::timestamptz),
      COALESCE(public._nav_safe_to_timestamptz(p_raw_payload -> '_feed_entry' ->> 'sistEndret'), '-infinity'::timestamptz),
      COALESCE(public._nav_safe_to_timestamptz(p_raw_payload -> 'nav_detail' ->> 'sistEndret'), '-infinity'::timestamptz),
      COALESCE(public._nav_safe_to_timestamptz(p_raw_payload -> 'nav_detail' -> 'ad_content' ->> 'updated'), '-infinity'::timestamptz),
      COALESCE(public._nav_safe_to_timestamptz(p_raw_payload -> 'nav_detail' -> 'json' ->> 'updated'), '-infinity'::timestamptz)
    ),
    '-infinity'::timestamptz
  );
$$;

CREATE OR REPLACE FUNCTION public._nav_compute_event_version_row(
  p_row public.job_opportunities
)
RETURNS timestamptz
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $$
  SELECT public._nav_compute_event_version_values(
    p_row.date_modified,
    p_row.nav_event_modified_at,
    p_row.raw_payload
  );
$$;

CREATE OR REPLACE FUNCTION public._nav_utc_text(p_value timestamptz)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog
AS $$
  SELECT CASE
    WHEN p_value IS NULL THEN NULL
    WHEN p_value = 'infinity'::timestamptz THEN 'infinity'
    WHEN p_value = '-infinity'::timestamptz THEN '-infinity'
    ELSE to_char(p_value AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
  END;
$$;

CREATE OR REPLACE FUNCTION public._nav_compute_payload_hash_values(
  p_title text,
  p_company_name text,
  p_location text,
  p_url text,
  p_status text,
  p_published_at timestamptz,
  p_expires_at timestamptz,
  p_application_due date,
  p_date_modified timestamptz,
  p_nav_event_modified_at timestamptz,
  p_raw_payload jsonb
)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog, public
AS $$
  SELECT md5(
    jsonb_build_object(
      'title', p_title,
      'company_name', p_company_name,
      'location', p_location,
      'url', p_url,
      'status', p_status,
      'published_at', public._nav_utc_text(p_published_at),
      'expires_at', public._nav_utc_text(p_expires_at),
      'application_due', p_application_due::text,
      'date_modified', public._nav_utc_text(p_date_modified),
      'nav_event_modified_at', public._nav_utc_text(p_nav_event_modified_at),
      'raw_payload', public._nav_canonicalize_payload(COALESCE(p_raw_payload, '{}'::jsonb))
    )::text
  );
$$;

CREATE OR REPLACE FUNCTION public._nav_compute_payload_hash_row(
  p_row public.job_opportunities
)
RETURNS text
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $$
  SELECT public._nav_compute_payload_hash_values(
    p_row.title,
    p_row.company_name,
    p_row.location,
    p_row.url,
    p_row.status,
    p_row.published_at,
    p_row.expires_at,
    p_row.application_due,
    p_row.date_modified,
    p_row.nav_event_modified_at,
    p_row.raw_payload
  );
$$;

CREATE TABLE IF NOT EXISTS public.nav_feed_leases (
  lock_name text PRIMARY KEY,
  mode text NOT NULL,
  run_id uuid NOT NULL,
  acquired_at timestamptz NOT NULL DEFAULT now(),
  heartbeat_at timestamptz NOT NULL DEFAULT now(),
  locked_until timestamptz NOT NULL,
  CONSTRAINT nav_feed_leases_lock_name_nonempty CHECK (btrim(lock_name) <> '')
);

CREATE TABLE IF NOT EXISTS public.nav_reconcile_runs (
  run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  status text NOT NULL DEFAULT 'running',
  window_started_at timestamptz NOT NULL,
  cutoff_event_ts timestamptz NOT NULL,
  current_feed_url text NOT NULL DEFAULT '/api/v1/feed',
  feed_etag text,
  feed_last_modified text,
  pages_fetched integer NOT NULL DEFAULT 0,
  events_seen bigint NOT NULL DEFAULT 0,
  active_seen bigint NOT NULL DEFAULT 0,
  inactive_seen bigint NOT NULL DEFAULT 0,
  detail_success bigint NOT NULL DEFAULT 0,
  detail_failure bigint NOT NULL DEFAULT 0,
  feed_tail_reached boolean NOT NULL DEFAULT false,
  last_http_status integer,
  started_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  error text,
  CONSTRAINT nav_reconcile_runs_status_check CHECK (
    status IN ('running', 'snapshot_complete', 'closing', 'completed', 'error')
  )
);

CREATE TABLE IF NOT EXISTS public.nav_reconcile_snapshot (
  run_id uuid NOT NULL REFERENCES public.nav_reconcile_runs(run_id) ON DELETE CASCADE,
  external_id text NOT NULL,
  final_status text,
  source_event_version timestamptz,
  source_event_id text,
  seen_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_nav_reconcile_snapshot_active
  ON public.nav_reconcile_snapshot (run_id, external_id)
  WHERE final_status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS public.nav_detail_retry_queue (
  external_id text PRIMARY KEY,
  status text NOT NULL DEFAULT 'pending',
  attempt_count integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz,
  CONSTRAINT nav_detail_retry_queue_status_check CHECK (
    status IN ('pending', 'resolved', 'abandoned')
  )
);

CREATE INDEX IF NOT EXISTS idx_nav_detail_retry_pending
  ON public.nav_detail_retry_queue (next_attempt_at, external_id)
  WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS public.nav_event_audit (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id uuid NOT NULL,
  run_mode text NOT NULL,
  external_id text NOT NULL,
  change_kind text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now(),
  previous_values jsonb,
  new_values jsonb NOT NULL,
  CONSTRAINT nav_event_audit_change_kind_check CHECK (
    change_kind IN ('insert', 'merge', 'closeout')
  )
);

CREATE INDEX IF NOT EXISTS idx_nav_event_audit_run
  ON public.nav_event_audit (run_id, id);

CREATE TABLE IF NOT EXISTS public.nav_run_counters (
  run_id uuid NOT NULL,
  run_mode text NOT NULL,
  metric text NOT NULL,
  count bigint NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, metric),
  CONSTRAINT nav_run_counters_metric_check CHECK (
    metric IN ('no_op', 'stale_ignored', 'insert', 'merge', 'closeout', 'detail_retry')
  )
);

ALTER TABLE public.nav_feed_sync_state
  ADD COLUMN IF NOT EXISTS feed_url text,
  ADD COLUMN IF NOT EXISTS feed_etag text,
  ADD COLUMN IF NOT EXISTS feed_last_modified text,
  ADD COLUMN IF NOT EXISTS tail_reached_at timestamptz,
  ADD COLUMN IF NOT EXISTS last_http_status integer,
  ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz,
  ADD COLUMN IF NOT EXISTS archived_at timestamptz;

UPDATE public.nav_feed_sync_state
SET
  status = 'error',
  error = COALESCE(error, 'Archived by NAV steady-state cursor migration'),
  finished_at = COALESCE(finished_at, now()),
  archived_at = now()
WHERE source = 'nav'
  AND mode = 'sync'
  AND status = 'in_progress'
  AND archived_at IS NULL;

CREATE OR REPLACE FUNCTION public.claim_nav_feed_lease(
  p_lock_name text,
  p_mode text,
  p_run_id uuid,
  p_ttl_seconds integer DEFAULT 300
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_claimed boolean;
BEGIN
  IF p_run_id IS NULL OR btrim(COALESCE(p_lock_name, '')) = '' THEN
    RAISE EXCEPTION 'lock_name and run_id are required' USING ERRCODE = '22023';
  END IF;

  INSERT INTO public.nav_feed_leases (
    lock_name, mode, run_id, acquired_at, heartbeat_at, locked_until
  ) VALUES (
    p_lock_name,
    COALESCE(NULLIF(btrim(p_mode), ''), 'unknown'),
    p_run_id,
    now(),
    now(),
    now() + make_interval(secs => LEAST(GREATEST(COALESCE(p_ttl_seconds, 300), 30), 900))
  )
  ON CONFLICT (lock_name) DO UPDATE
  SET
    mode = EXCLUDED.mode,
    run_id = EXCLUDED.run_id,
    acquired_at = CASE
      WHEN public.nav_feed_leases.run_id = EXCLUDED.run_id THEN public.nav_feed_leases.acquired_at
      ELSE now()
    END,
    heartbeat_at = now(),
    locked_until = EXCLUDED.locked_until
  WHERE public.nav_feed_leases.locked_until <= now()
     OR public.nav_feed_leases.run_id = EXCLUDED.run_id
  RETURNING true INTO v_claimed;

  RETURN COALESCE(v_claimed, false);
END;
$$;

CREATE OR REPLACE FUNCTION public.heartbeat_nav_feed_lease(
  p_lock_name text,
  p_run_id uuid,
  p_ttl_seconds integer DEFAULT 300
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_count integer;
BEGIN
  UPDATE public.nav_feed_leases
  SET
    heartbeat_at = now(),
    locked_until = now() + make_interval(secs => LEAST(GREATEST(COALESCE(p_ttl_seconds, 300), 30), 900))
  WHERE lock_name = p_lock_name
    AND run_id = p_run_id
    AND locked_until > now();
  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count = 1;
END;
$$;

CREATE OR REPLACE FUNCTION public.release_nav_feed_lease(
  p_lock_name text,
  p_run_id uuid
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_count integer;
BEGIN
  DELETE FROM public.nav_feed_leases
  WHERE lock_name = p_lock_name
    AND run_id = p_run_id;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count = 1;
END;
$$;

CREATE OR REPLACE FUNCTION public._nav_increment_counter(
  p_run_id uuid,
  p_run_mode text,
  p_metric text,
  p_count bigint DEFAULT 1
)
RETURNS void
LANGUAGE sql
VOLATILE
SET search_path = public
AS $$
  INSERT INTO public.nav_run_counters (run_id, run_mode, metric, count)
  VALUES (p_run_id, p_run_mode, p_metric, GREATEST(COALESCE(p_count, 0), 0))
  ON CONFLICT (run_id, metric) DO UPDATE
  SET
    count = public.nav_run_counters.count + EXCLUDED.count,
    updated_at = now();
$$;

CREATE OR REPLACE FUNCTION public._nav_apply_opportunity_event(
  p_event jsonb,
  p_run_id uuid,
  p_run_mode text,
  p_reconciliation_status text DEFAULT NULL
)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SET search_path = public, pg_temp
AS $$
DECLARE
  v_stored public.job_opportunities%ROWTYPE;
  v_external_id text := btrim(p_event ->> 'external_id');
  v_raw_payload jsonb := COALESCE(p_event -> 'raw_payload', '{}'::jsonb);
  v_title text := NULLIF(p_event ->> 'title', '');
  v_company_name text := NULLIF(p_event ->> 'company_name', '');
  v_location text := NULLIF(p_event ->> 'location', '');
  v_url text := NULLIF(p_event ->> 'url', '');
  v_status text := NULLIF(p_event ->> 'status', '');
  v_published_at timestamptz := public._nav_safe_to_timestamptz(p_event ->> 'published_at');
  v_expires_at timestamptz := public._nav_safe_to_timestamptz(p_event ->> 'expires_at');
  v_application_due date;
  v_date_modified timestamptz := public._nav_safe_to_timestamptz(p_event ->> 'date_modified');
  v_nav_event_modified_at timestamptz := public._nav_safe_to_timestamptz(p_event ->> 'nav_event_modified_at');
  v_incoming_version timestamptz;
  v_stored_version timestamptz;
  v_source_event_id text := NULLIF(p_event ->> 'source_event_id', '');
  v_final_payload jsonb;
  v_final_hash text;
  v_stored_hash text;
  v_previous jsonb;
  v_new jsonb;
BEGIN
  IF v_external_id IS NULL OR v_external_id = '' THEN
    RAISE EXCEPTION 'external_id is required' USING ERRCODE = '22023';
  END IF;

  BEGIN
    v_application_due := NULLIF(p_event ->> 'application_due', '')::date;
  EXCEPTION WHEN others THEN
    v_application_due := NULL;
  END;

  v_incoming_version := public._nav_compute_event_version_values(
    v_date_modified,
    v_nav_event_modified_at,
    v_raw_payload
  );

  SELECT * INTO v_stored
  FROM public.job_opportunities
  WHERE source = 'nav' AND external_id = v_external_id
  FOR UPDATE;

  IF NOT FOUND THEN
    v_final_hash := public._nav_compute_payload_hash_values(
      v_title, v_company_name, v_location, v_url, v_status,
      v_published_at, v_expires_at, v_application_due,
      v_date_modified, v_nav_event_modified_at, v_raw_payload
    );

    INSERT INTO public.job_opportunities (
      source, external_id, title, company_name, location, status, url,
      date_modified, published_at, expires_at, application_due,
      nav_event_modified_at, raw_payload,
      source_event_version, source_payload_hash, source_event_id,
      last_reconciled_run_id, last_reconciled_at, reconciliation_status
    ) VALUES (
      'nav', v_external_id, v_title, v_company_name, v_location, v_status, v_url,
      v_date_modified, v_published_at, v_expires_at, v_application_due,
      v_nav_event_modified_at, v_raw_payload,
      v_incoming_version, v_final_hash, v_source_event_id,
      CASE WHEN p_reconciliation_status IS NOT NULL THEN p_run_id END,
      CASE WHEN p_reconciliation_status IS NOT NULL THEN now() END,
      p_reconciliation_status
    );

    v_new := jsonb_build_object(
      'title', v_title,
      'company_name', v_company_name,
      'location', v_location,
      'url', v_url,
      'status', v_status,
      'published_at', v_published_at,
      'expires_at', v_expires_at,
      'application_due', v_application_due,
      'date_modified', v_date_modified,
      'nav_event_modified_at', v_nav_event_modified_at,
      'source_event_version', v_incoming_version,
      'source_payload_hash', v_final_hash
    );
    INSERT INTO public.nav_event_audit (
      run_id, run_mode, external_id, change_kind, previous_values, new_values
    ) VALUES (p_run_id, p_run_mode, v_external_id, 'insert', NULL, v_new);
    RETURN 'insert';
  END IF;

  v_stored_version := COALESCE(
    v_stored.source_event_version,
    public._nav_compute_event_version_row(v_stored)
  );

  IF COALESCE(v_incoming_version, '-infinity'::timestamptz)
     < COALESCE(v_stored_version, '-infinity'::timestamptz) THEN
    RETURN 'stale_ignored';
  END IF;

  v_final_payload := public._nav_jsonb_rich_merge(v_stored.raw_payload, v_raw_payload);
  v_title := COALESCE(v_title, v_stored.title);
  v_company_name := COALESCE(v_company_name, v_stored.company_name);
  v_location := COALESCE(v_location, v_stored.location);
  v_url := COALESCE(v_url, v_stored.url);
  v_status := COALESCE(v_status, v_stored.status);
  v_published_at := COALESCE(v_published_at, v_stored.published_at);
  v_expires_at := COALESCE(v_expires_at, v_stored.expires_at);
  v_application_due := COALESCE(v_application_due, v_stored.application_due);
  v_date_modified := COALESCE(v_date_modified, v_stored.date_modified);
  v_nav_event_modified_at := COALESCE(v_nav_event_modified_at, v_stored.nav_event_modified_at);

  v_final_hash := public._nav_compute_payload_hash_values(
    v_title, v_company_name, v_location, v_url, v_status,
    v_published_at, v_expires_at, v_application_due,
    v_date_modified, v_nav_event_modified_at, v_final_payload
  );
  v_stored_hash := COALESCE(
    v_stored.source_payload_hash,
    public._nav_compute_payload_hash_row(v_stored)
  );

  IF v_final_hash = v_stored_hash THEN
    RETURN 'no_op';
  END IF;

  v_previous := jsonb_build_object(
    'title', v_stored.title,
    'company_name', v_stored.company_name,
    'location', v_stored.location,
    'url', v_stored.url,
    'status', v_stored.status,
    'published_at', v_stored.published_at,
    'expires_at', v_stored.expires_at,
    'application_due', v_stored.application_due,
    'date_modified', v_stored.date_modified,
    'nav_event_modified_at', v_stored.nav_event_modified_at,
    'source_event_version', v_stored_version,
    'source_payload_hash', v_stored_hash
  );

  UPDATE public.job_opportunities
  SET
    title = v_title,
    company_name = v_company_name,
    location = v_location,
    status = v_status,
    url = v_url,
    date_modified = v_date_modified,
    published_at = v_published_at,
    expires_at = v_expires_at,
    application_due = v_application_due,
    nav_event_modified_at = v_nav_event_modified_at,
    raw_payload = v_final_payload,
    source_event_version = CASE
      WHEN COALESCE(v_incoming_version, '-infinity'::timestamptz)
           > COALESCE(v_stored_version, '-infinity'::timestamptz)
        THEN v_incoming_version
      ELSE v_stored_version
    END,
    source_payload_hash = v_final_hash,
    source_event_id = CASE
      WHEN COALESCE(v_incoming_version, '-infinity'::timestamptz)
           > COALESCE(v_stored_version, '-infinity'::timestamptz)
        THEN COALESCE(v_source_event_id, v_stored.source_event_id)
      ELSE COALESCE(v_stored.source_event_id, v_source_event_id)
    END,
    last_reconciled_run_id = CASE
      WHEN p_reconciliation_status IS NOT NULL THEN p_run_id
      ELSE v_stored.last_reconciled_run_id
    END,
    last_reconciled_at = CASE
      WHEN p_reconciliation_status IS NOT NULL THEN now()
      ELSE v_stored.last_reconciled_at
    END,
    reconciliation_status = COALESCE(p_reconciliation_status, v_stored.reconciliation_status)
  WHERE id = v_stored.id;

  v_new := jsonb_build_object(
    'title', v_title,
    'company_name', v_company_name,
    'location', v_location,
    'url', v_url,
    'status', v_status,
    'published_at', v_published_at,
    'expires_at', v_expires_at,
    'application_due', v_application_due,
    'date_modified', v_date_modified,
    'nav_event_modified_at', v_nav_event_modified_at,
    'source_event_version', CASE
      WHEN COALESCE(v_incoming_version, '-infinity'::timestamptz)
           > COALESCE(v_stored_version, '-infinity'::timestamptz)
        THEN v_incoming_version
      ELSE v_stored_version
    END,
    'source_payload_hash', v_final_hash
  );
  INSERT INTO public.nav_event_audit (
    run_id, run_mode, external_id, change_kind, previous_values, new_values
  ) VALUES (p_run_id, p_run_mode, v_external_id, 'merge', v_previous, v_new);
  RETURN 'merge';
END;
$$;

CREATE OR REPLACE FUNCTION public.apply_nav_opportunity_events(
  p_events jsonb,
  p_run_id uuid,
  p_run_mode text,
  p_reconcile_run_id uuid DEFAULT NULL
)
RETURNS TABLE (
  inserted_count integer,
  merged_count integer,
  no_op_count integer,
  stale_ignored_count integer
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_event jsonb;
  v_action text;
  v_external_id text;
  v_version timestamptz;
  v_mode_lock text;
BEGIN
  IF p_run_id IS NULL OR p_events IS NULL OR jsonb_typeof(p_events) <> 'array' THEN
    RAISE EXCEPTION 'run_id and a JSON array are required' USING ERRCODE = '22023';
  END IF;
  IF jsonb_array_length(p_events) > 500 THEN
    RAISE EXCEPTION 'maximum 500 NAV events per call' USING ERRCODE = '22023';
  END IF;
  v_mode_lock := CASE p_run_mode
    WHEN 'sync' THEN 'nav_steady'
    WHEN 'reconcile' THEN 'nav_reconcile'
    WHEN 'backfill' THEN 'nav_backfill'
    WHEN 'enrich_active' THEN 'nav_backfill'
    ELSE NULL
  END;
  IF v_mode_lock IS NULL THEN
    RAISE EXCEPTION 'unsupported NAV run mode: %', p_run_mode USING ERRCODE = '22023';
  END IF;
  IF p_reconcile_run_id IS NOT NULL AND (
    p_run_mode <> 'reconcile' OR p_reconcile_run_id <> p_run_id
  ) THEN
    RAISE EXCEPTION 'reconcile run IDs must match' USING ERRCODE = '22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.nav_feed_leases
    WHERE lock_name = 'nav_writer' AND run_id = p_run_id AND locked_until > now()
  ) OR NOT EXISTS (
    SELECT 1 FROM public.nav_feed_leases
    WHERE lock_name = v_mode_lock AND run_id = p_run_id AND locked_until > now()
  ) THEN
    RAISE EXCEPTION 'active NAV writer leases are required' USING ERRCODE = '55000';
  END IF;

  inserted_count := 0;
  merged_count := 0;
  no_op_count := 0;
  stale_ignored_count := 0;

  FOR v_event IN SELECT value FROM jsonb_array_elements(p_events)
  LOOP
    v_action := public._nav_apply_opportunity_event(
      v_event,
      p_run_id,
      p_run_mode,
      CASE WHEN p_reconcile_run_id IS NULL THEN NULL ELSE 'present' END
    );

    CASE v_action
      WHEN 'insert' THEN inserted_count := inserted_count + 1;
      WHEN 'merge' THEN merged_count := merged_count + 1;
      WHEN 'no_op' THEN no_op_count := no_op_count + 1;
      WHEN 'stale_ignored' THEN stale_ignored_count := stale_ignored_count + 1;
    END CASE;

    IF p_reconcile_run_id IS NOT NULL THEN
      v_external_id := btrim(v_event ->> 'external_id');
      v_version := public._nav_compute_event_version_values(
        public._nav_safe_to_timestamptz(v_event ->> 'date_modified'),
        public._nav_safe_to_timestamptz(v_event ->> 'nav_event_modified_at'),
        COALESCE(v_event -> 'raw_payload', '{}'::jsonb)
      );
      INSERT INTO public.nav_reconcile_snapshot (
        run_id, external_id, final_status, source_event_version, source_event_id, seen_at
      ) VALUES (
        p_reconcile_run_id,
        v_external_id,
        NULLIF(v_event ->> 'status', ''),
        v_version,
        NULLIF(v_event ->> 'source_event_id', ''),
        now()
      )
      ON CONFLICT (run_id, external_id) DO UPDATE
      SET
        final_status = EXCLUDED.final_status,
        source_event_version = EXCLUDED.source_event_version,
        source_event_id = EXCLUDED.source_event_id,
        seen_at = EXCLUDED.seen_at
      WHERE COALESCE(EXCLUDED.source_event_version, '-infinity'::timestamptz)
            >= COALESCE(public.nav_reconcile_snapshot.source_event_version, '-infinity'::timestamptz);
    END IF;
  END LOOP;

  PERFORM public._nav_increment_counter(p_run_id, p_run_mode, 'insert', inserted_count);
  PERFORM public._nav_increment_counter(p_run_id, p_run_mode, 'merge', merged_count);
  PERFORM public._nav_increment_counter(p_run_id, p_run_mode, 'no_op', no_op_count);
  PERFORM public._nav_increment_counter(p_run_id, p_run_mode, 'stale_ignored', stale_ignored_count);
  IF p_run_mode = 'enrich_active' THEN
    PERFORM public._nav_increment_counter(
      p_run_id,
      p_run_mode,
      'detail_retry',
      jsonb_array_length(p_events)
    );
  END IF;
  RETURN NEXT;
END;
$$;

CREATE OR REPLACE FUNCTION public.closeout_nav_reconciliation(
  p_run_id uuid,
  p_limit integer DEFAULT 500
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
  v_row public.job_opportunities%ROWTYPE;
  v_hash text;
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

  closed_count := 0;
  FOR v_row IN
    SELECT j.*
    FROM public.job_opportunities j
    WHERE j.source = 'nav'
      AND j.status = 'ACTIVE'
      AND COALESCE(j.source_event_version, public._nav_compute_event_version_row(j), '-infinity'::timestamptz) <= v_cutoff
      AND NOT EXISTS (
        SELECT 1
        FROM public.nav_reconcile_snapshot s
        WHERE s.run_id = p_run_id
          AND s.external_id = j.external_id
          AND s.final_status = 'ACTIVE'
      )
    ORDER BY j.external_id
    LIMIT LEAST(GREATEST(COALESCE(p_limit, 500), 1), 1000)
    FOR UPDATE SKIP LOCKED
  LOOP
    v_hash := public._nav_compute_payload_hash_values(
      v_row.title, v_row.company_name, v_row.location, v_row.url, 'INACTIVE',
      v_row.published_at, v_row.expires_at, v_row.application_due,
      v_row.date_modified, v_row.nav_event_modified_at, v_row.raw_payload
    );

    UPDATE public.job_opportunities
    SET
      status = 'INACTIVE',
      source_payload_hash = v_hash,
      last_reconciled_run_id = p_run_id,
      last_reconciled_at = now(),
      reconciliation_status = 'absent'
    WHERE id = v_row.id;

    INSERT INTO public.nav_event_audit (
      run_id, run_mode, external_id, change_kind, previous_values, new_values
    ) VALUES (
      p_run_id,
      'reconcile',
      v_row.external_id,
      'closeout',
      jsonb_build_object(
        'title', v_row.title,
        'company_name', v_row.company_name,
        'location', v_row.location,
        'url', v_row.url,
        'status', v_row.status,
        'published_at', v_row.published_at,
        'expires_at', v_row.expires_at,
        'application_due', v_row.application_due,
        'source_event_version', COALESCE(v_row.source_event_version, public._nav_compute_event_version_row(v_row)),
        'source_payload_hash', COALESCE(v_row.source_payload_hash, public._nav_compute_payload_hash_row(v_row))
      ),
      jsonb_build_object(
        'title', v_row.title,
        'company_name', v_row.company_name,
        'location', v_row.location,
        'url', v_row.url,
        'status', 'INACTIVE',
        'published_at', v_row.published_at,
        'expires_at', v_row.expires_at,
        'application_due', v_row.application_due,
        'source_event_version', COALESCE(v_row.source_event_version, public._nav_compute_event_version_row(v_row)),
        'source_payload_hash', v_hash,
        'reconciliation_status', 'absent'
      )
    );
    closed_count := closed_count + 1;
  END LOOP;

  PERFORM public._nav_increment_counter(p_run_id, 'reconcile', 'closeout', closed_count);

  SELECT count(*) INTO remaining_count
  FROM public.job_opportunities j
  WHERE j.source = 'nav'
    AND j.status = 'ACTIVE'
    AND COALESCE(j.source_event_version, public._nav_compute_event_version_row(j), '-infinity'::timestamptz) <= v_cutoff
    AND NOT EXISTS (
      SELECT 1
      FROM public.nav_reconcile_snapshot s
      WHERE s.run_id = p_run_id
        AND s.external_id = j.external_id
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

REVOKE ALL ON TABLE public.nav_feed_leases FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.nav_reconcile_runs FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.nav_reconcile_snapshot FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.nav_detail_retry_queue FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.nav_event_audit FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.nav_run_counters FROM PUBLIC, anon, authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.nav_feed_leases TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.nav_reconcile_runs TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.nav_reconcile_snapshot TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.nav_detail_retry_queue TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.nav_event_audit TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.nav_run_counters TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.nav_event_audit_id_seq TO service_role;

REVOKE ALL ON FUNCTION public._nav_safe_to_timestamptz(text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public._nav_canonicalize_payload(jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public._nav_jsonb_rich_merge(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public._nav_compute_event_version_values(timestamptz, timestamptz, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public._nav_compute_event_version_row(public.job_opportunities) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public._nav_utc_text(timestamptz) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public._nav_compute_payload_hash_values(text, text, text, text, text, timestamptz, timestamptz, date, timestamptz, timestamptz, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public._nav_compute_payload_hash_row(public.job_opportunities) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public._nav_increment_counter(uuid, text, text, bigint) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public._nav_apply_opportunity_event(jsonb, uuid, text, text) FROM PUBLIC, anon, authenticated;

REVOKE ALL ON FUNCTION public.claim_nav_feed_lease(text, text, uuid, integer) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.heartbeat_nav_feed_lease(text, uuid, integer) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.release_nav_feed_lease(text, uuid) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.apply_nav_opportunity_events(jsonb, uuid, text, uuid) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.closeout_nav_reconciliation(uuid, integer) FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.claim_nav_feed_lease(text, text, uuid, integer) TO service_role;
GRANT EXECUTE ON FUNCTION public.heartbeat_nav_feed_lease(text, uuid, integer) TO service_role;
GRANT EXECUTE ON FUNCTION public.release_nav_feed_lease(text, uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.apply_nav_opportunity_events(jsonb, uuid, text, uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.closeout_nav_reconciliation(uuid, integer) TO service_role;

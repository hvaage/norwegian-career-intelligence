-- =============================================================================
-- NAV sync run log + status view (Job Buddy scheduled sync)
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- -----------------------------------------------------------------------------
-- nav_sync_run_log
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.nav_sync_run_log (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  started_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz,
  status          text NOT NULL,
  mode            text NOT NULL,
  pages_fetched   integer,
  fetched_count   integer,
  active_count    integer,
  inserted_count  integer,
  updated_count   integer,
  error           text,
  raw_response    jsonb,
  CONSTRAINT nav_sync_run_log_status_check CHECK (
    status IN ('running', 'success', 'failed')
  )
);

COMMENT ON TABLE public.nav_sync_run_log IS
  'Audit log for scheduled and manual nav-feed sync invocations (mode=sync).';

CREATE INDEX IF NOT EXISTS idx_nav_sync_run_log_started_at
  ON public.nav_sync_run_log (started_at DESC);

CREATE INDEX IF NOT EXISTS idx_nav_sync_run_log_status_started_at
  ON public.nav_sync_run_log (status, started_at DESC);

ALTER TABLE public.nav_sync_run_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY nav_sync_run_log_select_authenticated
  ON public.nav_sync_run_log
  FOR SELECT
  TO authenticated
  USING (true);

-- -----------------------------------------------------------------------------
-- nav_sync_status — latest run + last success (admin)
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW public.nav_sync_status AS
SELECT
  (
    SELECT id FROM public.nav_sync_run_log
    ORDER BY started_at DESC LIMIT 1
  ) AS last_run_id,
  (
    SELECT started_at FROM public.nav_sync_run_log
    ORDER BY started_at DESC LIMIT 1
  ) AS last_run_started_at,
  (
    SELECT finished_at FROM public.nav_sync_run_log
    ORDER BY started_at DESC LIMIT 1
  ) AS last_run_finished_at,
  (
    SELECT status FROM public.nav_sync_run_log
    ORDER BY started_at DESC LIMIT 1
  ) AS last_run_status,
  (
    SELECT mode FROM public.nav_sync_run_log
    ORDER BY started_at DESC LIMIT 1
  ) AS last_run_mode,
  (
    SELECT pages_fetched FROM public.nav_sync_run_log
    ORDER BY started_at DESC LIMIT 1
  ) AS last_run_pages_fetched,
  (
    SELECT fetched_count FROM public.nav_sync_run_log
    ORDER BY started_at DESC LIMIT 1
  ) AS last_run_fetched_count,
  (
    SELECT active_count FROM public.nav_sync_run_log
    ORDER BY started_at DESC LIMIT 1
  ) AS last_run_active_count,
  (
    SELECT inserted_count FROM public.nav_sync_run_log
    ORDER BY started_at DESC LIMIT 1
  ) AS last_run_inserted_count,
  (
    SELECT updated_count FROM public.nav_sync_run_log
    ORDER BY started_at DESC LIMIT 1
  ) AS last_run_updated_count,
  (
    SELECT error FROM public.nav_sync_run_log
    WHERE status = 'failed'
    ORDER BY started_at DESC LIMIT 1
  ) AS last_error,
  (
    SELECT finished_at FROM public.nav_sync_run_log
    WHERE status = 'failed'
    ORDER BY started_at DESC LIMIT 1
  ) AS last_error_at,
  (
    SELECT started_at FROM public.nav_sync_run_log
    WHERE status = 'success'
    ORDER BY started_at DESC LIMIT 1
  ) AS last_success_started_at,
  (
    SELECT finished_at FROM public.nav_sync_run_log
    WHERE status = 'success'
    ORDER BY started_at DESC LIMIT 1
  ) AS last_success_at,
  (
    SELECT fetched_count FROM public.nav_sync_run_log
    WHERE status = 'success'
    ORDER BY started_at DESC LIMIT 1
  ) AS last_success_fetched_count,
  (
    SELECT inserted_count FROM public.nav_sync_run_log
    WHERE status = 'success'
    ORDER BY started_at DESC LIMIT 1
  ) AS last_success_inserted_count,
  (
    SELECT updated_count FROM public.nav_sync_run_log
    WHERE status = 'success'
    ORDER BY started_at DESC LIMIT 1
  ) AS last_success_updated_count;

COMMENT ON VIEW public.nav_sync_status IS
  'Single-row summary of the latest NAV sync run and last successful run.';

GRANT SELECT ON public.nav_sync_status TO authenticated, service_role;

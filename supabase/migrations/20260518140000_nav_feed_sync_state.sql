-- =============================================================================
-- Norwegian Career Intelligence — NAV feed sync state (006)
-- =============================================================================
-- Tracks batched backfill and incremental sync cursor (next_url) for NAV feed.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- nav_feed_sync_state
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.nav_feed_sync_state (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source          text NOT NULL,
  mode            text NOT NULL,
  last_next_url   text,
  pages_fetched   integer NOT NULL DEFAULT 0,
  total_fetched   integer NOT NULL DEFAULT 0,
  total_imported  integer NOT NULL DEFAULT 0,
  total_updated   integer NOT NULL DEFAULT 0,
  total_skipped   integer NOT NULL DEFAULT 0,
  started_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz,
  status          text NOT NULL DEFAULT 'in_progress',
  error           text,
  CONSTRAINT nav_feed_sync_state_mode_check CHECK (
    mode IN ('backfill', 'sync')
  ),
  CONSTRAINT nav_feed_sync_state_status_check CHECK (
    status IN ('in_progress', 'completed', 'error')
  ),
  CONSTRAINT nav_feed_sync_state_source_nonempty CHECK (length(trim(source)) > 0)
);

COMMENT ON TABLE public.nav_feed_sync_state IS
  'Cursor and progress for NAV pam-stilling-feed backfill (full history) and incremental sync.';

COMMENT ON COLUMN public.nav_feed_sync_state.last_next_url IS
  'Relative next_url from last processed page; resume fetch starts here.';

CREATE INDEX IF NOT EXISTS idx_nav_feed_sync_state_source_mode_status
  ON public.nav_feed_sync_state (source, mode, status);

CREATE INDEX IF NOT EXISTS idx_nav_feed_sync_state_started_at
  ON public.nav_feed_sync_state (started_at DESC);

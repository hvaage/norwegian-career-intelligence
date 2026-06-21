---- tern: disable-tx ----
-- Build without blocking NAV opportunity writes.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_job_opportunities_nav_changed_external
  ON public.job_opportunities (
    (GREATEST(
      COALESCE(updated_at, '-infinity'::timestamptz),
      COALESCE(date_modified, '-infinity'::timestamptz),
      COALESCE(nav_event_modified_at, '-infinity'::timestamptz),
      COALESCE(imported_at, '-infinity'::timestamptz)
    )),
    external_id
  )
  WHERE source = 'nav';

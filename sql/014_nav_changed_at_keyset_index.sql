-- Run outside an explicit transaction before downstream catch-up.
EXPLAIN (COSTS true, VERBOSE false)
SELECT external_id
FROM public.job_opportunities
WHERE source = 'nav'
  AND ROW(
    GREATEST(
      COALESCE(updated_at, '-infinity'::timestamptz),
      COALESCE(date_modified, '-infinity'::timestamptz),
      COALESCE(nav_event_modified_at, '-infinity'::timestamptz),
      COALESCE(imported_at, '-infinity'::timestamptz)
    ),
    external_id
  ) > ROW('2026-06-17T19:48:39.741Z'::timestamptz, '')
ORDER BY
  GREATEST(
    COALESCE(updated_at, '-infinity'::timestamptz),
    COALESCE(date_modified, '-infinity'::timestamptz),
    COALESCE(nav_event_modified_at, '-infinity'::timestamptz),
    COALESCE(imported_at, '-infinity'::timestamptz)
  ),
  external_id
LIMIT 500;

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

SELECT
  indexrelid::regclass AS index_name,
  indisvalid,
  indisready
FROM pg_index
WHERE indexrelid = 'public.idx_job_opportunities_nav_changed_external'::regclass;

EXPLAIN (COSTS true, VERBOSE false)
SELECT external_id
FROM public.job_opportunities
WHERE source = 'nav'
  AND ROW(
    GREATEST(
      COALESCE(updated_at, '-infinity'::timestamptz),
      COALESCE(date_modified, '-infinity'::timestamptz),
      COALESCE(nav_event_modified_at, '-infinity'::timestamptz),
      COALESCE(imported_at, '-infinity'::timestamptz)
    ),
    external_id
  ) > ROW('2026-06-17T19:48:39.741Z'::timestamptz, '')
ORDER BY
  GREATEST(
    COALESCE(updated_at, '-infinity'::timestamptz),
    COALESCE(date_modified, '-infinity'::timestamptz),
    COALESCE(nav_event_modified_at, '-infinity'::timestamptz),
    COALESCE(imported_at, '-infinity'::timestamptz)
  ),
  external_id
LIMIT 500;

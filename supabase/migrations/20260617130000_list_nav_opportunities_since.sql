-- =============================================================================
-- NAV opportunities read API for downstream canonical opportunity sync
-- =============================================================================
-- Returns ACTIVE and INACTIVE NAV rows changed after a stable tuple cursor.
-- Intended caller: app-side sync-nav-opportunities with NAV source service role.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.list_nav_opportunities_since(
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
      greatest(
        coalesce(jo.updated_at, '-infinity'::timestamptz),
        coalesce(jo.date_modified, '-infinity'::timestamptz),
        coalesce(jo.nav_event_modified_at, '-infinity'::timestamptz),
        coalesce(jo.imported_at, '-infinity'::timestamptz)
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
    b.changed_at
  FROM base b
  WHERE
    b.changed_at > coalesce(p_since, '-infinity'::timestamptz)
    OR (
      b.changed_at = coalesce(p_since, '-infinity'::timestamptz)
      AND b.external_id > coalesce(p_after_external_id, '')
    )
  ORDER BY b.changed_at ASC, b.external_id ASC
  LIMIT least(greatest(coalesce(p_limit, 500), 1), 1000);
$$;

COMMENT ON FUNCTION public.list_nav_opportunities_since(timestamptz, text, int) IS
  'Read-only NAV opportunity cursor API for downstream sync. Returns ACTIVE and INACTIVE rows sorted by (changed_at, external_id).';

REVOKE ALL ON FUNCTION public.list_nav_opportunities_since(timestamptz, text, int)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.list_nav_opportunities_since(timestamptz, text, int)
  TO service_role;

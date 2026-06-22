-- Temporary service-role-only helper. A later migration drops it after rotation.
CREATE OR REPLACE FUNCTION public.rotate_nav_feed_service_role_key(
  p_secret text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, vault, pg_temp
AS $$
DECLARE
  existing_secret_id uuid;
BEGIN
  IF p_secret IS NULL OR length(btrim(p_secret)) < 20 THEN
    RAISE EXCEPTION 'A non-empty service key is required';
  END IF;

  SELECT secret.id INTO existing_secret_id
  FROM vault.secrets AS secret
  WHERE secret.name = 'nav_feed_service_role_key'
  ORDER BY secret.updated_at DESC NULLS LAST
  LIMIT 1;

  IF existing_secret_id IS NULL THEN
    PERFORM vault.create_secret(
      btrim(p_secret),
      'nav_feed_service_role_key',
      'Service key used by NAV pg_cron invokers'
    );
  ELSE
    PERFORM vault.update_secret(
      existing_secret_id,
      btrim(p_secret),
      'nav_feed_service_role_key',
      'Service key used by NAV pg_cron invokers'
    );
  END IF;
END;
$$;

ALTER FUNCTION public.rotate_nav_feed_service_role_key(text) OWNER TO postgres;
REVOKE ALL ON FUNCTION public.rotate_nav_feed_service_role_key(text)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.rotate_nav_feed_service_role_key(text)
  TO service_role;

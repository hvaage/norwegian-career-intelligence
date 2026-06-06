-- Allow frontend (anon/authenticated) to read filtered NAV jobs via view only.

GRANT SELECT ON public.valid_nav_jobs TO anon, authenticated;

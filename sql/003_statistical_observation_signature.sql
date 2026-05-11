-- =============================================================================
-- Norwegian Career Intelligence — observation_signature (003)
-- =============================================================================
-- Adds first-class observation_signature to statistical_observations for
-- deterministic duplicate detection and replay-safe imports (see
-- docs/ssb-import-validation-checklist.md and scripts/import_ssb_observations.py).
--
-- Scope:
--   - public.statistical_observations only
--
-- Explicitly NOT included:
--   - signals, gaps, recommendations
--   - other tables
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1) Column: observation_signature (text, hex digest from importer)
-- -----------------------------------------------------------------------------
ALTER TABLE public.statistical_observations
  ADD COLUMN IF NOT EXISTS observation_signature text;

COMMENT ON COLUMN public.statistical_observations.observation_signature IS
  'Deterministic SHA-256 hex digest over table_id, source_file, period, contents_code, '
  'ordered dimension codes, and normalization_version; used for idempotent import / '
  'duplicate protection.';

-- -----------------------------------------------------------------------------
-- 2) Backfill from JSON (metadata_json preferred; raw_observation_json fallback)
-- -----------------------------------------------------------------------------
UPDATE public.statistical_observations
SET observation_signature = COALESCE(
  NULLIF(btrim(metadata_json->>'observation_signature'), ''),
  NULLIF(btrim(raw_observation_json->>'observation_signature'), '')
)
WHERE observation_signature IS NULL
  AND (
    NULLIF(btrim(metadata_json->>'observation_signature'), '') IS NOT NULL
    OR NULLIF(btrim(raw_observation_json->>'observation_signature'), '') IS NOT NULL
  );

-- -----------------------------------------------------------------------------
-- 3) Block migration if duplicate non-null signatures would violate uniqueness
-- -----------------------------------------------------------------------------
DO $$
DECLARE
  dup_count integer;
BEGIN
  SELECT count(*)::integer
  INTO dup_count
  FROM (
    SELECT observation_signature
    FROM public.statistical_observations
    WHERE observation_signature IS NOT NULL
    GROUP BY observation_signature
    HAVING count(*) > 1
  ) d;

  IF dup_count > 0 THEN
    RAISE EXCEPTION
      '003_statistical_observation_signature: % duplicate observation_signature group(s) '
      '(non-null values appearing more than once). Resolve duplicates, then re-run.',
      dup_count
      USING ERRCODE = '23505';
  END IF;
END;
$$;

-- -----------------------------------------------------------------------------
-- 4) Indexes (IF NOT EXISTS; partial unique for non-null signatures only)
-- -----------------------------------------------------------------------------

-- Partial unique index: multiple NULLs allowed; each non-null signature at most once.
CREATE UNIQUE INDEX IF NOT EXISTS uq_statistical_observations_observation_signature_not_null
  ON public.statistical_observations (observation_signature)
  WHERE (observation_signature IS NOT NULL);

-- General btree on the column (includes NULL rows for scans / future backfills).
CREATE INDEX IF NOT EXISTS idx_statistical_observations_observation_signature
  ON public.statistical_observations (observation_signature);

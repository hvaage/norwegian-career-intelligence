# SSB API test plan (PxWebApi v2)

## Purpose

This technical test verifies that we can access selected SSB tables through **PxWebApi v2**, inspect metadata, and retrieve a small sample dataset before we build full extraction and signal pipelines.

The goal is to confirm:

- endpoint availability,
- metadata structure and dimensions,
- period fields and output format hints,
- feasibility of small `json-stat2` requests.

## API endpoints in this test

For each table id (`11615`, `12850`, `08417`, `09793`):

- Basic metadata:  
  `https://data.ssb.no/api/pxwebapi/v2/tables/{table_id}?lang=no`
- Detailed metadata:  
  `https://data.ssb.no/api/pxwebapi/v2/tables/{table_id}/metadata?lang=no`
- Sample data attempt (prefer `json-stat2`):  
  `https://data.ssb.no/api/pxwebapi/v2/tables/{table_id}/data?lang=no&outputFormat=json-stat2`

The scripts first attempt a filtered small POST selection based on metadata, and fall back to a GET `json-stat2` request if needed.

## Additional labor-market structure tables

This test plan now also includes:

- `08417`
- `09793`

These are useful beyond graduate-focused analyses because they help describe broader labor-market structure (industry composition, employment distribution, and structural context), which is needed for:

- role-family context across sectors,
- trajectory and transition realism,
- market/risk framing that is not limited to entry-level pipelines.

They support whole-labor-market intelligence by complementing education and trainee sources with population-level structure data.

## Output files

The script saves raw responses to:

- `data/raw/ssb/{table_id}_basic_metadata.json`
- `data/raw/ssb/{table_id}_metadata.json`
- `data/raw/ssb/{table_id}_sample_data.json`

## Limits and safeguards

- Handles HTTP `404`, `429`, and `503` with clear console messages.
- Handles non-JSON or malformed JSON safely.
- Handles too-large query style errors and metadata-shape issues safely.
- Writes diagnostic payloads when requests fail.
- Does **not** write to Supabase and does **not** normalize data.

## Why only small samples now

We still fetch very small samples only (latest period where possible, then latest two periods fallback) because this phase is technical exploration:

- verify endpoint behavior,
- verify query mechanics per table,
- verify metadata dimensions and cardinality,
- avoid premature large pulls before extraction and confidence rules are finalized.

This keeps the test repeatable, cheap, and explainable while we harden ingestion strategy.

## How this becomes verified statistical signals later

After this test is stable, the next step is to convert selected table outputs into structured, provenance-linked market/supply signals:

1. Canonicalize source + dataset references (already defined in schema/spec docs).
2. Extract specific metrics with period windows.
3. Attach confidence category as `verified_statistical` only when metric lineage is complete.
4. Feed those signals into gap/overlap/recommendation logic with explicit citation paths.

This test intentionally stops before those steps; it only validates API access and payload shape.


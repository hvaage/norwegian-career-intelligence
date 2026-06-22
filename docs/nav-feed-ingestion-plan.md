# NAV pam-stilling-feed — ingestion plan (initial)

## Purpose

This document describes how we verify connectivity and response shape against NAV's **pam-stilling-feed** before a full production ingestion module is available. The goal is a small, repeatable technical test—not full ingestion, Supabase, or AI extraction.

Official references:

- [Swagger (dev)](https://pam-stilling-feed.ekstern.dev.nav.no/swagger)
- [OpenAPI JSON](https://pam-stilling-feed.ekstern.dev.nav.no/api/openapi.json)
- [Product documentation](https://navikt.github.io/pam-stilling-feed/)
- Production feed host: `https://pam-stilling-feed.nav.no`

## Supabase Edge Function (`nav-feed`)

For browser/API testing without exposing the NAV token:

1. Deploy: `supabase functions deploy nav-feed`
2. Set secret (optional; without it the function fetches a fresh token from `/api/publicToken`):
   `supabase secrets set NAV_FEED_TOKEN=<token>`
3. Open `public/nav-feed-test.html` (local static server), enter **Supabase URL** and **anon key**, click **Test NAV integrasjon**.

The function calls `GET https://pam-stilling-feed.nav.no/api/v1/feed` with `Authorization: Bearer …`. On **401/403** it logs the response body, fetches a new token from `https://pam-stilling-feed.nav.no/api/publicToken`, and retries once.

**Database:** run `sql/005_job_opportunities.sql` before import. The `nav-feed` Edge Function upserts ACTIVE rows into `job_opportunities` (`source` + `external_id` unique).

## How to run (Python smoke test)

1. **Python environment**

   ```bash
   cd /path/to/norwegian-career-intelligence
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configuration (optional)**

   Copy `.env.example` to `.env` and set variables as needed.
   `NAV_FEED_BASE_URL` defaults to the production host if unset.
   If `NAV_FEED_TOKEN` is unset on the production host, the smoke test fetches
   NAV's current public token from `/api/publicToken`. The public token rotates
   irregularly and is suitable for experiments/smoke tests; a registered private
   token should be used for durable production ingestion.

3. **Execute the smoke test**

   ```bash
   python scripts/test_nav_feed.py
   ```

   Raw HTTP bodies are written to:

   - `data/raw/sample_feed.json` — response from `GET /api/v1/feed` (always written for that response)
   - `data/raw/sample_entry.json` — response from `GET /api/v1/feedentry/{entryId}` only when the feed response contains at least one entry id (otherwise this file is not created)

## Expected outcomes

- **With a valid Bearer token** or the current NAV public token: HTTP 200 on `/api/v1/feed`, JSON with top-level keys including `items` (per OpenAPI `Feed` schema). The script prints URL, status, selected headers (`ETag`, `Last-Modified`, `Content-Type`, `Link`, etc.), keys, and the first three `items`. It detects an entry URL/path from the first item, calls that entry endpoint, and saves both raw files.

- **Without a token** (or wrong token): the API typically returns **401** with a small JSON body (for example `title`, `status`, `type`, `details`) rather than a `Feed` with `items`. The script reports status, relevant headers, top-level keys, and saves `sample_feed.json` for inspection. It exits non-zero when no `items` list is present.

- **Wrong or expired token**: **401**/**403** with a token set — the script suggests verifying the token value and validity.

- **304 Not Modified**: possible when using conditional headers later; the current script does not send `If-None-Match` / `If-Modified-Since` on the first request.

- **Non-JSON or unexpected shape**: the script surfaces parse errors, a short body snippet, and does not assume Supabase or downstream pipelines.

## Next steps after the NAV token is received

1. Keep the smoke test green against the **production** base URL.
2. Design **pagination**: follow `next_url` / `next_id` from the `Feed` object (see OpenAPI) and persist cursors or ETags for incremental pulls.
3. Add a dedicated **ingestion module** (out of scope for this test): download pages, store immutable raw JSON, then normalize in a separate step.
4. Harden operations: retries, backoff, structured logging, and metrics—still without coupling this smoke test to storage or AI.

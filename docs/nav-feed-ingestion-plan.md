# NAV pam-stilling-feed — ingestion plan (initial)

## Purpose

This document describes how we verify connectivity and response shape against NAV's **pam-stilling-feed** before a production API token is available. The goal is a small, repeatable technical test—not full ingestion, Supabase, or AI extraction.

Official references:

- [Swagger (dev)](https://pam-stilling-feed.ekstern.dev.nav.no/swagger)
- [OpenAPI JSON](https://pam-stilling-feed.ekstern.dev.nav.no/api/openapi.json)
- [Product documentation](https://navikt.github.io/pam-stilling-feed/)

## How to run

1. **Python environment**

   ```bash
   cd /path/to/norwegian-career-intelligence
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configuration (optional)**

   Copy `.env.example` to `.env` and set variables as needed.  
   `NAV_FEED_BASE_URL` defaults to the dev host if unset.

3. **Execute the smoke test**

   ```bash
   python scripts/test_nav_feed.py
   ```

   Raw HTTP bodies are written to:

   - `data/raw/sample_feed.json` — response from `GET /api/v1/feed` (always written for that response)
   - `data/raw/sample_entry.json` — response from `GET /api/v1/feedentry/{entryId}` only when the feed response contains at least one entry id (otherwise this file is not created)

## Expected outcomes

- **With a valid Bearer token** (when the environment requires it): HTTP 200 on `/api/v1/feed`, JSON with top-level keys including `items` (per OpenAPI `Feed` schema). The script prints URL, status, selected headers (`ETag`, `Last-Modified`, `Content-Type`, `Link`, etc.), keys, and the first three `items`. It detects an entry `id` from an item, then calls `/api/v1/feedentry/{entryId}` and saves both raw files.

- **Without a token** (or wrong token): the current **dev** host typically returns **401** with a small JSON body (for example `title`, `status`, `type`, `details`) rather than a `Feed` with `items`. The script still reports status, relevant headers, top-level keys, and saves `sample_feed.json` for inspection. It prints explicit guidance about `NAV_FEED_TOKEN` when auth fails.

- **Wrong or expired token**: **401**/**403** with a token set — the script suggests verifying the token value and validity.

- **304 Not Modified**: possible when using conditional headers later; the current script does not send `If-None-Match` / `If-Modified-Since` on the first request.

- **Non-JSON or unexpected shape**: the script surfaces parse errors, a short body snippet, and does not assume Supabase or downstream pipelines.

## Next steps after the NAV token is received

1. Confirm the same script against the **production** base URL (set `NAV_FEED_BASE_URL` accordingly) and validate 200 responses end-to-end.
2. Design **pagination**: follow `next_url` / `next_id` from the `Feed` object (see OpenAPI) and persist cursors or ETags for incremental pulls.
3. Add a dedicated **ingestion module** (out of scope for this test): download pages, store immutable raw JSON, then normalize in a separate step.
4. Harden operations: retries, backoff, structured logging, and metrics—still without coupling this smoke test to storage or AI.

# NAV upstream contract v1

Frozen: 2026-06-21

This contract is owned by `norwegian-career-intelligence`. Downstream services
must use the service-role RPCs and must never call the NAV feed from a client.
Both Edge Function write endpoints perform their own constant-time service-role
header check; `verify_jwt=false` is only used so current `sb_secret_*` keys work.

## Deployment order

1. Pause `nav_feed_sync_every_15min` for the deployment window.
2. Apply `20260621120000_nav_upstream_repair_foundation.sql`.
3. Run `sql/014_nav_changed_at_keyset_index.sql` outside a transaction.
4. Verify the index is valid in `pg_index`.
5. Apply `20260621120100_nav_source_contracts.sql`.
6. Apply `20260621120300_nav_repair_invokers.sql`.
7. Deploy `nav-feed` and `nav-feed-enrich`.
8. Invoke one steady sync and replay the same page. The replay must produce
   `noOpCount > 0` without changing opportunity `updated_at` values.
9. Re-enable steady cron. Start reconciliation explicitly with
   `invoke_nav_reconcile(NULL, true)` only after the steady check is green.

No migration performs a metadata backfill over existing opportunities. Event
version and hash are computed on read until a real source change is applied.

## Cursor RPC

`list_nav_opportunities_since(timestamptz, text, int)` keeps its input defaults.
The return order is fixed:

1. `external_id text`
2. `title text`
3. `company_name text`
4. `location text`
5. `url text`
6. `published_at timestamptz`
7. `expires_at timestamptz`
8. `application_due date`
9. `status text`
10. `date_modified timestamptz`
11. `nav_event_modified_at timestamptz`
12. `updated_at timestamptz`
13. `raw_payload jsonb`
14. `source_event_version timestamptz`
15. `source_payload_hash text`
16. `source_event_id text`
17. `changed_at timestamptz`

The first 13 fields preserve M5.6. The cursor is the tuple
`(changed_at, external_id)`, ordered ascending. Only `service_role` may execute
the function.

## By-ID RPC

`list_nav_opportunities_by_external_ids(text[])` accepts at most 500 unique,
non-empty IDs. Duplicate and blank input values are removed. It returns fields
1-11 above, then `raw_payload`, `source_event_version`,
`source_payload_hash`, and `source_event_id`. `application_due` is `date`.

The target by-ID backfill is read-only upstream and does not claim an upstream
writer lease.

## Event semantics

Trusted event time is the newest valid value from:

- `nav_event_modified_at`
- `date_modified`
- `_feed_entry.sistEndret`
- `nav_detail.sistEndret`
- `nav_detail.ad_content.updated`
- `nav_detail.json.updated`

Local `updated_at`, `imported_at`, polling time, and reconciliation observation
time are never source event time.

`source_payload_hash` is a deterministic content hash over the final persisted
scalar fields and the canonical merged raw payload. Sparse events preserve rich stored data. Older
events are ignored, identical events are no-ops, same-version richer events
fill gaps, and newer events merge normally. No-op and stale events do not write
the opportunity row or audit table.

INACTIVE never deletes a row or raw history. First-seen reconciliation absence
does not invent a source timestamp. Closeout preserves `source_event_version`,
marks `reconciliation_status='absent'`, and is independent of detail retries.

## Writer leases

Every upstream writer claims `nav_writer` and then one mode lease:

- `nav_steady`
- `nav_reconcile`
- `nav_backfill` (legacy backfill and detail retry)

Lease TTL is five minutes with 30-second heartbeat. Releases are compare-and-set
by `run_id` and happen mode-first, writer-second. A failed secondary claim
releases the shared writer lease.

## Feed modes

- `sync`: bootstraps `?last` once, persists the actual tail page and validators,
  polls that page, and follows every `next_url` in order.
- `reconcile`: snapshots events from `window_started_at`, a six-month
  `If-Modified-Since` boundary, and resumes by `run_id`. `cutoff_event_ts` is
  the high-watermark captured when the run starts; closeout only changes rows
  whose event version is not newer than that high-watermark.
- `enrich_active`: processes the persistent detail retry queue with backoff.
- `backfill`: preserves the separate historical cursor and is manual only.

## Target handoff

Lovable may begin target work after the upstream RPC smoke tests pass. Target
must:

- append the three source metadata fields to its NAV adapter;
- use by-ID batches of at most 500 for existing source postings;
- merge payloads without deleting rich target history;
- preserve all `user_opportunities`, statuses, and AI fields;
- recalculate lifecycle from source evidence, not mirror observation time;
- catch up the tuple cursor until source and target ACTIVE counts agree, with
  temporary differences explained by in-flight batches;
- expose health read-only in `/admin/sync`; no Jobb-leads frontend change.

## Rollback

Operational rollback is applied in reverse deployment order: pause new cron,
restore the previous Edge Function, restore invokers/RPCs, then remove the
index only if query plans require it. Applied scalar lifecycle changes can be
reversed from `nav_event_audit` in reverse audit order. Raw payload corrections
are made by a new reconciliation run; audit intentionally does not store full
previous payload copies.

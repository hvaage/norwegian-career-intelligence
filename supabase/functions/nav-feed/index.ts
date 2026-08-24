/**
 * NAV pam-stilling-feed mirror.
 *
 * Modes:
 * - sync: permanent tail polling with ETag/Last-Modified
 * - reconcile: resumable six-month snapshot and ACTIVE closeout
 * - backfill: legacy full-history cursor, now using conditional writes
 * - enrich_active: retry missing ACTIVE details
 * - test_feedentry: read-only feedentry inspection
 */

import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import {
  createClient,
  type SupabaseClient,
} from "https://esm.sh/@supabase/supabase-js@2.49.1";
import {
  buildActiveEvent,
  buildInactiveEvent,
  isActive,
  isInactive,
  type NavFeedEntryDetail,
  type NavFeedItem,
  type NavOpportunityEvent,
} from "../_shared/nav-event.ts";
import { hasServiceRole } from "../_shared/service-auth.ts";

const NAV_PUBLIC_TOKEN_URL = "https://pam-stilling-feed.nav.no/api/publicToken";
const NAV_FEED_BASE = "https://pam-stilling-feed.nav.no";
const FEED_START_PATH = "/api/v1/feed";
const FEED_LAST_PATH = "/api/v1/feed?last";
const SOURCE_NAV = "nav";
const APPLY_CHUNK_SIZE = 100;
const LEASE_TTL_SECONDS = 300;
const LEASE_HEARTBEAT_MS = 30_000;
const DEFAULT_SYNC_MAX_PAGES = 10;
const DEFAULT_BACKFILL_MAX_PAGES = 25;
const DEFAULT_RECONCILE_MAX_PAGES = 25;
const DEFAULT_ENRICH_MAX_ROWS = 100;
const DEFAULT_DETAIL_BUDGET = 500;

const corsHeaders: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

type WriteMode = "sync" | "reconcile" | "backfill" | "enrich_active";
type RequestMode = WriteMode | "test_feedentry";

type RunRequest = {
  mode: WriteMode;
  maxPages: number;
  maxDetails: number;
  startFresh: boolean;
  runId: string | null;
};

type TestRequest = {
  mode: "test_feedentry";
  feedEntryId: string;
};

type ParsedRequest = RunRequest | TestRequest;

type FeedBody = {
  items?: NavFeedItem[];
  feed_url?: string | null;
  next_url?: string | null;
};

type FetchValidators = {
  etag?: string | null;
  lastModified?: string | null;
};

type FetchResult = {
  response: Response;
  rawBody: string;
  token: string;
  responseUrl: string;
  etag: string | null;
  lastModified: string | null;
};

type ApplyStats = {
  inserted: number;
  merged: number;
  noOp: number;
  staleIgnored: number;
};

type DetailStats = {
  attempted: number;
  succeeded: number;
  failed: number;
  queued: number;
};

type PageEventsResult = {
  token: string;
  events: NavOpportunityEvent[];
  resolvedDetailIds: string[];
  queuedDetails: Array<{ externalId: string; error: string }>;
  activeCount: number;
  inactiveCount: number;
  skippedCount: number;
  detailStats: DetailStats;
};

type SyncState = {
  id: string;
  source: string;
  mode: string;
  last_next_url: string | null;
  feed_url: string | null;
  feed_etag: string | null;
  feed_last_modified: string | null;
  pages_fetched: number;
  total_fetched: number;
  total_imported: number;
  total_updated: number;
  total_skipped: number;
  started_at: string;
  status: string;
};

type ReconcileRun = {
  run_id: string;
  status: "running" | "snapshot_complete" | "closing" | "completed" | "error";
  window_started_at: string;
  cutoff_event_ts: string;
  current_feed_url: string;
  feed_etag: string | null;
  feed_last_modified: string | null;
  pages_fetched: number;
  events_seen: number;
  active_seen: number;
  inactive_seen: number;
  detail_success: number;
  detail_failure: number;
  feed_tail_reached: boolean;
};

type RunResult = {
  ok: boolean;
  mode: WriteMode;
  runId: string;
  status: string;
  leaseBusy: boolean;
  pagesFetched: number;
  fetchedCount: number;
  activeCount: number;
  inactiveCount: number;
  skippedCount: number;
  insertedCount: number;
  updatedCount: number;
  noOpCount: number;
  staleIgnoredCount: number;
  detailFetchedCount: number;
  detailFailedCount: number;
  detailQueuedCount: number;
  lastFeedUrl: string | null;
  finished: boolean;
  error: string | null;
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

function getSupabase(): SupabaseClient {
  const url = Deno.env.get("SUPABASE_URL")?.trim();
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")?.trim();
  if (!url || !key) {
    throw new Error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required");
  }
  return createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

function clamp(value: unknown, fallback: number, max: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return fallback;
  }
  return Math.min(Math.floor(value), max);
}

async function parseRequest(req: Request): Promise<ParsedRequest> {
  if (req.method === "GET") {
    return {
      mode: "sync",
      maxPages: DEFAULT_SYNC_MAX_PAGES,
      maxDetails: DEFAULT_DETAIL_BUDGET,
      startFresh: false,
      runId: null,
    };
  }

  let body: Record<string, unknown> = {};
  try {
    body = await req.json();
  } catch {
    // Empty POST is a normal steady-sync request.
  }

  if (body.mode === "test_feedentry") {
    const id = typeof body.feedEntryId === "string"
      ? body.feedEntryId.trim()
      : "";
    if (!id) throw new Error("feedEntryId is required for test_feedentry");
    return { mode: "test_feedentry", feedEntryId: id };
  }

  const mode: WriteMode = body.mode === "reconcile" ||
      body.mode === "backfill" || body.mode === "enrich_active"
    ? body.mode
    : "sync";
  const defaultLimit = mode === "sync"
    ? DEFAULT_SYNC_MAX_PAGES
    : mode === "reconcile"
    ? DEFAULT_RECONCILE_MAX_PAGES
    : mode === "enrich_active"
    ? DEFAULT_ENRICH_MAX_ROWS
    : DEFAULT_BACKFILL_MAX_PAGES;

  return {
    mode,
    maxPages: clamp(body.maxPages ?? body.maxRows, defaultLimit, 500),
    maxDetails: clamp(body.maxDetails, DEFAULT_DETAIL_BUDGET, 1000),
    startFresh: body.startFresh === true,
    runId: typeof body.runId === "string" && body.runId.trim()
      ? body.runId.trim()
      : null,
  };
}

function resolveFeedUrl(pathOrUrl: string): string {
  const raw = pathOrUrl.trim();
  if (raw.startsWith("http://") || raw.startsWith("https://")) return raw;
  return `${NAV_FEED_BASE}${raw.startsWith("/") ? raw : `/${raw}`}`;
}

function feedEntryPath(id: string): string {
  return `/api/v1/feedentry/${encodeURIComponent(id)}`;
}

async function fetchFreshPublicToken(): Promise<string> {
  const response = await fetch(NAV_PUBLIC_TOKEN_URL, {
    headers: { Accept: "application/json" },
  });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(
      `NAV publicToken HTTP ${response.status}: ${body.slice(0, 200)}`,
    );
  }
  const match = body.match(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
  if (!match?.[0]) {
    throw new Error("NAV publicToken response did not contain a JWT");
  }
  return match[0];
}

async function resolveToken(): Promise<string> {
  return Deno.env.get("NAV_FEED_TOKEN")?.trim() ||
    await fetchFreshPublicToken();
}

async function fetchNav(
  token: string,
  pathOrUrl: string,
  validators: FetchValidators = {},
): Promise<FetchResult> {
  const url = resolveFeedUrl(pathOrUrl);
  const headers: Record<string, string> = {
    Accept: "application/json",
    Authorization: `Bearer ${token}`,
  };
  if (validators.etag) headers["If-None-Match"] = validators.etag;
  if (validators.lastModified) {
    headers["If-Modified-Since"] = validators.lastModified;
  }

  let response = await fetch(url, { headers });
  if (response.status === 401 || response.status === 403) {
    token = await fetchFreshPublicToken();
    headers.Authorization = `Bearer ${token}`;
    response = await fetch(url, { headers });
  }

  const rawBody = response.status === 304 ? "" : await response.text();
  if (!response.ok && response.status !== 304) {
    console.error(
      `[nav-feed] NAV ${response.status}: ${rawBody.slice(0, 500)}`,
    );
  }
  return {
    response,
    rawBody,
    token,
    responseUrl: response.url || url,
    etag: response.headers.get("etag"),
    lastModified: response.headers.get("last-modified"),
  };
}

async function fetchDetail(
  token: string,
  item: NavFeedItem,
): Promise<{ token: string; detail: NavFeedEntryDetail }> {
  const externalId = item.id?.trim();
  const path = item.url?.trim() ||
    (externalId ? feedEntryPath(externalId) : "");
  if (!path) throw new Error("NAV item has no detail URL or external ID");
  const result = await fetchNav(token, path);
  if (!result.response.ok) {
    throw new Error(`feedentry HTTP ${result.response.status}`);
  }
  try {
    return {
      token: result.token,
      detail: JSON.parse(result.rawBody) as NavFeedEntryDetail,
    };
  } catch {
    throw new Error("feedentry returned invalid JSON");
  }
}

function emptyApplyStats(): ApplyStats {
  return { inserted: 0, merged: 0, noOp: 0, staleIgnored: 0 };
}

function addApplyStats(target: ApplyStats, source: ApplyStats): void {
  target.inserted += source.inserted;
  target.merged += source.merged;
  target.noOp += source.noOp;
  target.staleIgnored += source.staleIgnored;
}

function chunks<T>(values: T[], size: number): T[][] {
  const result: T[][] = [];
  for (let index = 0; index < values.length; index += size) {
    result.push(values.slice(index, index + size));
  }
  return result;
}

async function applyEvents(
  supabase: SupabaseClient,
  events: NavOpportunityEvent[],
  runId: string,
  mode: WriteMode,
  reconcileRunId: string | null = null,
): Promise<ApplyStats> {
  const total = emptyApplyStats();
  for (const batch of chunks(events, APPLY_CHUNK_SIZE)) {
    const { data, error } = await supabase.rpc("apply_nav_opportunity_events", {
      p_events: batch,
      p_run_id: runId,
      p_run_mode: mode,
      p_reconcile_run_id: reconcileRunId,
    });
    if (error) {
      throw new Error(`conditional NAV merge failed: ${error.message}`);
    }
    const row = Array.isArray(data) ? data[0] : data;
    addApplyStats(total, {
      inserted: Number(row?.inserted_count ?? 0),
      merged: Number(row?.merged_count ?? 0),
      noOp: Number(row?.no_op_count ?? 0),
      staleIgnored: Number(row?.stale_ignored_count ?? 0),
    });
  }
  return total;
}

async function resolveDetailRetry(
  supabase: SupabaseClient,
  externalId: string,
): Promise<void> {
  const now = new Date().toISOString();
  const { error } = await supabase.from("nav_detail_retry_queue").upsert({
    external_id: externalId,
    status: "resolved",
    next_attempt_at: now,
    last_error: null,
    updated_at: now,
    resolved_at: now,
  }, { onConflict: "external_id" });
  if (error) {
    console.warn(
      `[nav-feed] could not resolve detail retry ${externalId}: ${error.message}`,
    );
  }
}

async function mapPageEvents(
  token: string,
  items: NavFeedItem[],
  detailBudget: { remaining: number },
): Promise<PageEventsResult> {
  const events: NavOpportunityEvent[] = [];
  const resolvedDetailIds: string[] = [];
  const queuedDetails: Array<{ externalId: string; error: string }> = [];
  const detailStats: DetailStats = {
    attempted: 0,
    succeeded: 0,
    failed: 0,
    queued: 0,
  };
  let activeCount = 0;
  let inactiveCount = 0;
  let skippedCount = 0;
  let currentToken = token;

  for (const item of items) {
    if (isInactive(item)) {
      inactiveCount += 1;
      const event = buildInactiveEvent(
        NAV_FEED_BASE,
        item,
        new Date().toISOString(),
      );
      if (event) events.push(event);
      else skippedCount += 1;
      continue;
    }

    if (!isActive(item)) {
      skippedCount += 1;
      continue;
    }

    activeCount += 1;
    const externalId = item.id?.trim();
    let event: NavOpportunityEvent | null = null;

    if (detailBudget.remaining > 0) {
      detailBudget.remaining -= 1;
      detailStats.attempted += 1;
      try {
        const fetched = await fetchDetail(currentToken, item);
        currentToken = fetched.token;
        event = buildActiveEvent(NAV_FEED_BASE, item, fetched.detail);
        detailStats.succeeded += 1;
        if (externalId) resolvedDetailIds.push(externalId);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        detailStats.failed += 1;
        event = buildActiveEvent(NAV_FEED_BASE, item);
        if (externalId) {
          queuedDetails.push({ externalId, error: message });
          detailStats.queued += 1;
        }
      }
    } else {
      event = buildActiveEvent(NAV_FEED_BASE, item);
      if (externalId) {
        queuedDetails.push({
          externalId,
          error: "detail budget exhausted",
        });
        detailStats.queued += 1;
      }
    }

    if (event) events.push(event);
    else skippedCount += 1;
  }

  return {
    token: currentToken,
    events,
    resolvedDetailIds,
    queuedDetails,
    activeCount,
    inactiveCount,
    skippedCount,
    detailStats,
  };
}

async function startRunLog(
  supabase: SupabaseClient,
  mode: WriteMode,
): Promise<string> {
  const { data, error } = await supabase.from("nav_sync_run_log").insert({
    status: "running",
    mode,
  }).select("id").single();
  if (error || !data?.id) {
    throw new Error(`could not create NAV run log: ${error?.message}`);
  }
  return String(data.id);
}

async function finishRunLog(
  supabase: SupabaseClient,
  logId: string,
  result: RunResult,
): Promise<void> {
  const { error } = await supabase.from("nav_sync_run_log").update({
    status: result.ok ? "success" : "failed",
    finished_at: new Date().toISOString(),
    pages_fetched: result.pagesFetched,
    fetched_count: result.fetchedCount,
    active_count: result.activeCount,
    inserted_count: result.insertedCount,
    updated_count: result.updatedCount,
    error: result.error,
    raw_response: result,
  }).eq("id", logId);
  if (error) {
    console.error(`[nav-feed] run log completion failed: ${error.message}`);
  }
}

function leaseName(mode: WriteMode): string {
  if (mode === "sync") return "nav_steady";
  if (mode === "reconcile") return "nav_reconcile";
  return "nav_backfill";
}

async function claimLease(
  supabase: SupabaseClient,
  name: string,
  mode: string,
  runId: string,
): Promise<boolean> {
  const { data, error } = await supabase.rpc("claim_nav_feed_lease", {
    p_lock_name: name,
    p_mode: mode,
    p_run_id: runId,
    p_ttl_seconds: LEASE_TTL_SECONDS,
  });
  if (error) throw new Error(`could not claim ${name}: ${error.message}`);
  return data === true;
}

async function releaseLease(
  supabase: SupabaseClient,
  name: string,
  runId: string,
): Promise<void> {
  const { error } = await supabase.rpc("release_nav_feed_lease", {
    p_lock_name: name,
    p_run_id: runId,
  });
  if (error) {
    console.error(`[nav-feed] could not release ${name}: ${error.message}`);
  }
}

async function withWriterLeases<T>(
  supabase: SupabaseClient,
  mode: WriteMode,
  runId: string,
  operation: () => Promise<T>,
): Promise<{ leaseBusy: boolean; value?: T }> {
  const modeLease = leaseName(mode);
  if (!await claimLease(supabase, "nav_writer", "shared_writer", runId)) {
    return { leaseBusy: true };
  }

  let modeClaimed = false;
  let heartbeatTimer: number | undefined;
  try {
    modeClaimed = await claimLease(supabase, modeLease, mode, runId);
    if (!modeClaimed) return { leaseBusy: true };

    heartbeatTimer = setInterval(() => {
      for (const name of ["nav_writer", modeLease]) {
        void supabase.rpc("heartbeat_nav_feed_lease", {
          p_lock_name: name,
          p_run_id: runId,
          p_ttl_seconds: LEASE_TTL_SECONDS,
        }).then(({ data, error }) => {
          if (error || data !== true) {
            console.error(
              `[nav-feed] lease heartbeat failed for ${name}: ${
                error?.message ?? "lost"
              }`,
            );
          }
        });
      }
    }, LEASE_HEARTBEAT_MS);

    return { leaseBusy: false, value: await operation() };
  } finally {
    if (heartbeatTimer !== undefined) clearInterval(heartbeatTimer);
    if (modeClaimed) await releaseLease(supabase, modeLease, runId);
    await releaseLease(supabase, "nav_writer", runId);
  }
}

function parseFeedBody(rawBody: string): FeedBody {
  try {
    return JSON.parse(rawBody) as FeedBody;
  } catch {
    throw new Error("NAV feed returned invalid JSON");
  }
}

function baseResult(mode: WriteMode, runId: string): RunResult {
  return {
    ok: true,
    mode,
    runId,
    status: "success",
    leaseBusy: false,
    pagesFetched: 0,
    fetchedCount: 0,
    activeCount: 0,
    inactiveCount: 0,
    skippedCount: 0,
    insertedCount: 0,
    updatedCount: 0,
    noOpCount: 0,
    staleIgnoredCount: 0,
    detailFetchedCount: 0,
    detailFailedCount: 0,
    detailQueuedCount: 0,
    lastFeedUrl: null,
    finished: false,
    error: null,
  };
}

function addPageResult(
  result: RunResult,
  mapped: PageEventsResult,
  applied: ApplyStats,
): void {
  result.fetchedCount += mapped.activeCount + mapped.inactiveCount +
    mapped.skippedCount;
  result.activeCount += mapped.activeCount;
  result.inactiveCount += mapped.inactiveCount;
  result.skippedCount += mapped.skippedCount;
  result.insertedCount += applied.inserted;
  result.updatedCount += applied.merged;
  result.noOpCount += applied.noOp;
  result.staleIgnoredCount += applied.staleIgnored;
  result.detailFetchedCount += mapped.detailStats.succeeded;
  result.detailFailedCount += mapped.detailStats.failed;
  result.detailQueuedCount += mapped.detailStats.queued;
}

async function resolveMappedDetails(
  supabase: SupabaseClient,
  mapped: PageEventsResult,
): Promise<void> {
  const now = new Date().toISOString();
  const queued = new Map<string, string>();
  for (const item of mapped.queuedDetails) {
    queued.set(item.externalId, item.error);
  }
  if (queued.size > 0) {
    const { error } = await supabase.from("nav_detail_retry_queue").upsert(
      Array.from(queued, ([externalId, message]) => ({
        external_id: externalId,
        status: "pending",
        next_attempt_at: now,
        last_error: message.slice(0, 1000),
        updated_at: now,
        resolved_at: null,
      })),
      { onConflict: "external_id" },
    );
    if (error) {
      throw new Error(`could not queue NAV detail retries: ${error.message}`);
    }
  }

  const resolved = [...new Set(mapped.resolvedDetailIds)];
  if (resolved.length > 0) {
    const { error } = await supabase.from("nav_detail_retry_queue").upsert(
      resolved.map((externalId) => ({
        external_id: externalId,
        status: "resolved",
        next_attempt_at: now,
        last_error: null,
        updated_at: now,
        resolved_at: now,
      })),
      { onConflict: "external_id" },
    );
    if (error) {
      throw new Error(`could not resolve NAV detail retries: ${error.message}`);
    }
  }
}

async function loadSteadyState(
  supabase: SupabaseClient,
  startFresh: boolean,
): Promise<SyncState> {
  if (startFresh) {
    const { error } = await supabase.from("nav_feed_sync_state").update({
      archived_at: new Date().toISOString(),
      status: "error",
      error: "Archived by explicit steady-state restart",
      finished_at: new Date().toISOString(),
    }).eq("source", SOURCE_NAV).eq("mode", "sync").is("archived_at", null);
    if (error) {
      throw new Error(`could not archive steady state: ${error.message}`);
    }
  } else {
    const { data, error } = await supabase.from("nav_feed_sync_state").select(
      "*",
    )
      .eq("source", SOURCE_NAV).eq("mode", "sync").is("archived_at", null)
      .order("started_at", { ascending: false }).limit(1).maybeSingle();
    if (error) throw new Error(`could not load steady state: ${error.message}`);
    if (data) return data as SyncState;
  }

  const { data, error } = await supabase.from("nav_feed_sync_state").insert({
    source: SOURCE_NAV,
    mode: "sync",
    status: "in_progress",
    feed_url: FEED_LAST_PATH,
    last_next_url: FEED_LAST_PATH,
    heartbeat_at: new Date().toISOString(),
  }).select("*").single();
  if (error || !data) {
    throw new Error(`could not create steady state: ${error?.message}`);
  }
  return data as SyncState;
}

async function updateSyncState(
  supabase: SupabaseClient,
  stateId: string,
  patch: Record<string, unknown>,
): Promise<void> {
  const { error } = await supabase.from("nav_feed_sync_state").update(patch).eq(
    "id",
    stateId,
  );
  if (error) throw new Error(`could not persist NAV cursor: ${error.message}`);
}

async function runSteadySync(
  token: string,
  supabase: SupabaseClient,
  request: RunRequest,
  runId: string,
): Promise<RunResult> {
  const state = await loadSteadyState(supabase, request.startFresh);
  const result = baseResult("sync", runId);
  const detailBudget = { remaining: request.maxDetails };
  let currentToken = token;
  let feedUrl = state.feed_url || state.last_next_url || FEED_LAST_PATH;
  let etag = state.feed_etag;
  let lastModified = state.feed_last_modified;

  for (let page = 0; page < request.maxPages; page += 1) {
    const fetched = await fetchNav(currentToken, feedUrl, {
      etag,
      lastModified,
    });
    currentToken = fetched.token;
    result.lastFeedUrl = fetched.responseUrl;

    if (fetched.response.status === 304) {
      await updateSyncState(supabase, state.id, {
        last_http_status: 304,
        heartbeat_at: new Date().toISOString(),
        tail_reached_at: new Date().toISOString(),
        error: null,
      });
      result.status = "not_modified";
      result.finished = true;
      break;
    }
    if (!fetched.response.ok) {
      throw new Error(`NAV feed HTTP ${fetched.response.status}`);
    }

    const body = parseFeedBody(fetched.rawBody);
    const items = Array.isArray(body.items) ? body.items : [];
    const mapped = await mapPageEvents(currentToken, items, detailBudget);
    currentToken = mapped.token;
    const applied = await applyEvents(supabase, mapped.events, runId, "sync");
    await resolveMappedDetails(supabase, mapped);
    addPageResult(result, mapped, applied);
    result.pagesFetched += 1;

    const nextUrl = typeof body.next_url === "string" && body.next_url.trim()
      ? body.next_url.trim()
      : null;
    const pageFeedUrl =
      typeof body.feed_url === "string" && body.feed_url.trim()
        ? body.feed_url.trim()
        : fetched.responseUrl;
    const pollingUrl = nextUrl || pageFeedUrl;
    result.lastFeedUrl = resolveFeedUrl(pollingUrl);
    await updateSyncState(supabase, state.id, {
      feed_url: pollingUrl,
      last_next_url: pollingUrl,
      feed_etag: nextUrl ? null : fetched.etag,
      feed_last_modified: nextUrl ? null : fetched.lastModified,
      tail_reached_at: nextUrl ? null : new Date().toISOString(),
      last_http_status: fetched.response.status,
      heartbeat_at: new Date().toISOString(),
      pages_fetched: state.pages_fetched + result.pagesFetched,
      total_fetched: state.total_fetched + result.fetchedCount,
      total_imported: state.total_imported + result.insertedCount,
      total_updated: state.total_updated + result.updatedCount,
      total_skipped: state.total_skipped + result.skippedCount,
      error: null,
    });

    feedUrl = pollingUrl;
    etag = nextUrl ? null : fetched.etag;
    lastModified = nextUrl ? null : fetched.lastModified;
    if (!nextUrl) {
      result.finished = true;
      break;
    }
  }

  return result;
}

async function loadBackfillState(supabase: SupabaseClient): Promise<SyncState> {
  const { data, error } = await supabase.from("nav_feed_sync_state").select("*")
    .eq("source", SOURCE_NAV).eq("mode", "backfill").eq("status", "in_progress")
    .order("started_at", { ascending: false }).limit(1).maybeSingle();
  if (error) throw new Error(`could not load backfill state: ${error.message}`);
  if (data) return data as SyncState;

  const inserted = await supabase.from("nav_feed_sync_state").insert({
    source: SOURCE_NAV,
    mode: "backfill",
    status: "in_progress",
    last_next_url: FEED_START_PATH,
  }).select("*").single();
  if (inserted.error || !inserted.data) {
    throw new Error(
      `could not create backfill state: ${inserted.error?.message}`,
    );
  }
  return inserted.data as SyncState;
}

async function runBackfill(
  token: string,
  supabase: SupabaseClient,
  request: RunRequest,
  runId: string,
): Promise<RunResult> {
  const state = await loadBackfillState(supabase);
  const result = baseResult("backfill", runId);
  const detailBudget = { remaining: request.maxDetails };
  let currentToken = token;
  let nextUrl: string | null = state.last_next_url || FEED_START_PATH;

  while (nextUrl && result.pagesFetched < request.maxPages) {
    const fetched = await fetchNav(currentToken, nextUrl);
    currentToken = fetched.token;
    if (!fetched.response.ok) {
      throw new Error(`NAV backfill HTTP ${fetched.response.status}`);
    }
    const body = parseFeedBody(fetched.rawBody);
    const items = Array.isArray(body.items) ? body.items : [];
    const mapped = await mapPageEvents(currentToken, items, detailBudget);
    currentToken = mapped.token;
    const applied = await applyEvents(
      supabase,
      mapped.events,
      runId,
      "backfill",
    );
    await resolveMappedDetails(supabase, mapped);
    addPageResult(result, mapped, applied);
    result.pagesFetched += 1;
    nextUrl = typeof body.next_url === "string" && body.next_url.trim()
      ? body.next_url.trim()
      : null;
    result.lastFeedUrl = nextUrl || fetched.responseUrl;

    await updateSyncState(supabase, state.id, {
      last_next_url: nextUrl,
      pages_fetched: state.pages_fetched + result.pagesFetched,
      total_fetched: state.total_fetched + result.fetchedCount,
      total_imported: state.total_imported + result.insertedCount,
      total_updated: state.total_updated + result.updatedCount,
      total_skipped: state.total_skipped + result.skippedCount,
      heartbeat_at: new Date().toISOString(),
      error: null,
    });
  }

  if (!nextUrl) {
    result.finished = true;
    await updateSyncState(supabase, state.id, {
      status: "completed",
      finished_at: new Date().toISOString(),
      last_next_url: null,
    });
  }
  return result;
}

async function loadOrCreateReconcileRun(
  supabase: SupabaseClient,
  requestedRunId: string | null,
  startFresh: boolean,
): Promise<ReconcileRun> {
  if (requestedRunId) {
    const { data, error } = await supabase.from("nav_reconcile_runs").select(
      "*",
    )
      .eq("run_id", requestedRunId).single();
    if (error || !data) {
      throw new Error(`reconcile run not found: ${error?.message}`);
    }
    return data as ReconcileRun;
  }

  if (!startFresh) {
    const { data, error } = await supabase.from("nav_reconcile_runs").select(
      "*",
    )
      .in("status", ["running", "snapshot_complete", "closing"])
      .order("started_at", { ascending: false }).limit(1).maybeSingle();
    if (error) {
      throw new Error(`could not load reconcile run: ${error.message}`);
    }
    if (data) return data as ReconcileRun;
  }

  const cutoff = new Date();
  const windowStart = new Date(cutoff);
  windowStart.setUTCMonth(windowStart.getUTCMonth() - 6);
  const { data, error } = await supabase.from("nav_reconcile_runs").insert({
    status: "running",
    window_started_at: windowStart.toISOString(),
    cutoff_event_ts: cutoff.toISOString(),
    current_feed_url: FEED_START_PATH,
  }).select("*").single();
  if (error || !data) {
    throw new Error(`could not create reconcile run: ${error?.message}`);
  }
  return data as ReconcileRun;
}

async function runReconcileCloseout(
  supabase: SupabaseClient,
  run: ReconcileRun,
  result: RunResult,
): Promise<RunResult> {
  const { error: backfillError } = await supabase.rpc(
    "nav_backfill_reconcile_source_versions",
    { p_run_id: run.run_id, p_limit: 100 },
  );
  if (backfillError) {
    throw new Error(
      `NAV reconcile source-version backfill failed: ${backfillError.message}`,
    );
  }
  const { data, error } = await supabase.rpc("closeout_nav_reconciliation", {
    p_run_id: run.run_id,
    // Closeout is deliberately incremental. The database function is
    // set-based, while this cap keeps large raw payload batches beneath the
    // database statement timeout.
    p_limit: 100,
  });
  if (error) throw new Error(`NAV reconcile closeout failed: ${error.message}`);
  const row = Array.isArray(data) ? data[0] : data;
  result.updatedCount += Number(row?.closed_count ?? 0);
  result.finished = row?.completed === true;
  result.status = result.finished ? "completed" : "closing";
  return result;
}

async function runReconcile(
  token: string,
  supabase: SupabaseClient,
  request: RunRequest,
  run: ReconcileRun,
): Promise<RunResult> {
  const result = baseResult("reconcile", run.run_id);
  result.status = run.status;
  if (run.status === "snapshot_complete" || run.status === "closing") {
    return await runReconcileCloseout(supabase, run, result);
  }
  if (run.status === "completed") {
    result.finished = true;
    return result;
  }

  let currentToken = token;
  let feedUrl = run.current_feed_url || FEED_START_PATH;
  const detailBudget = { remaining: request.maxDetails };

  while (result.pagesFetched < request.maxPages) {
    const initialPage = run.pages_fetched + result.pagesFetched === 0;
    const fetched = await fetchNav(currentToken, feedUrl, {
      etag: initialPage ? run.feed_etag : null,
      lastModified: initialPage
        ? new Date(run.window_started_at).toUTCString()
        : null,
    });
    currentToken = fetched.token;
    result.lastFeedUrl = fetched.responseUrl;

    if (fetched.response.status === 304) {
      const { error } = await supabase.from("nav_reconcile_runs").update({
        status: "snapshot_complete",
        feed_tail_reached: true,
        last_http_status: 304,
        updated_at: new Date().toISOString(),
      }).eq("run_id", run.run_id);
      if (error) {
        throw new Error(
          `could not complete reconcile snapshot: ${error.message}`,
        );
      }
      run.status = "snapshot_complete";
      run.feed_tail_reached = true;
      break;
    }
    if (!fetched.response.ok) {
      throw new Error(`NAV reconcile HTTP ${fetched.response.status}`);
    }

    const body = parseFeedBody(fetched.rawBody);
    const items = Array.isArray(body.items) ? body.items : [];
    const mapped = await mapPageEvents(currentToken, items, detailBudget);
    currentToken = mapped.token;
    const applied = await applyEvents(
      supabase,
      mapped.events,
      run.run_id,
      "reconcile",
      run.run_id,
    );
    await resolveMappedDetails(supabase, mapped);
    addPageResult(result, mapped, applied);
    result.pagesFetched += 1;

    const nextUrl = typeof body.next_url === "string" && body.next_url.trim()
      ? body.next_url.trim()
      : null;
    const patch: Record<string, unknown> = {
      current_feed_url: nextUrl ||
        (typeof body.feed_url === "string" && body.feed_url.trim()
          ? body.feed_url.trim()
          : fetched.responseUrl),
      feed_etag: nextUrl ? null : fetched.etag,
      feed_last_modified: nextUrl ? null : fetched.lastModified,
      pages_fetched: run.pages_fetched + result.pagesFetched,
      events_seen: run.events_seen + result.fetchedCount,
      active_seen: run.active_seen + result.activeCount,
      inactive_seen: run.inactive_seen + result.inactiveCount,
      detail_success: run.detail_success + result.detailFetchedCount,
      detail_failure: run.detail_failure + result.detailFailedCount,
      last_http_status: fetched.response.status,
      updated_at: new Date().toISOString(),
      error: null,
    };
    if (!nextUrl) {
      patch.status = "snapshot_complete";
      patch.feed_tail_reached = true;
      run.status = "snapshot_complete";
      run.feed_tail_reached = true;
    }
    const { error } = await supabase.from("nav_reconcile_runs").update(patch)
      .eq("run_id", run.run_id);
    if (error) {
      throw new Error(`could not persist reconcile cursor: ${error.message}`);
    }

    feedUrl = nextUrl ||
      (typeof body.feed_url === "string" && body.feed_url.trim()
        ? body.feed_url.trim()
        : fetched.responseUrl);
    if (!nextUrl) break;
  }

  result.status = run.status;
  if (run.status === "snapshot_complete") {
    return await runReconcileCloseout(supabase, run, result);
  }
  return result;
}

type RetryCandidate = {
  external_id: string;
  attempt_count: number;
};

async function loadRetryCandidates(
  supabase: SupabaseClient,
  limit: number,
): Promise<RetryCandidate[]> {
  const { data, error } = await supabase.from("nav_detail_retry_queue")
    .select("external_id, attempt_count")
    .eq("status", "pending")
    .lte("next_attempt_at", new Date().toISOString())
    .order("next_attempt_at", { ascending: true })
    .limit(limit);
  if (error) throw new Error(`could not load detail retries: ${error.message}`);
  if ((data ?? []).length > 0) return data as RetryCandidate[];

  const fallback = await supabase.from("job_opportunities")
    .select("external_id")
    .eq("source", SOURCE_NAV)
    .eq("status", "ACTIVE")
    .is("raw_payload->nav_detail", null)
    .limit(limit);
  if (fallback.error) {
    throw new Error(
      `could not load missing NAV details: ${fallback.error.message}`,
    );
  }
  return (fallback.data ?? []).map((row) => ({
    external_id: String(row.external_id),
    attempt_count: 0,
  }));
}

async function markRetryFailure(
  supabase: SupabaseClient,
  candidate: RetryCandidate,
  message: string,
): Promise<void> {
  const attempts = candidate.attempt_count + 1;
  const abandoned = attempts >= 10;
  const delayMinutes = Math.min(2 ** Math.min(attempts, 8), 360);
  const next = new Date(Date.now() + delayMinutes * 60_000).toISOString();
  const { error } = await supabase.from("nav_detail_retry_queue").upsert({
    external_id: candidate.external_id,
    status: abandoned ? "abandoned" : "pending",
    attempt_count: attempts,
    next_attempt_at: next,
    last_error: message.slice(0, 1000),
    updated_at: new Date().toISOString(),
  }, { onConflict: "external_id" });
  if (error) {
    console.error(`[nav-feed] retry failure update failed: ${error.message}`);
  }
}

async function runDetailRetry(
  token: string,
  supabase: SupabaseClient,
  request: RunRequest,
  runId: string,
): Promise<RunResult> {
  const result = baseResult("enrich_active", runId);
  const candidates = await loadRetryCandidates(supabase, request.maxPages);
  let currentToken = token;

  for (const candidate of candidates) {
    const item: NavFeedItem = {
      id: candidate.external_id,
      url: feedEntryPath(candidate.external_id),
      _feed_entry: { status: "ACTIVE" },
    };
    try {
      const fetched = await fetchDetail(currentToken, item);
      currentToken = fetched.token;
      const event = buildActiveEvent(NAV_FEED_BASE, item, fetched.detail);
      if (!event) throw new Error("could not map detail response");
      const applied = await applyEvents(
        supabase,
        [event],
        runId,
        "enrich_active",
      );
      result.fetchedCount += 1;
      result.activeCount += event.status === "ACTIVE" ? 1 : 0;
      result.inactiveCount += event.status === "INACTIVE" ? 1 : 0;
      result.insertedCount += applied.inserted;
      result.updatedCount += applied.merged;
      result.noOpCount += applied.noOp;
      result.staleIgnoredCount += applied.staleIgnored;
      result.detailFetchedCount += 1;
      await resolveDetailRetry(supabase, candidate.external_id);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      result.detailFailedCount += 1;
      await markRetryFailure(supabase, candidate, message);
    }
  }
  result.finished = candidates.length === 0;
  return result;
}

async function testFeedEntry(
  token: string,
  externalId: string,
): Promise<Response> {
  const result = await fetchNav(token, feedEntryPath(externalId));
  let payload: unknown = result.rawBody;
  try {
    payload = JSON.parse(result.rawBody);
  } catch {
    // Keep the raw body for diagnostics.
  }
  return jsonResponse({
    ok: result.response.ok,
    mode: "test_feedentry",
    externalId,
    httpStatus: result.response.status,
    payload,
  }, result.response.ok ? 200 : result.response.status);
}

async function executeWriteMode(
  token: string,
  supabase: SupabaseClient,
  request: RunRequest,
  logId: string,
): Promise<RunResult> {
  let reconcileRun: ReconcileRun | null = null;
  let operationRunId = logId;
  if (request.mode === "reconcile") {
    reconcileRun = await loadOrCreateReconcileRun(
      supabase,
      request.runId,
      request.startFresh,
    );
    operationRunId = reconcileRun.run_id;
  }

  const leased = await withWriterLeases(
    supabase,
    request.mode,
    operationRunId,
    async () => {
      if (request.mode === "sync") {
        return await runSteadySync(token, supabase, request, operationRunId);
      }
      if (request.mode === "backfill") {
        return await runBackfill(token, supabase, request, operationRunId);
      }
      if (request.mode === "enrich_active") {
        return await runDetailRetry(token, supabase, request, operationRunId);
      }
      return await runReconcile(token, supabase, request, reconcileRun!);
    },
  );

  if (leased.leaseBusy || !leased.value) {
    const result = baseResult(request.mode, operationRunId);
    result.status = "lease_busy";
    result.leaseBusy = true;
    result.finished = false;
    return result;
  }
  return leased.value;
}

async function handle(req: Request): Promise<Response> {
  const parsed = await parseRequest(req);
  const token = await resolveToken();
  if (parsed.mode === "test_feedentry") {
    return await testFeedEntry(token, parsed.feedEntryId);
  }

  const supabase = getSupabase();
  const logId = await startRunLog(supabase, parsed.mode);
  let result: RunResult;
  try {
    result = await executeWriteMode(token, supabase, parsed, logId);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    result = baseResult(parsed.mode, parsed.runId || logId);
    result.ok = false;
    result.status = "failed";
    result.error = message;
    console.error(`[nav-feed] ${parsed.mode} failed: ${message}`);
  }
  await finishRunLog(supabase, logId, result);
  return jsonResponse(result, result.ok ? 200 : 500);
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }
  if (req.method !== "GET" && req.method !== "POST") {
    return jsonResponse(
      { ok: false, error: "Only GET and POST are supported" },
      405,
    );
  }
  const expectedKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")?.trim();
  if (!expectedKey || !hasServiceRole(req, expectedKey)) {
    return jsonResponse({ ok: false, error: "Unauthorized" }, 401);
  }
  try {
    return await handle(req);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error(`[nav-feed] unhandled: ${message}`);
    return jsonResponse({ ok: false, error: message }, 500);
  }
});

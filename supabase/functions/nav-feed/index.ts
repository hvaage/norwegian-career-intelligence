/**
 * NAV pam-stilling-feed — backfill and incremental sync into job_opportunities.
 *
 * POST body:
 *   Import: { "mode": "backfill" | "sync", "maxPages": number, "startFresh": boolean }
 *   Test:   { "mode": "test_feedentry", "feedEntryId": "uuid" }
 *
 * Backfill: paginate from /api/v1/feed via next_url in batches (default maxPages 25).
 * Sync: resume from sync state, else /api/v1/feed?last, else /api/v1/feed.
 */

import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient, type SupabaseClient } from "https://esm.sh/@supabase/supabase-js@2.49.1";

const NAV_PUBLIC_TOKEN_URL = "https://pam-stilling-feed.nav.no/api/publicToken";
const NAV_FEED_BASE = "https://pam-stilling-feed.nav.no";
const FEED_START_PATH = "/api/v1/feed";
const FEED_LAST_PATH = "/api/v1/feed?last";
const SOURCE_NAV = "nav";
const UPSERT_CHUNK = 100;
const DEFAULT_BACKFILL_MAX_PAGES = 25;
const DEFAULT_SYNC_MAX_PAGES = 10;
const DEFAULT_TEST_FEED_ENTRY_ID = "aacf9c18-bcef-48c0-968e-238c5b88eff4";
const MAX_ACTIVE_DETAILS_PER_RUN = 500;

const DATE_FIELD_NAMES = [
  "published",
  "publishedAt",
  "publishedDate",
  "created",
  "createdAt",
  "updated",
  "updatedAt",
  "expires",
  "applicationDue",
  "applicationDeadline",
  "firstPublished",
  "sourceUpdated",
  "sistEndret",
] as const;

const corsHeaders: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

type FeedMode = "backfill" | "sync" | "enrich_active";
type RequestMode = FeedMode | "test_feedentry";

type RunRequest = {
  mode: FeedMode;
  maxPages: number;
  startFresh: boolean;
};

type TestFeedEntryRequest = {
  mode: "test_feedentry";
  feedEntryId: string;
};

type ParsedRequest = RunRequest | TestFeedEntryRequest;

type DateFieldHit = {
  path: string;
  field: string;
  value: string;
};

type SuggestedDateMapping = {
  jobOpportunitiesColumn: string;
  sourcePath: string;
  sampleValue: string;
  note: string;
};

type TestFeedEntryResult = {
  ok: boolean;
  mode: "test_feedentry";
  feedEntryId: string;
  feedEntryUrl: string;
  httpStatus: number;
  rawPreview: Record<string, unknown> | null;
  topLevelKeys: string[];
  dateFieldsFound: DateFieldHit[];
  suggestedMapping: SuggestedDateMapping[] | null;
  conclusion: string;
  error: string | null;
};

type NavFeedEntry = {
  status?: string;
  title?: string;
  businessName?: string;
  municipal?: string;
  sistEndret?: string;
};

type NavAdContent = {
  published?: string;
  expires?: string;
  updated?: string;
  applicationDue?: string;
};

type NavFeedEntryDetail = {
  uuid?: string;
  status?: string;
  sistEndret?: string;
  ad_content?: NavAdContent;
  json?: NavAdContent;
};

type NavFeedItem = {
  id?: string;
  url?: string;
  title?: string;
  date_modified?: string;
  _feed_entry?: NavFeedEntry;
};

type NavRawPayload = NavFeedItem & { nav_detail?: NavFeedEntryDetail };

type ExistingJobOpportunityRow = {
  external_id: string;
  title: string | null;
  company_name: string | null;
  location: string | null;
  url: string | null;
  published_at: string | null;
  expires_at: string | null;
  application_due: string | null;
  raw_payload: NavRawPayload | null;
};

type NavFeedBody = {
  items?: NavFeedItem[];
  next_url?: string | null;
};

type JobOpportunityRow = {
  source: string;
  external_id: string;
  title: string | null;
  company_name: string | null;
  location: string | null;
  status: string | null;
  url: string | null;
  date_modified: string | null;
  published_at: string | null;
  expires_at: string | null;
  application_due: string | null;
  nav_event_modified_at: string | null;
  raw_payload: NavRawPayload;
};

type DetailImportStats = {
  activeDetailFetchedCount: number;
  activeDetailFailedCount: number;
  publishedDateFoundCount: number;
  applicationDueFoundCount: number;
};

type ActiveDetailBudget = {
  remaining: number;
};

type SyncStateRow = {
  id: string;
  source: string;
  mode: string;
  last_next_url: string | null;
  pages_fetched: number;
  total_fetched: number;
  total_imported: number;
  total_updated: number;
  total_skipped: number;
  started_at: string;
  finished_at: string | null;
  status: string;
  error: string | null;
};

type NavImportResult = {
  ok: boolean;
  mode: FeedMode;
  pagesFetched: number;
  fetchedCount: number;
  activeCount: number;
  inactiveCount: number;
  insertedCount: number;
  updatedCount: number;
  skippedCount: number;
  activeDetailFetchedCount: number;
  activeDetailFailedCount: number;
  publishedDateFoundCount: number;
  applicationDueFoundCount: number;
  lastNextUrl: string | null;
  finished: boolean;
  error: string | null;
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

function errorResult(
  mode: FeedMode,
  error: string,
): NavImportResult {
  return {
    ok: false,
    mode,
    pagesFetched: 0,
    fetchedCount: 0,
    activeCount: 0,
    inactiveCount: 0,
    insertedCount: 0,
    updatedCount: 0,
    skippedCount: 0,
    activeDetailFetchedCount: 0,
    activeDetailFailedCount: 0,
    publishedDateFoundCount: 0,
    applicationDueFoundCount: 0,
    lastNextUrl: null,
    finished: false,
    error,
  };
}

function emptyDetailStats(): DetailImportStats {
  return {
    activeDetailFetchedCount: 0,
    activeDetailFailedCount: 0,
    publishedDateFoundCount: 0,
    applicationDueFoundCount: 0,
  };
}

function parseTimestamp(value: string | undefined | null): string | null {
  const raw = value?.trim();
  if (!raw) return null;
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

function parseDateOnly(value: string | undefined | null): string | null {
  const raw = value?.trim();
  if (!raw) return null;
  const datePart = raw.slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(datePart)) return null;
  return datePart;
}

function getAdContent(detail: NavFeedEntryDetail): NavAdContent | undefined {
  return detail.ad_content ?? detail.json;
}

function feedEventModifiedAt(item: NavFeedItem): string | null {
  return parseTimestamp(item._feed_entry?.sistEndret) ??
    parseTimestamp(item.date_modified);
}

function recordDateStats(
  stats: DetailImportStats,
  row: Pick<
    JobOpportunityRow,
    "published_at" | "application_due"
  >,
): void {
  if (row.published_at) stats.publishedDateFoundCount += 1;
  if (row.application_due) stats.applicationDueFoundCount += 1;
}

function extractDatesFromDetail(
  detail: NavFeedEntryDetail,
  item: NavFeedItem,
): Pick<
  JobOpportunityRow,
  | "published_at"
  | "expires_at"
  | "application_due"
  | "nav_event_modified_at"
  | "date_modified"
> {
  const content = getAdContent(detail);
  const published_at = parseTimestamp(content?.published);
  const expires_at = parseTimestamp(content?.expires);
  const application_due = parseDateOnly(content?.applicationDue);
  const nav_event_modified_at = parseTimestamp(content?.updated) ??
    parseTimestamp(detail.sistEndret) ??
    feedEventModifiedAt(item);

  return {
    published_at,
    expires_at,
    application_due,
    nav_event_modified_at,
    date_modified: nav_event_modified_at,
  };
}

function resolveFeedPageUrl(pathOrUrl: string): string {
  const raw = pathOrUrl.trim();
  if (raw.startsWith("http://") || raw.startsWith("https://")) return raw;
  return `${NAV_FEED_BASE}${raw.startsWith("/") ? raw : `/${raw}`}`;
}

function logTokenPrefix(label: string, token: string): void {
  console.log(`[nav-feed] ${label} token prefix (20 chars): ${token.slice(0, 20)}...`);
}

async function fetchFreshPublicToken(): Promise<string> {
  const res = await fetch(NAV_PUBLIC_TOKEN_URL, {
    headers: { Accept: "application/json" },
  });
  const text = await res.text();
  console.log(`[nav-feed] publicToken HTTP ${res.status}`);
  if (!res.ok) {
    console.error(`[nav-feed] publicToken body: ${text.slice(0, 2000)}`);
    throw new Error(
      `Kunne ikke hente public token (${res.status}): ${text.slice(0, 300)}`,
    );
  }
  const eyj = text.match(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
  if (eyj?.[0]) return eyj[0];
  throw new Error("publicToken-respons inneholdt ikke et gyldig JWT");
}

async function resolveToken(): Promise<string> {
  const fromEnv = Deno.env.get("NAV_FEED_TOKEN")?.trim();
  if (fromEnv) {
    logTokenPrefix("NAV_FEED_TOKEN secret", fromEnv);
    console.log("[nav-feed] token source used: secret");
    return fromEnv;
  }
  console.log("[nav-feed] token source used: publicToken");
  const token = await fetchFreshPublicToken();
  logTokenPrefix("publicToken", token);
  return token;
}

function getSupabase(): SupabaseClient {
  const url = Deno.env.get("SUPABASE_URL")?.trim();
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")?.trim();
  if (!url || !key) {
    throw new Error(
      "SUPABASE_URL og SUPABASE_SERVICE_ROLE_KEY må være satt for import",
    );
  }
  return createClient(url, key);
}

function feedEntryPath(id: string): string {
  return `/api/v1/feedentry/${id}`;
}

function isTestFeedEntryRequest(req: ParsedRequest): req is TestFeedEntryRequest {
  return req.mode === "test_feedentry";
}

async function parseRequest(req: Request): Promise<ParsedRequest> {
  if (req.method === "GET") {
    return { mode: "sync", maxPages: DEFAULT_SYNC_MAX_PAGES, startFresh: false };
  }
  try {
    const body = await req.json();
    if (body?.mode === "test_feedentry") {
      const id = typeof body?.feedEntryId === "string" && body.feedEntryId.trim()
        ? body.feedEntryId.trim()
        : DEFAULT_TEST_FEED_ENTRY_ID;
      return { mode: "test_feedentry", feedEntryId: id };
    }
    const requestedMode = body?.mode;
    const mode: FeedMode =
      requestedMode === "sync" || requestedMode === "enrich_active"
        ? (requestedMode as FeedMode)
        : "backfill";
    const maxPages = typeof body?.maxPages === "number" && body.maxPages > 0
      ? Math.floor(body.maxPages)
      : mode === "sync" ? DEFAULT_SYNC_MAX_PAGES : DEFAULT_BACKFILL_MAX_PAGES;
    return {
      mode,
      maxPages,
      startFresh: body?.startFresh === true,
    };
  } catch {
    return {
      mode: "backfill",
      maxPages: DEFAULT_BACKFILL_MAX_PAGES,
      startFresh: false,
    };
  }
}

function collectDateFields(
  value: unknown,
  path = "",
  hits: DateFieldHit[] = [],
): DateFieldHit[] {
  if (value === null || value === undefined) return hits;
  if (Array.isArray(value)) {
    value.forEach((item, i) => collectDateFields(item, `${path}[${i}]`, hits));
    return hits;
  }
  if (typeof value === "object") {
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
      const childPath = path ? `${path}.${key}` : key;
      if (
        (DATE_FIELD_NAMES as readonly string[]).includes(key) &&
        (typeof child === "string" || typeof child === "number")
      ) {
        hits.push({ path: childPath, field: key, value: String(child) });
      }
      collectDateFields(child, childPath, hits);
    }
  }
  return hits;
}

function buildSuggestedMapping(hits: DateFieldHit[]): SuggestedDateMapping[] | null {
  if (hits.length === 0) return null;

  const byField = new Map<string, DateFieldHit>();
  for (const hit of hits) {
    if (!byField.has(hit.field)) byField.set(hit.field, hit);
  }

  const mapping: SuggestedDateMapping[] = [];
  const add = (
    column: string,
    field: string,
    note: string,
    preferPaths: string[] = [],
  ) => {
    let hit = preferPaths
      .map((p) => hits.find((h) => h.path === p || h.path.endsWith(`.${p}`)))
      .find(Boolean);
    if (!hit) hit = byField.get(field);
    if (!hit) return;
    mapping.push({
      jobOpportunitiesColumn: column,
      sourcePath: hit.path,
      sampleValue: hit.value,
      note,
    });
  };

  add("published_at (ny kolonne)", "published", "Publiseringsdato fra annonseinnhold", [
    "ad_content.published",
    "json.published",
  ]);
  add("expires_at (ny kolonne)", "expires", "Utløpsdato for annonsen", [
    "ad_content.expires",
    "json.expires",
  ]);
  add("application_due (ny kolonne)", "applicationDue", "Søknadsfrist", [
    "ad_content.applicationDue",
    "json.applicationDue",
  ]);
  add("date_modified", "updated", "Siste endring i annonseinnhold", [
    "ad_content.updated",
    "json.updated",
  ]);
  add("date_modified", "sistEndret", "Feed-entry sist endret (fallback)", [
    "sistEndret",
  ]);

  return mapping.length > 0 ? mapping : null;
}

function buildTestConclusion(
  hits: DateFieldHit[],
  raw: Record<string, unknown> | null,
): string {
  const status = typeof raw?.status === "string" ? raw.status : null;
  const hasAdContent = raw?.ad_content != null || raw?.json != null;

  if (hits.length === 0) {
    if (status === "INACTIVE" && !hasAdContent) {
      return "Feedentry-detail for INACTIVE stilling returnerer kun uuid, status og sistEndret (innhold maskert). Publiseringsdato finnes ikke i denne responsen. For historiske INACTIVE: bruk feed status, sistEndret og eventuelt item.date_modified fra feed-siden. ACTIVE krever eget kall til feedentry — test med en ACTIVE uuid.";
    }
    return "Ingen kjente datofelter i feedentry-detail. Bruk feed status, sistEndret, item.date_modified fra feed, og vurder annet NAV-endepunkt for publiseringsdato.";
  }

  return "Datofelter funnet i feedentry-detail (typisk under ad_content for ACTIVE). Ikke endre produksjonsimport før mapping er godkjent — feed-siden alene har ikke published/expires/applicationDue.";
}

async function runTestFeedEntry(
  token: string,
  feedEntryId: string,
): Promise<TestFeedEntryResult> {
  const path = feedEntryPath(feedEntryId);
  const pageUrl = resolveFeedPageUrl(path);

  const pageResult = await fetchNavFeedPage(token, pageUrl);
  const { response, rawBody } = pageResult;

  let rawPreview: Record<string, unknown> | null = null;
  if (response.ok) {
    try {
      rawPreview = JSON.parse(rawBody) as Record<string, unknown>;
    } catch {
      return {
        ok: false,
        mode: "test_feedentry",
        feedEntryId,
        feedEntryUrl: pageUrl,
        httpStatus: response.status,
        rawPreview: null,
        topLevelKeys: [],
        dateFieldsFound: [],
        suggestedMapping: null,
        conclusion: "Feedentry-respons var ikke gyldig JSON.",
        error: "Ugyldig JSON fra NAV feedentry",
      };
    }
  }

  const dateFieldsFound = rawPreview ? collectDateFields(rawPreview) : [];
  const suggestedMapping = buildSuggestedMapping(dateFieldsFound);
  const topLevelKeys = rawPreview ? Object.keys(rawPreview) : [];

  return {
    ok: response.ok,
    mode: "test_feedentry",
    feedEntryId,
    feedEntryUrl: pageUrl,
    httpStatus: response.status,
    rawPreview,
    topLevelKeys,
    dateFieldsFound,
    suggestedMapping,
    conclusion: buildTestConclusion(dateFieldsFound, rawPreview),
    error: response.ok ? null : `NAV feedentry returnerte HTTP ${response.status}`,
  };
}

async function fetchNavFeedPage(
  token: string,
  pageUrl: string,
): Promise<{ response: Response; rawBody: string; token: string }> {
  const headers = {
    Accept: "application/json",
    Authorization: `Bearer ${token}`,
  };

  let response = await fetch(pageUrl, { headers });
  console.log(`[nav-feed] NAV response status: ${response.status}`);

  if (response.status === 401 || response.status === 403) {
    const errBody = await response.text();
    console.error(
      `[nav-feed] auth failed (${response.status}), body: ${errBody.slice(0, 2000)}`,
    );
    const fresh = await fetchFreshPublicToken();
    logTokenPrefix("publicToken (retry)", fresh);
    token = fresh;
    response = await fetch(pageUrl, {
      headers: { ...headers, Authorization: `Bearer ${token}` },
    });
    console.log(`[nav-feed] NAV response status (retry): ${response.status}`);
    if (!response.ok) {
      console.error(`[nav-feed] retry body: ${(await response.text()).slice(0, 2000)}`);
    }
  }

  const rawBody = await response.text();
  if (!response.ok) {
    console.error(`[nav-feed] page error body: ${rawBody.slice(0, 2000)}`);
  }

  return { response, rawBody, token };
}

function resolveUrl(itemUrl: string | undefined): string | null {
  const raw = itemUrl?.trim();
  if (!raw) return null;
  if (raw.startsWith("http://") || raw.startsWith("https://")) return raw;
  return `${NAV_FEED_BASE}${raw.startsWith("/") ? raw : `/${raw}`}`;
}

function mapFeedItemBase(item: NavFeedItem): Omit<
  JobOpportunityRow,
  | "published_at"
  | "expires_at"
  | "application_due"
  | "nav_event_modified_at"
  | "date_modified"
  | "raw_payload"
> | null {
  const externalId = item.id?.trim();
  if (!externalId) return null;

  const entry = item._feed_entry;
  const status = entry?.status?.trim() ?? null;
  const title = item.title?.trim() || entry?.title?.trim() || null;

  return {
    source: SOURCE_NAV,
    external_id: externalId,
    title,
    company_name: entry?.businessName?.trim() || null,
    location: entry?.municipal?.trim() || null,
    status,
    url: resolveUrl(item.url),
  };
}

function mapInactiveRow(item: NavFeedItem): JobOpportunityRow | null {
  const base = mapFeedItemBase(item);
  if (!base) return null;

  const nav_event_modified_at = feedEventModifiedAt(item) ??
    new Date().toISOString();

  return {
    ...base,
    published_at: null,
    expires_at: null,
    application_due: null,
    nav_event_modified_at,
    date_modified: nav_event_modified_at,
    raw_payload: item,
  };
}

function mergeInactivePayload(
  existing: NavRawPayload | null | undefined,
  inactiveEvent: unknown,
): NavRawPayload {
  const existingRecord =
    existing && typeof existing === "object" && !Array.isArray(existing)
      ? { ...(existing as Record<string, unknown>) }
      : {};
  const eventRecord =
    inactiveEvent && typeof inactiveEvent === "object" && !Array.isArray(inactiveEvent)
      ? { ...(inactiveEvent as Record<string, unknown>) }
      : {};

  return {
    ...eventRecord,
    ...existingRecord,
    nav_inactive_event: inactiveEvent,
    last_nav_status: "INACTIVE",
  } as NavRawPayload;
}

async function upsertInactiveRows(
  supabase: SupabaseClient,
  items: NavFeedItem[],
): Promise<{ insertedCount: number; updatedCount: number; skippedCount: number }> {
  const inactiveRows = items
    .map(mapInactiveRow)
    .filter((row): row is JobOpportunityRow => row !== null);

  if (inactiveRows.length === 0) {
    return { insertedCount: 0, updatedCount: 0, skippedCount: items.length };
  }

  const dedupMap = new Map<string, JobOpportunityRow>();
  for (const row of inactiveRows) {
    dedupMap.set(row.external_id, row);
  }
  const uniqueRows = Array.from(dedupMap.values());
  const externalIds = uniqueRows.map((row) => row.external_id);
  const existingMap = new Map<string, ExistingJobOpportunityRow>();

  for (const idChunk of chunk(externalIds, 200)) {
    const { data, error } = await supabase
      .from("job_opportunities")
      .select(
        "external_id, title, company_name, location, url, published_at, expires_at, application_due, raw_payload",
      )
      .eq("source", SOURCE_NAV)
      .in("external_id", idChunk);

    if (error) {
      throw new Error(`Kunne ikke slå opp INACTIVE-rader: ${error.message}`);
    }

    for (const row of data ?? []) {
      existingMap.set(String(row.external_id), row as ExistingJobOpportunityRow);
    }
  }

  let insertedCount = 0;
  let updatedCount = 0;
  const rowsToUpsert = uniqueRows.map((row) => {
    const existing = existingMap.get(row.external_id);
    if (existing) updatedCount += 1;
    else insertedCount += 1;

    return {
      ...row,
      title: existing?.title ?? row.title,
      company_name: existing?.company_name ?? row.company_name,
      location: existing?.location ?? row.location,
      url: existing?.url ?? row.url,
      published_at: existing?.published_at ?? row.published_at,
      expires_at: existing?.expires_at ?? row.expires_at,
      application_due: existing?.application_due ?? row.application_due,
      raw_payload: mergeInactivePayload(existing?.raw_payload, row.raw_payload),
    };
  });

  for (const batch of chunk(rowsToUpsert, UPSERT_CHUNK)) {
    const { error } = await supabase
      .from("job_opportunities")
      .upsert(batch, { onConflict: "source,external_id" });
    if (error) {
      throw new Error(`Supabase INACTIVE upsert feilet: ${error.message}`);
    }
  }

  return { insertedCount, updatedCount, skippedCount: items.length - inactiveRows.length };
}

function mapActiveRowWithoutDetail(item: NavFeedItem): JobOpportunityRow | null {
  const base = mapFeedItemBase(item);
  if (!base) return null;

  const nav_event_modified_at = feedEventModifiedAt(item);

  return {
    ...base,
    published_at: null,
    expires_at: null,
    application_due: null,
    nav_event_modified_at,
    date_modified: nav_event_modified_at,
    raw_payload: item,
  };
}

async function fetchFeedEntryDetail(
  token: string,
  itemUrl: string,
): Promise<{ token: string; detail: NavFeedEntryDetail }> {
  const pageUrl = resolveFeedPageUrl(itemUrl);
  const pageResult = await fetchNavFeedPage(token, pageUrl);
  token = pageResult.token;

  if (!pageResult.response.ok) {
    throw new Error(
      `feedentry HTTP ${pageResult.response.status} for ${itemUrl}`,
    );
  }

  let detail: NavFeedEntryDetail;
  try {
    detail = JSON.parse(pageResult.rawBody) as NavFeedEntryDetail;
  } catch {
    throw new Error(`feedentry ugyldig JSON for ${itemUrl}`);
  }

  return { token, detail };
}

async function mapActiveRowWithDetail(
  item: NavFeedItem,
  token: string,
  stats: DetailImportStats,
): Promise<{ token: string; row: JobOpportunityRow | null }> {
  const base = mapFeedItemBase(item);
  if (!base) return { token, row: null };

  const itemUrl = item.url?.trim();
  if (!itemUrl) {
    stats.activeDetailFailedCount += 1;
    return { token, row: mapActiveRowWithoutDetail(item) };
  }

  try {
    const { token: nextToken, detail } = await fetchFeedEntryDetail(token, itemUrl);
    token = nextToken;
    const dates = extractDatesFromDetail(detail, item);
    stats.activeDetailFetchedCount += 1;

    const row: JobOpportunityRow = {
      ...base,
      ...dates,
      raw_payload: { ...item, nav_detail: detail },
    };
    recordDateStats(stats, row);
    return { token, row };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.warn(`[nav-feed] detail fetch failed id=${base.external_id}: ${message}`);
    stats.activeDetailFailedCount += 1;
    return { token, row: mapActiveRowWithoutDetail(item) };
  }
}

async function mapItemsToRows(
  items: NavFeedItem[],
  token: string,
  budget: ActiveDetailBudget,
  stats: DetailImportStats,
  supabase: SupabaseClient,
): Promise<{
  token: string;
  rows: JobOpportunityRow[];
  skipped: number;
  inactiveInserted: number;
  inactiveUpdated: number;
}> {
  const rows: JobOpportunityRow[] = [];
  let skipped = 0;

  const inactive: NavFeedItem[] = [];
  const active: NavFeedItem[] = [];

  for (const item of items) {
    const status = item._feed_entry?.status?.trim();
    if (status === "ACTIVE") {
      active.push(item);
    } else if (status === "INACTIVE" && item.id?.trim()) {
      inactive.push(item);
    }
  }

  const inactiveResult = await upsertInactiveRows(supabase, inactive);
  skipped += inactiveResult.skippedCount;

  // Active rows: always fetch detail. No budget when we already have only ACTIVE in input.
  for (const item of active) {
    if (budget.remaining > 0) {
      const result = await mapActiveRowWithDetail(item, token, stats);
      token = result.token;
      if (result.row) rows.push(result.row);
      else skipped += 1;
      budget.remaining -= 1;
    } else {
      // Out of budget. Skip these (they'll be picked up by enrich_active later).
      skipped += 1;
    }
  }

  return {
    token,
    rows,
    skipped,
    inactiveInserted: inactiveResult.insertedCount,
    inactiveUpdated: inactiveResult.updatedCount,
  };
}

function chunk<T>(arr: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += size) {
    out.push(arr.slice(i, i + size));
  }
  return out;
}

async function upsertJobRows(
  supabase: SupabaseClient,
  rows: JobOpportunityRow[],
): Promise<{ insertedCount: number; updatedCount: number }> {
  if (rows.length === 0) return { insertedCount: 0, updatedCount: 0 };

  // Deduplicate by external_id within this batch. NAV-feeden kan
  // returnere samme uuid på samme side eller på tvers av sider innen
  // én run. ON CONFLICT DO UPDATE krever unike rader per batch.
  const dedupMap = new Map<string, JobOpportunityRow>();
  for (const r of rows) {
    dedupMap.set(r.external_id, r);
  }
  const uniqueRows = Array.from(dedupMap.values());

  const externalIds = uniqueRows.map((r) => r.external_id);
  const existingSet = new Set<string>();

  for (const idChunk of chunk(externalIds, 200)) {
    const { data, error } = await supabase
      .from("job_opportunities")
      .select("external_id")
      .eq("source", SOURCE_NAV)
      .in("external_id", idChunk);
    if (error) {
      throw new Error(`Kunne ikke slå opp eksisterende rader: ${error.message}`);
    }
    for (const row of data ?? []) {
      if (row.external_id) existingSet.add(String(row.external_id));
    }
  }

  let insertedCount = 0;
  let updatedCount = 0;
  for (const r of uniqueRows) {
    if (existingSet.has(r.external_id)) updatedCount += 1;
    else insertedCount += 1;
  }

  for (const batch of chunk(uniqueRows, UPSERT_CHUNK)) {
    const { error } = await supabase
      .from("job_opportunities")
      .upsert(batch, { onConflict: "source,external_id" });
    if (error) {
      throw new Error(`Supabase upsert feilet: ${error.message}`);
    }
  }

  return { insertedCount, updatedCount };
}

async function loadResumeState(
  supabase: SupabaseClient,
  mode: FeedMode,
): Promise<SyncStateRow | null> {
  const { data, error } = await supabase
    .from("nav_feed_sync_state")
    .select("*")
    .eq("source", SOURCE_NAV)
    .eq("mode", mode)
    .eq("status", "in_progress")
    .order("started_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  if (error) {
    throw new Error(`Kunne ikke lese sync-state: ${error.message}`);
  }
  return data as SyncStateRow | null;
}

async function createSyncState(
  supabase: SupabaseClient,
  mode: FeedMode,
): Promise<SyncStateRow> {
  const { data, error } = await supabase
    .from("nav_feed_sync_state")
    .insert({
      source: SOURCE_NAV,
      mode,
      status: "in_progress",
    })
    .select("*")
    .single();

  if (error || !data) {
    throw new Error(`Kunne ikke opprette sync-state: ${error?.message}`);
  }
  return data as SyncStateRow;
}

async function persistSyncState(
  supabase: SupabaseClient,
  stateId: string,
  patch: Partial<SyncStateRow>,
): Promise<void> {
  const { error } = await supabase
    .from("nav_feed_sync_state")
    .update(patch)
    .eq("id", stateId);
  if (error) {
    throw new Error(`Kunne ikke oppdatere sync-state: ${error.message}`);
  }
}

function resolveStartPath(
  mode: FeedMode,
  resume: SyncStateRow | null,
  startFresh: boolean,
): string {
  if (resume && !startFresh && resume.last_next_url?.trim()) {
    return resume.last_next_url.trim();
  }
  if (mode === "sync") {
    return FEED_LAST_PATH;
  }
  return FEED_START_PATH;
}

async function runFeedImport(
  token: string,
  supabase: SupabaseClient,
  run: RunRequest,
): Promise<NavImportResult> {
  const mode = run.mode;
  const maxPages = run.maxPages;

  let syncState: SyncStateRow;
  const resume = run.startFresh ? null : await loadResumeState(supabase, mode);
  if (resume && !run.startFresh) {
    syncState = resume;
    console.log(
      `[nav-feed] resume ${mode} from last_next_url=${syncState.last_next_url ?? FEED_START_PATH}`,
    );
  } else {
    syncState = await createSyncState(supabase, mode);
    console.log(`[nav-feed] new ${mode} run id=${syncState.id}`);
  }

  let currentToken = token;
  let nextPath: string | null = resolveStartPath(mode, resume, run.startFresh);

  let runPagesFetched = 0;
  let runFetched = 0;
  let runInserted = 0;
  let runUpdated = 0;
  let runSkipped = 0;
  let runActive = 0;
  let runInactive = 0;
  let lastNextUrl: string | null = null;
  let finished = false;
  const detailStats = emptyDetailStats();
  const detailBudget: ActiveDetailBudget = {
    remaining: MAX_ACTIVE_DETAILS_PER_RUN,
  };

  const basePages = syncState.pages_fetched ?? 0;
  const baseFetched = syncState.total_fetched ?? 0;
  const baseImported = syncState.total_imported ?? 0;
  const baseUpdated = syncState.total_updated ?? 0;
  const baseSkipped = syncState.total_skipped ?? 0;

  try {
    while (nextPath && runPagesFetched < maxPages) {
      runPagesFetched += 1;
      const pageNum = basePages + runPagesFetched;
      const pageUrl = resolveFeedPageUrl(nextPath);

      const pageResult = await fetchNavFeedPage(currentToken, pageUrl);
      currentToken = pageResult.token;
      const { response, rawBody } = pageResult;

      if (!response.ok) {
        throw new Error(
          `NAV feed returnerte HTTP ${response.status} på side ${pageNum}`,
        );
      }

      let data: NavFeedBody;
      try {
        data = JSON.parse(rawBody) as NavFeedBody;
        console.log("[nav-feed] NAV response parsed as JSON: true");
      } catch {
        console.error(`[nav-feed] invalid JSON page ${pageNum}: ${rawBody.slice(0, 500)}`);
        throw new Error(`NAV feed returnerte ugyldig JSON på side ${pageNum}`);
      }

      const items = Array.isArray(data.items) ? data.items : [];
      let pageActive = 0;
      let pageInactive = 0;

      for (const item of items) {
        const st = item._feed_entry?.status?.trim();
        if (st === "ACTIVE") pageActive += 1;
        else if (st === "INACTIVE") pageInactive += 1;
      }

      const mapped = await mapItemsToRows(
        items,
        currentToken,
        detailBudget,
        detailStats,
        supabase,
      );
      currentToken = mapped.token;
      const {
        rows,
        skipped: pageSkipped,
        inactiveInserted,
        inactiveUpdated,
      } = mapped;

      const { insertedCount, updatedCount } = await upsertJobRows(supabase, rows);

      runFetched += items.length;
      runInserted += insertedCount + inactiveInserted;
      runUpdated += updatedCount + inactiveUpdated;
      runSkipped += pageSkipped;
      runActive += pageActive;
      runInactive += pageInactive;

      const nextUrl = typeof data.next_url === "string" && data.next_url.trim()
        ? data.next_url.trim()
        : null;

      lastNextUrl = nextUrl;

      console.log(
        `[nav-feed] page=${pageNum} items=${items.length} ACTIVE=${pageActive} INACTIVE=${pageInactive} skipped=${pageSkipped} insert=${insertedCount}+inactive:${inactiveInserted} update=${updatedCount}+inactive:${inactiveUpdated} detailFetched=${detailStats.activeDetailFetchedCount} detailBudgetLeft=${detailBudget.remaining} next_url=${nextUrl ? "yes" : "no"}`,
      );

      await persistSyncState(supabase, syncState.id, {
        last_next_url: nextUrl,
        pages_fetched: basePages + runPagesFetched,
        total_fetched: baseFetched + runFetched,
        total_imported: baseImported + runInserted,
        total_updated: baseUpdated + runUpdated,
        total_skipped: baseSkipped + runSkipped,
      });

      if (!nextUrl) {
        finished = true;
        console.log("[nav-feed] stop: no next_url (feed end)");
        break;
      }

      nextPath = nextUrl;
    }

    if (!finished && runPagesFetched >= maxPages) {
      console.log(`[nav-feed] stop: maxPages (${maxPages}) reached`);
    }

    if (finished) {
      await persistSyncState(supabase, syncState.id, {
        status: "completed",
        finished_at: new Date().toISOString(),
        last_next_url: null,
      });
    }

    return {
      ok: true,
      mode,
      pagesFetched: runPagesFetched,
      fetchedCount: runFetched,
      activeCount: runActive,
      inactiveCount: runInactive,
      insertedCount: runInserted,
      updatedCount: runUpdated,
      skippedCount: runSkipped,
      activeDetailFetchedCount: detailStats.activeDetailFetchedCount,
      activeDetailFailedCount: detailStats.activeDetailFailedCount,
      publishedDateFoundCount: detailStats.publishedDateFoundCount,
      applicationDueFoundCount: detailStats.applicationDueFoundCount,
      lastNextUrl: finished ? null : lastNextUrl,
      finished,
      error: null,
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    await persistSyncState(supabase, syncState.id, {
      status: "error",
      error: message,
      finished_at: new Date().toISOString(),
      last_next_url: lastNextUrl,
      pages_fetched: basePages + runPagesFetched,
      total_fetched: baseFetched + runFetched,
      total_imported: baseImported + runInserted,
      total_updated: baseUpdated + runUpdated,
      total_skipped: baseSkipped + runSkipped,
    });
    throw err;
  }
}

type SyncRunLogPatch = {
  status: "running" | "success" | "failed";
  finished_at?: string;
  pages_fetched?: number;
  fetched_count?: number;
  active_count?: number;
  inserted_count?: number;
  updated_count?: number;
  error?: string | null;
  raw_response?: unknown;
};

async function startSyncRunLog(
  supabase: SupabaseClient,
  mode: string,
): Promise<string> {
  const { data, error } = await supabase
    .from("nav_sync_run_log")
    .insert({
      status: "running",
      mode,
    })
    .select("id")
    .single();

  if (error || !data?.id) {
    throw new Error(`Kunne ikke opprette sync-logg: ${error?.message}`);
  }
  return String(data.id);
}

async function completeSyncRunLog(
  supabase: SupabaseClient,
  logId: string,
  patch: SyncRunLogPatch,
): Promise<void> {
  const { error } = await supabase
    .from("nav_sync_run_log")
    .update({
      ...patch,
      finished_at: patch.finished_at ?? new Date().toISOString(),
    })
    .eq("id", logId);

  if (error) {
    console.error(`[nav-feed] sync log update failed: ${error.message}`);
  }
}

function navResultToLogPatch(
  result: NavImportResult,
  status: "success" | "failed",
): SyncRunLogPatch {
  return {
    status,
    pages_fetched: result.pagesFetched,
    fetched_count: result.fetchedCount,
    active_count: result.activeCount,
    inserted_count: result.insertedCount,
    updated_count: result.updatedCount,
    error: result.error,
    raw_response: result,
  };
}
async function runEnrichActive(
  token: string,
  supabase: SupabaseClient,
  batchSize: number,
): Promise<NavImportResult> {
  const detailStats = emptyDetailStats();
  let currentToken = token;
  let processed = 0;
  let updated = 0;
  let skipped = 0;

// Pull ACTIVE rows that are missing nav_detail (server-side filter)
const { data, error } = await supabase
.from("job_opportunities")
.select("external_id, url, raw_payload")
.eq("source", SOURCE_NAV)
.eq("status", "ACTIVE")
.is("raw_payload->nav_detail", null)
.limit(batchSize);
if (error) {
throw new Error(`Kunne ikke hente ACTIVE-rader: ${error.message}`);
}
const missingDetail = data ?? [];
console.log(`[nav-feed] enrich_active: fetched ${missingDetail.length} rows needing detail`);

  for (const row of missingDetail) {
    processed += 1;
    const itemUrl = row.url?.trim();
    const externalId = row.external_id?.trim();
    if (!itemUrl || !externalId) {
      skipped += 1;
      continue;
    }

    try {
      const fetched = await fetchFeedEntryDetail(currentToken, itemUrl);
      currentToken = fetched.token;
      const detail = fetched.detail;

      if (detail.status?.trim() === "INACTIVE") {
        const nav_event_modified_at = parseTimestamp(detail.sistEndret) ??
          new Date().toISOString();
        const payload = mergeInactivePayload(
          row.raw_payload as NavRawPayload | null,
          detail,
        );

        const { error: inactiveErr } = await supabase
          .from("job_opportunities")
          .update({
            status: "INACTIVE",
            nav_event_modified_at,
            date_modified: nav_event_modified_at,
            raw_payload: payload,
          })
          .eq("source", SOURCE_NAV)
          .eq("external_id", externalId);

        if (inactiveErr) {
          console.warn(
            `[nav-feed] enrich inactive update failed ${externalId}: ${inactiveErr.message}`,
          );
          skipped += 1;
          continue;
        }

        detailStats.activeDetailFetchedCount += 1;
        updated += 1;
        continue;
      }

      const payload = row.raw_payload as NavRawPayload;
      const dates = extractDatesFromDetail(detail, payload);
      const updatedPayload: NavRawPayload = { ...payload, nav_detail: detail };

      const { error: updErr } = await supabase
        .from("job_opportunities")
        .update({
          ...dates,
          raw_payload: updatedPayload,
        })
        .eq("source", SOURCE_NAV)
        .eq("external_id", externalId);

      if (updErr) {
        console.warn(`[nav-feed] enrich update failed ${externalId}: ${updErr.message}`);
        skipped += 1;
        continue;
      }

      detailStats.activeDetailFetchedCount += 1;
      updated += 1;
      if (dates.published_at) detailStats.publishedDateFoundCount += 1;
      if (dates.application_due) detailStats.applicationDueFoundCount += 1;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.warn(`[nav-feed] enrich fetch failed ${externalId}: ${message}`);
      detailStats.activeDetailFailedCount += 1;
      skipped += 1;
    }
  }

  return {
    ok: true,
    mode: "enrich_active",
    pagesFetched: 0,
    fetchedCount: processed,
    activeCount: processed,
    inactiveCount: 0,
    insertedCount: 0,
    updatedCount: updated,
    skippedCount: skipped,
    activeDetailFetchedCount: detailStats.activeDetailFetchedCount,
    activeDetailFailedCount: detailStats.activeDetailFailedCount,
    publishedDateFoundCount: detailStats.publishedDateFoundCount,
    applicationDueFoundCount: detailStats.applicationDueFoundCount,
    lastNextUrl: null,
    finished: missingDetail.length === 0,
    error: null,
  };
}
async function runSyncWithLogging(
  token: string,
  supabase: SupabaseClient,
  run: RunRequest,
): Promise<Response> {
  const logId = await startSyncRunLog(supabase, run.mode);

  try {
    const result = await runFeedImport(token, supabase, run);
    await completeSyncRunLog(
      supabase,
      logId,
      navResultToLogPatch(result, result.ok ? "success" : "failed"),
    );
    return jsonResponse(result, result.ok ? 200 : 500);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    const errorPayload = errorResult(run.mode, message);
    await completeSyncRunLog(supabase, logId, {
      status: "failed",
      error: message,
      raw_response: errorPayload,
    });
    return jsonResponse(errorPayload, 500);
  }
}

async function handleNavFeed(req: Request): Promise<Response> {
  console.log("[nav-feed] function started");

  let parsed: ParsedRequest = {
    mode: "backfill",
    maxPages: DEFAULT_BACKFILL_MAX_PAGES,
    startFresh: false,
  };

  try {
    parsed = await parseRequest(req);

    const token = await resolveToken();

    if (isTestFeedEntryRequest(parsed)) {
      console.log(`[nav-feed] test_feedentry id=${parsed.feedEntryId}`);
      const result = await runTestFeedEntry(token, parsed.feedEntryId);
      return jsonResponse(result, result.ok ? 200 : result.httpStatus);
    }

    const run = parsed;
    console.log(
      `[nav-feed] mode=${run.mode} maxPages=${run.maxPages} startFresh=${run.startFresh}`,
    );

    const supabase = getSupabase();
    if (run.mode === "enrich_active") {
      const logId = await startSyncRunLog(supabase, "enrich_active");
      try {
        const result = await runEnrichActive(token, supabase, run.maxPages);
        await completeSyncRunLog(
          supabase,
          logId,
          navResultToLogPatch(result, "success"),
        );
        return jsonResponse(result);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        const errorPayload = errorResult("backfill", message);
        await completeSyncRunLog(supabase, logId, {
          status: "failed",
          error: message,
          raw_response: errorPayload,
        });
        return jsonResponse(errorPayload, 500);
      }
    }
    if (run.mode === "sync") {
      return await runSyncWithLogging(token, supabase, run);
    }

    const result = await runFeedImport(token, supabase, run);
    return jsonResponse(result);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[nav-feed] internal error: ${message}`);
    const mode = parsed.mode === "test_feedentry" ? "backfill" : parsed.mode;
    return jsonResponse(errorResult(mode, message), 500);
  }
}

Deno.serve(async (req: Request) => {
  console.log(`[nav-feed] request ${req.method} ${req.url}`);

  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  if (req.method !== "GET" && req.method !== "POST") {
    return jsonResponse(errorResult("backfill", "Kun GET og POST er støttet"), 405);
  }

  try {
    return await handleNavFeed(req);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[nav-feed] unhandled outer: ${message}`);
    return jsonResponse(errorResult("backfill", message), 500);
  }
});

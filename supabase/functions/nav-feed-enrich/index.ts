/**
 * NAV job_opportunities enrichment — fill date fields on existing ACTIVE rows.
 *
 * POST body: { "maxRows": number }  (default 100)
 */

import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient, type SupabaseClient } from "https://esm.sh/@supabase/supabase-js@2.49.1";

const NAV_PUBLIC_TOKEN_URL = "https://pam-stilling-feed.nav.no/api/publicToken";
const NAV_FEED_BASE = "https://pam-stilling-feed.nav.no";
const SOURCE_NAV = "nav";
const DEFAULT_MAX_ROWS = 100;

const corsHeaders: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
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

type CandidateRow = {
  id: string;
  external_id: string;
  raw_payload: Record<string, unknown> | null;
  published_at: string | null;
  expires_at: string | null;
  application_due: string | null;
  nav_event_modified_at: string | null;
  date_modified: string | null;
};

type EnrichRequest = {
  maxRows: number;
};

type EnrichResult = {
  ok: boolean;
  candidatesFound: number;
  updatedCount: number;
  failedCount: number;
  publishedFoundCount: number;
  applicationDueFoundCount: number;
  remainingWithoutDates: number;
  error: string | null;
};

const CANDIDATE_FILTER = [
  "published_at.is.null",
  "expires_at.is.null",
  "application_due.is.null",
  "nav_event_modified_at.is.null",
].join(",");

function jsonResponse(body: EnrichResult, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

function errorResult(error: string): EnrichResult {
  return {
    ok: false,
    candidatesFound: 0,
    updatedCount: 0,
    failedCount: 0,
    publishedFoundCount: 0,
    applicationDueFoundCount: 0,
    remainingWithoutDates: 0,
    error,
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

function feedEntryPath(externalId: string): string {
  return `/api/v1/feedentry/${externalId}`;
}

function resolveFeedPageUrl(pathOrUrl: string): string {
  const raw = pathOrUrl.trim();
  if (raw.startsWith("http://") || raw.startsWith("https://")) return raw;
  return `${NAV_FEED_BASE}${raw.startsWith("/") ? raw : `/${raw}`}`;
}

async function fetchFreshPublicToken(): Promise<string> {
  const res = await fetch(NAV_PUBLIC_TOKEN_URL, {
    headers: { Accept: "application/json" },
  });
  const text = await res.text();
  if (!res.ok) {
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
  if (fromEnv) return fromEnv;
  return await fetchFreshPublicToken();
}

function getSupabase(): SupabaseClient {
  const url = Deno.env.get("SUPABASE_URL")?.trim();
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")?.trim();
  if (!url || !key) {
    throw new Error(
      "SUPABASE_URL og SUPABASE_SERVICE_ROLE_KEY må være satt",
    );
  }
  return createClient(url, key);
}

async function parseRequest(req: Request): Promise<EnrichRequest> {
  if (req.method === "GET") {
    return { maxRows: DEFAULT_MAX_ROWS };
  }
  try {
    const body = await req.json();
    const maxRows = typeof body?.maxRows === "number" && body.maxRows > 0
      ? Math.floor(body.maxRows)
      : DEFAULT_MAX_ROWS;
    return { maxRows };
  } catch {
    return { maxRows: DEFAULT_MAX_ROWS };
  }
}

async function fetchNavPage(
  token: string,
  pageUrl: string,
): Promise<{ response: Response; rawBody: string; token: string }> {
  const headers = {
    Accept: "application/json",
    Authorization: `Bearer ${token}`,
  };

  let response = await fetch(pageUrl, { headers });

  if (response.status === 401 || response.status === 403) {
    token = await fetchFreshPublicToken();
    response = await fetch(pageUrl, {
      headers: { ...headers, Authorization: `Bearer ${token}` },
    });
  }

  const rawBody = await response.text();
  return { response, rawBody, token };
}

async function fetchFeedEntryDetail(
  token: string,
  externalId: string,
): Promise<{ token: string; detail: NavFeedEntryDetail }> {
  const pageUrl = resolveFeedPageUrl(feedEntryPath(externalId));
  const pageResult = await fetchNavPage(token, pageUrl);
  token = pageResult.token;

  if (!pageResult.response.ok) {
    throw new Error(
      `feedentry HTTP ${pageResult.response.status} for ${externalId}`,
    );
  }

  const detail = JSON.parse(pageResult.rawBody) as NavFeedEntryDetail;
  return { token, detail };
}

function extractDatesFromDetail(detail: NavFeedEntryDetail): {
  published_at: string | null;
  expires_at: string | null;
  application_due: string | null;
  nav_event_modified_at: string | null;
} {
  const content = getAdContent(detail);
  if (!content) {
    const nav_event_modified_at = parseTimestamp(detail.sistEndret);
    return {
      published_at: null,
      expires_at: null,
      application_due: null,
      nav_event_modified_at,
    };
  }

  return {
    published_at: parseTimestamp(content.published),
    expires_at: parseTimestamp(content.expires),
    application_due: parseDateOnly(content.applicationDue),
    nav_event_modified_at: parseTimestamp(content.updated) ??
      parseTimestamp(detail.sistEndret),
  };
}

function mergePayload(
  existing: Record<string, unknown> | null,
  detail: NavFeedEntryDetail,
): Record<string, unknown> {
  const base = existing && typeof existing === "object" ? { ...existing } : {};
  return { ...base, nav_detail: detail };
}

async function countCandidates(supabase: SupabaseClient): Promise<number> {
  const { count, error } = await supabase
    .from("job_opportunities")
    .select("id", { count: "exact", head: true })
    .eq("source", SOURCE_NAV)
    .eq("status", "ACTIVE")
    .or(CANDIDATE_FILTER);

  if (error) {
    throw new Error(`Kunne ikke telle kandidater: ${error.message}`);
  }
  return count ?? 0;
}

async function loadCandidates(
  supabase: SupabaseClient,
  maxRows: number,
): Promise<CandidateRow[]> {
  const { data, error } = await supabase
    .from("job_opportunities")
    .select(
      "id, external_id, raw_payload, published_at, expires_at, application_due, nav_event_modified_at, date_modified",
    )
    .eq("source", SOURCE_NAV)
    .eq("status", "ACTIVE")
    .or(CANDIDATE_FILTER)
    .order("updated_at", { ascending: true })
    .limit(maxRows);

  if (error) {
    throw new Error(`Kunne ikke hente kandidater: ${error.message}`);
  }
  return (data ?? []) as CandidateRow[];
}

async function runEnrichment(
  supabase: SupabaseClient,
  req: EnrichRequest,
): Promise<EnrichResult> {
  const candidatesFound = await countCandidates(supabase);
  const batch = await loadCandidates(supabase, req.maxRows);

  console.log(
    `[nav-feed-enrich] candidates=${candidatesFound} batch=${batch.length} maxRows=${req.maxRows}`,
  );

  if (batch.length === 0) {
    return {
      ok: true,
      candidatesFound,
      updatedCount: 0,
      failedCount: 0,
      publishedFoundCount: 0,
      applicationDueFoundCount: 0,
      remainingWithoutDates: candidatesFound,
      error: null,
    };
  }

  let token = await resolveToken();
  let updatedCount = 0;
  let failedCount = 0;
  let publishedFoundCount = 0;
  let applicationDueFoundCount = 0;

  for (const row of batch) {
    try {
      const { token: nextToken, detail } = await fetchFeedEntryDetail(
        token,
        row.external_id,
      );
      token = nextToken;

      const dates = extractDatesFromDetail(detail);
      const patch: Record<string, unknown> = {
        raw_payload: mergePayload(row.raw_payload, detail),
      };

      if (dates.published_at != null) patch.published_at = dates.published_at;
      if (dates.expires_at != null) patch.expires_at = dates.expires_at;
      if (dates.application_due != null) {
        patch.application_due = dates.application_due;
      }
      if (dates.nav_event_modified_at != null) {
        patch.nav_event_modified_at = dates.nav_event_modified_at;
        patch.date_modified = dates.nav_event_modified_at;
      }

      const { error } = await supabase
        .from("job_opportunities")
        .update(patch)
        .eq("id", row.id);

      if (error) {
        throw new Error(error.message);
      }

      updatedCount += 1;
      if (dates.published_at) publishedFoundCount += 1;
      if (dates.application_due) applicationDueFoundCount += 1;
    } catch (err) {
      failedCount += 1;
      const message = err instanceof Error ? err.message : String(err);
      console.warn(
        `[nav-feed-enrich] failed id=${row.external_id}: ${message}`,
      );
    }
  }

  const remainingWithoutDates = await countCandidates(supabase);

  return {
    ok: true,
    candidatesFound,
    updatedCount,
    failedCount,
    publishedFoundCount,
    applicationDueFoundCount,
    remainingWithoutDates,
    error: null,
  };
}

async function handleEnrich(req: Request): Promise<Response> {
  console.log("[nav-feed-enrich] started");

  try {
    const enrichReq = await parseRequest(req);
    const supabase = getSupabase();
    const result = await runEnrichment(supabase, enrichReq);
    return jsonResponse(result);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[nav-feed-enrich] error: ${message}`);
    return jsonResponse(errorResult(message), 500);
  }
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  if (req.method !== "GET" && req.method !== "POST") {
    return jsonResponse(errorResult("Kun GET og POST er støttet"), 405);
  }

  return await handleEnrich(req);
});

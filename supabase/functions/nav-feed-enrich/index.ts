/**
 * Compatibility endpoint for the old NAV enrichment URL.
 * All writes are delegated to nav-feed so leases and conditional merge apply.
 */

import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { hasServiceRole } from "../_shared/service-auth.ts";

const DEFAULT_MAX_ROWS = 100;

const corsHeaders: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

async function parseMaxRows(req: Request): Promise<number> {
  if (req.method === "GET") return DEFAULT_MAX_ROWS;
  try {
    const body = await req.json();
    const value = body?.maxRows ?? body?.batchSize;
    return typeof value === "number" && Number.isFinite(value) && value > 0
      ? Math.min(Math.floor(value), 500)
      : DEFAULT_MAX_ROWS;
  } catch {
    return DEFAULT_MAX_ROWS;
  }
}

async function proxyEnrichment(req: Request): Promise<Response> {
  const supabaseUrl = Deno.env.get("SUPABASE_URL")?.trim();
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")?.trim();
  if (!supabaseUrl || !serviceKey) {
    return jsonResponse({
      ok: false,
      error: "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required",
    }, 500);
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    apikey: serviceKey,
  };
  if (serviceKey.startsWith("eyJ")) {
    headers.Authorization = `Bearer ${serviceKey}`;
  }

  const response = await fetch(`${supabaseUrl}/functions/v1/nav-feed`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      mode: "enrich_active",
      maxRows: await parseMaxRows(req),
    }),
  });
  return new Response(await response.text(), {
    status: response.status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
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
    return await proxyEnrichment(req);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return jsonResponse({ ok: false, error: message }, 500);
  }
});

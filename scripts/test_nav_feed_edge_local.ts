/**
 * Local smoke test for nav-feed Edge Function logic (same token order as index.ts).
 * Run: deno run --allow-net --allow-env scripts/test_nav_feed_edge_local.ts
 */

const NAV_FEED_URL = "https://pam-stilling-feed.nav.no/api/v1/feed";
const NAV_PUBLIC_TOKEN_URL = "https://pam-stilling-feed.nav.no/api/publicToken";

function logTokenPrefix(label: string, token: string): void {
  console.log(`[nav-feed-test] ${label} token prefix (20 chars): ${token.slice(0, 20)}...`);
}

function extractJwt(text: string): string | null {
  const m = text.match(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
  return m?.[0] ?? null;
}

async function fetchFreshPublicToken(): Promise<string> {
  const res = await fetch(NAV_PUBLIC_TOKEN_URL, {
    headers: { Accept: "application/json" },
  });
  const text = await res.text();
  console.log(`[nav-feed-test] publicToken HTTP ${res.status}`);
  if (!res.ok) {
    console.error(`[nav-feed-test] publicToken body: ${text.slice(0, 2000)}`);
    throw new Error(`Kunne ikke hente public token (${res.status})`);
  }
  const jwt = extractJwt(text);
  if (!jwt) throw new Error("publicToken-respons inneholdt ikke JWT");
  return jwt;
}

async function resolveToken(): Promise<string> {
  const fromEnv = Deno.env.get("NAV_FEED_TOKEN")?.trim();
  if (fromEnv) {
    logTokenPrefix("NAV_FEED_TOKEN", fromEnv);
    return fromEnv;
  }
  const token = await fetchFreshPublicToken();
  logTokenPrefix("publicToken", token);
  return token;
}

async function callFeed(token: string): Promise<Response> {
  return fetch(NAV_FEED_URL, {
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
    },
  });
}

type FeedBody = { items?: Array<{ title?: string; _feed_entry?: { title?: string } }> };

function firstTitle(data: FeedBody): string | null {
  const first = data.items?.[0];
  if (!first) return null;
  return (first.title?.trim() || first._feed_entry?.title?.trim()) ?? null;
}

async function main(): Promise<void> {
  const token = await resolveToken();
  logTokenPrefix("feed request", token);
  let res = await callFeed(token);
  console.log(`[nav-feed-test] feed HTTP ${res.status}`);
  let tokenRefreshed = false;

  if (res.status === 401 || res.status === 403) {
    const errBody = await res.text();
    console.error(`[nav-feed-test] auth failed body: ${errBody.slice(0, 2000)}`);
    const fresh = await fetchFreshPublicToken();
    logTokenPrefix("publicToken (retry)", fresh);
    res = await callFeed(fresh);
    tokenRefreshed = true;
    console.log(`[nav-feed-test] feed retry HTTP ${res.status}`);
  }

  const raw = await res.text();
  if (!res.ok) {
    console.error(`[nav-feed-test] feed error body: ${raw.slice(0, 2000)}`);
    Deno.exit(1);
  }

  const data = JSON.parse(raw) as FeedBody;
  const jobCount = Array.isArray(data.items) ? data.items.length : 0;
  const title = firstTitle(data);

  console.log("\n--- Result (edge-function shape) ---");
  console.log(JSON.stringify({
    ok: true,
    httpStatus: res.status,
    jobCount,
    firstJobTitle: title,
    tokenRefreshed,
  }, null, 2));

  if (res.status !== 200 || jobCount === 0 || !title) {
    Deno.exit(1);
  }
}

main().catch((e) => {
  console.error(e);
  Deno.exit(1);
});

export type NavAdContent = {
  published?: string;
  expires?: string;
  updated?: string;
  applicationDue?: string;
};

export type NavFeedEntryDetail = {
  uuid?: string;
  status?: string;
  sistEndret?: string;
  ad_content?: NavAdContent;
  json?: NavAdContent;
};

export type NavFeedEntry = {
  status?: string;
  title?: string;
  businessName?: string;
  municipal?: string;
  sistEndret?: string;
};

export type NavFeedItem = {
  id?: string;
  url?: string;
  title?: string;
  date_modified?: string;
  _feed_entry?: NavFeedEntry;
};

export type NavRawPayload = NavFeedItem & {
  nav_detail?: NavFeedEntryDetail;
  nav_inactive_event?: Record<string, unknown>;
  last_nav_status?: string;
};

export type NavOpportunityEvent = {
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
  source_event_id: string;
};

export function parseTimestamp(
  value: string | undefined | null,
): string | null {
  const raw = value?.trim();
  if (!raw) return null;
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

export function parseDateOnly(value: string | undefined | null): string | null {
  const raw = value?.trim();
  if (!raw) return null;
  const datePart = raw.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(datePart) ? datePart : null;
}

function maxTimestamp(values: Array<string | null>): string | null {
  let newest: string | null = null;
  for (const value of values) {
    if (value && (!newest || value > newest)) newest = value;
  }
  return newest;
}

export function eventVersion(
  item: NavFeedItem,
  detail?: NavFeedEntryDetail,
): string | null {
  const content = detail?.ad_content ?? detail?.json;
  return maxTimestamp([
    parseTimestamp(item.date_modified),
    parseTimestamp(item._feed_entry?.sistEndret),
    parseTimestamp(detail?.sistEndret),
    parseTimestamp(content?.updated),
  ]);
}

export function sourceEventId(
  externalId: string,
  status: string | null,
  version: string | null,
): string {
  return `${externalId}:${status ?? "UNKNOWN"}:${version ?? "unversioned"}`;
}

function resolveUrl(
  baseUrl: string,
  itemUrl: string | undefined,
): string | null {
  const raw = itemUrl?.trim();
  if (!raw) return null;
  if (raw.startsWith("http://") || raw.startsWith("https://")) return raw;
  return `${baseUrl}${raw.startsWith("/") ? raw : `/${raw}`}`;
}

function baseEvent(
  baseUrl: string,
  item: NavFeedItem,
  detail?: NavFeedEntryDetail,
): NavOpportunityEvent | null {
  const externalId = item.id?.trim() || detail?.uuid?.trim();
  if (!externalId) return null;

  const entry = item._feed_entry;
  const content = detail?.ad_content ?? detail?.json;
  const status = detail?.status?.trim() || entry?.status?.trim() || null;
  const version = eventVersion(item, detail);

  return {
    external_id: externalId,
    title: item.title?.trim() || entry?.title?.trim() || null,
    company_name: entry?.businessName?.trim() || null,
    location: entry?.municipal?.trim() || null,
    status,
    url: resolveUrl(baseUrl, item.url),
    date_modified: version,
    published_at: parseTimestamp(content?.published),
    expires_at: parseTimestamp(content?.expires),
    application_due: parseDateOnly(content?.applicationDue),
    nav_event_modified_at: version,
    raw_payload: detail ? { ...item, nav_detail: detail } : item,
    source_event_id: sourceEventId(externalId, status, version),
  };
}

export function buildActiveEvent(
  baseUrl: string,
  item: NavFeedItem,
  detail?: NavFeedEntryDetail,
): NavOpportunityEvent | null {
  const event = baseEvent(baseUrl, item, detail);
  if (event) {
    event.raw_payload = { ...event.raw_payload, last_nav_status: "ACTIVE" };
  }
  return event;
}

export function buildInactiveEvent(
  baseUrl: string,
  item: NavFeedItem,
  observedAt: string,
): NavOpportunityEvent | null {
  const event = baseEvent(baseUrl, item);
  if (!event) return null;
  const sourceEventAt = eventVersion(item);
  event.status = "INACTIVE";
  event.source_event_id = sourceEventId(
    event.external_id,
    event.status,
    sourceEventAt,
  );
  event.raw_payload = {
    ...item,
    nav_inactive_event: {
      observed_at: observedAt,
      source_event_at: sourceEventAt,
      source_event_at_reliable: sourceEventAt !== null,
      external_id: event.external_id,
    },
    last_nav_status: "INACTIVE",
  };
  return event;
}

export function isActive(item: NavFeedItem): boolean {
  return item._feed_entry?.status?.trim() === "ACTIVE";
}

export function isInactive(item: NavFeedItem): boolean {
  return item._feed_entry?.status?.trim() === "INACTIVE";
}

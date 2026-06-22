import {
  buildActiveEvent,
  buildInactiveEvent,
  eventVersion,
  parseDateOnly,
  parseTimestamp,
  sourceEventId,
} from "./nav-event.ts";

function assertEquals(actual: unknown, expected: unknown): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(
      `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
    );
  }
}

Deno.test("timestamp parser rejects invalid input", () => {
  assertEquals(parseTimestamp("not-a-date"), null);
  assertEquals(parseDateOnly("2026-06-21T10:00:00Z"), "2026-06-21");
});

Deno.test("event version uses newest trustworthy NAV timestamp", () => {
  const version = eventVersion(
    {
      id: "one",
      date_modified: "2026-06-20T10:00:00Z",
      _feed_entry: { sistEndret: "2026-06-20T11:00:00Z" },
    },
    {
      sistEndret: "2026-06-20T12:00:00Z",
      ad_content: { updated: "2026-06-20T13:00:00Z" },
    },
  );
  assertEquals(version, "2026-06-20T13:00:00.000Z");
});

Deno.test("active event maps application due as date", () => {
  const event = buildActiveEvent(
    "https://pam-stilling-feed.nav.no",
    {
      id: "abc",
      url: "/api/v1/feedentry/abc",
      _feed_entry: { status: "ACTIVE", title: "Rådgiver" },
    },
    {
      uuid: "abc",
      status: "ACTIVE",
      ad_content: {
        applicationDue: "2026-07-01T23:59:59+02:00",
        updated: "2026-06-21T10:00:00Z",
      },
    },
  );
  assertEquals(event?.application_due, "2026-07-01");
  assertEquals(
    event?.source_event_id,
    sourceEventId("abc", "ACTIVE", "2026-06-21T10:00:00.000Z"),
  );
});

Deno.test("inactive event keeps reliable source time separate from observation", () => {
  const event = buildInactiveEvent(
    "https://pam-stilling-feed.nav.no",
    {
      id: "abc",
      _feed_entry: { status: "INACTIVE", sistEndret: "2026-06-20T10:00:00Z" },
    },
    "2026-06-21T10:00:00.000Z",
  );
  assertEquals(event?.nav_event_modified_at, "2026-06-20T10:00:00.000Z");
  assertEquals(
    event?.raw_payload.nav_inactive_event?.source_event_at_reliable,
    true,
  );
});

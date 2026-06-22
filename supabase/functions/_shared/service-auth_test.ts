import {
  constantTimeEqual,
  hasServiceRole,
  suppliedServiceKey,
} from "./service-auth.ts";

function assert(value: boolean, message: string): void {
  if (!value) throw new Error(message);
}

Deno.test("constant-time comparison handles equal and unequal lengths", () => {
  assert(constantTimeEqual("secret", "secret"), "equal values must pass");
  assert(!constantTimeEqual("secret", "secrex"), "unequal values must fail");
  assert(
    !constantTimeEqual("secret", "secret-long"),
    "length mismatch must fail",
  );
});

Deno.test("service key can be supplied as apikey", () => {
  const req = new Request("https://example.test", {
    headers: { apikey: "secret" },
  });
  assert(suppliedServiceKey(req) === "secret", "apikey was not extracted");
  assert(hasServiceRole(req, "secret"), "apikey was not authorized");
});

Deno.test("service key can be supplied as bearer", () => {
  const req = new Request("https://example.test", {
    headers: { Authorization: "Bearer secret" },
  });
  assert(hasServiceRole(req, "secret"), "bearer was not authorized");
});

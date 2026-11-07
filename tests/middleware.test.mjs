// Pure helpers of the sign-in gate. Run: node --test tests/middleware.test.mjs
// (needs the bundled copy that tests/test_site.py builds with esbuild).
import { test } from "node:test";
import assert from "node:assert/strict";

const gate = await import(process.env.GATE_BUNDLE);

test("allowed list is trimmed, lower-cased and ignores blanks", () => {
  assert.deepEqual(gate.parseAllowed(" Me@Example.com , ,you@x.org "), ["me@example.com", "you@x.org"]);
  assert.deepEqual(gate.parseAllowed(undefined), []);
});

test("only listed emails pass, case-insensitively", () => {
  const list = gate.parseAllowed("me@example.com");
  assert.equal(gate.isAllowed("ME@example.com", list), true);
  assert.equal(gate.isAllowed("someone@example.com", list), false);
  assert.equal(gate.isAllowed(undefined, list), false);
  assert.equal(gate.isAllowed("me@example.com", []), false);
});

test("email shape check", () => {
  assert.equal(gate.isValidEmail("a@b.co"), true);
  assert.equal(gate.isValidEmail("nope"), false);
});

test("matcher leaves only the login page, its stylesheet and robots.txt open", () => {
  const re = new RegExp("^" + gate.config.matcher[0] + "$");
  for (const open of ["/login.html", "/assets/site.css", "/robots.txt"]) {
    assert.equal(re.test(open), false, open);
  }
  for (const gated of ["/", "/index.html", "/data/overview.json", "/data/m/hrv.json",
                       "/metrics/hrv.html", "/assets/app.js", "/login", "/auth/confirm"]) {
    assert.equal(re.test(gated), true, gated);
  }
});

test("without settings the gate fails closed", async () => {
  delete process.env.SUPABASE_URL;
  const res = await gate.default(new Request("https://h.example/data/overview.json"));
  assert.equal(res.status, 503);
  const page = await gate.default(new Request("https://h.example/index.html"));
  assert.equal(page.status, 503);
});

test("a visitor with no session is sent to the login page; data gets a 401", async () => {
  process.env.SUPABASE_URL = "https://project.supabase.co";
  process.env.SUPABASE_PUBLISHABLE_KEY = "sb_publishable_dummy";
  process.env.ALLOWED_EMAILS = "me@example.com";
  const page = await gate.default(new Request("https://h.example/metrics/hrv.html"));
  assert.equal(page.status, 307);
  assert.equal(page.headers.get("location"), "https://h.example/login.html");
  const data = await gate.default(new Request("https://h.example/data/overview.json"));
  assert.equal(data.status, 401);
});

test("an email not on this site's list never reaches Supabase", async () => {
  const body = new URLSearchParams({ email: "stranger@example.com" });
  const res = await gate.default(new Request("https://h.example/login", { method: "POST", body }));
  assert.equal(res.status, 303);
  assert.equal(res.headers.get("location"), "https://h.example/login.html?sent=1");
  const bad = await gate.default(new Request("https://h.example/login", {
    method: "POST", body: new URLSearchParams({ email: "nope" }) }));
  assert.equal(bad.headers.get("location"), "https://h.example/login.html?error=email");
});

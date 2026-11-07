// The gate in front of the whole site. Vercel runs this before every request,
// static files included, so no page and no data file is served to anyone who
// has not signed in.
//
// Sign-in is an email link through Supabase, the same project and the same
// flow as the car-price site: enter your email, tap the link Supabase sends.
// Supabase only creates accounts for emails on its allow list, and that list
// is shared with car-price, so this file checks a second, narrower list of its
// own (ALLOWED_EMAILS) before letting anyone see health data.
//
// Settings, all in the Vercel project's Environment Variables:
//   SUPABASE_URL              the Supabase project URL (same as car-price)
//   SUPABASE_PUBLISHABLE_KEY  its publishable key (same as car-price)
//   ALLOWED_EMAILS            who may see this site, comma-separated
// If any is missing the gate fails closed: nothing is served.

import { createServerClient } from "@supabase/ssr";
import { next } from "@vercel/functions";

export const config = {
  // Everything except the login page, its stylesheet and the crawler file.
  matcher: ["/((?!login\\.html$|assets/site\\.css$|robots\\.txt$|favicon\\.ico$).*)"],
};

// --- pure helpers (tested in tests/middleware.test.mjs) ----------------------

export function parseAllowed(raw: string | undefined): string[] {
  return (raw ?? "")
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean);
}

export function isAllowed(email: string | undefined | null, allowed: string[]): boolean {
  if (!email) return false;
  return allowed.includes(email.trim().toLowerCase());
}

export function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

/** Login-page paths the gate must never redirect away from. */
export function isLoginPath(pathname: string): boolean {
  return pathname === "/login" || pathname === "/login.html";
}

// --- cookies ----------------------------------------------------------------

type CookieToSet = { name: string; value: string; options?: Record<string, unknown> };

function readCookies(request: Request): { name: string; value: string }[] {
  const header = request.headers.get("cookie") ?? "";
  return header
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const i = part.indexOf("=");
      const name = i < 0 ? part : part.slice(0, i);
      const value = i < 0 ? "" : part.slice(i + 1);
      return { name, value: decodeURIComponent(value) };
    });
}

function serializeCookie({ name, value, options = {} }: CookieToSet): string {
  const o = options as {
    maxAge?: number; expires?: Date; path?: string; domain?: string;
    sameSite?: string | boolean; secure?: boolean; httpOnly?: boolean;
  };
  const parts = [`${name}=${encodeURIComponent(value)}`];
  parts.push(`Path=${o.path ?? "/"}`);
  if (o.maxAge !== undefined) parts.push(`Max-Age=${Math.floor(o.maxAge)}`);
  if (o.expires) parts.push(`Expires=${new Date(o.expires).toUTCString()}`);
  if (o.domain) parts.push(`Domain=${o.domain}`);
  const same = o.sameSite === true ? "Strict" : o.sameSite || "Lax";
  parts.push(`SameSite=${String(same).charAt(0).toUpperCase()}${String(same).slice(1)}`);
  // Always Secure and HttpOnly here: the site is HTTPS-only, and no page
  // script ever needs to read the session.
  parts.push("Secure", "HttpOnly");
  return parts.join("; ");
}

/** A Supabase client for this one request, collecting any cookies it sets. */
function supabaseFor(request: Request, url: string, key: string) {
  const pending: CookieToSet[] = [];
  const client = createServerClient(url, key, {
    cookies: {
      getAll: () => readCookies(request),
      setAll: (toSet: CookieToSet[]) => { pending.push(...toSet); },
    },
  });
  return { client, pending };
}

function redirect(to: URL | string, request: Request, cookies: CookieToSet[] = []): Response {
  const headers = new Headers({ Location: new URL(to, request.url).toString(),
                                "Cache-Control": "private, no-store" });
  cookies.forEach((c) => headers.append("Set-Cookie", serializeCookie(c)));
  // 303 after a form POST so the browser follows with GET; 307 otherwise.
  return new Response(null, { status: request.method === "POST" ? 303 : 307, headers });
}

function notConfigured(): Response {
  return new Response(
    "This site is not configured yet: the Vercel project needs SUPABASE_URL, " +
    "SUPABASE_PUBLISHABLE_KEY and ALLOWED_EMAILS. See the README, 'Deploying'.",
    { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8",
                              "Cache-Control": "no-store" } },
  );
}

// --- the gate ---------------------------------------------------------------

export default async function middleware(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;

  const SUPABASE_URL = process.env.SUPABASE_URL ?? "";
  const SUPABASE_KEY = process.env.SUPABASE_PUBLISHABLE_KEY ?? "";
  const allowed = parseAllowed(process.env.ALLOWED_EMAILS);
  if (!SUPABASE_URL || !SUPABASE_KEY || !allowed.length) {
    return isLoginPath(path) && request.method === "GET" ? next() : notConfigured();
  }

  // 1. The login form posts here. The answer is the same whether or not the
  //    email may sign in, so the page never reveals who is on the list.
  if (path === "/login" && request.method === "POST") {
    const form = await request.formData();
    const email = String(form.get("email") ?? "").trim().toLowerCase();
    if (!isValidEmail(email)) return redirect("/login.html?error=email", request);
    // Refuse emails not on THIS site's list before asking Supabase at all:
    // the shared Supabase list may admit people who should not see this data.
    if (!isAllowed(email, allowed)) return redirect("/login.html?sent=1", request);
    const { client, pending } = supabaseFor(request, SUPABASE_URL, SUPABASE_KEY);
    const { error } = await client.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: `${url.origin}/auth/confirm`, shouldCreateUser: false },
    });
    if (error && error.status === 429) return redirect("/login.html?error=slow", request, pending);
    // The PKCE code verifier rides in `pending`; the link must be opened in
    // this same browser, as with car-price.
    return redirect("/login.html?sent=1", request, pending);
  }
  if (path === "/login") return redirect("/login.html" + url.search, request);

  // 2. The link in the email lands here.
  if (path === "/auth/confirm") {
    const { client, pending } = supabaseFor(request, SUPABASE_URL, SUPABASE_KEY);
    const code = url.searchParams.get("code");
    const tokenHash = url.searchParams.get("token_hash");
    const type = url.searchParams.get("type");
    let ok = false;
    if (code) {
      ok = !(await client.auth.exchangeCodeForSession(code)).error;
    } else if (tokenHash && type) {
      ok = !(await client.auth.verifyOtp({ type: type as "email", token_hash: tokenHash })).error;
    }
    return redirect(ok ? "/" : "/login.html?error=link", request, pending);
  }

  // 3. Sign out.
  if (path === "/auth/signout") {
    const { client, pending } = supabaseFor(request, SUPABASE_URL, SUPABASE_KEY);
    await client.auth.signOut();
    return redirect("/login.html", request, pending);
  }

  if (isLoginPath(path)) return next();

  // 4. Everything else: signed in, and on this site's list.
  const { client, pending } = supabaseFor(request, SUPABASE_URL, SUPABASE_KEY);
  // getClaims checks the token's signature, so a forged cookie fails here,
  // and refreshes an expired session (which is what fills `pending`).
  const { data } = await client.auth.getClaims();
  const claims = data?.claims as { sub?: string; email?: string } | undefined;
  if (!claims?.sub) {
    // Data fetches get a plain 401 rather than an HTML login page in a
    // place where the browser expects JSON.
    if (path.startsWith("/data/")) {
      return new Response("Signed out", { status: 401,
        headers: { "Cache-Control": "no-store" } });
    }
    return redirect("/login.html", request, pending);
  }
  if (!isAllowed(claims.email, allowed)) {
    await client.auth.signOut();
    return redirect("/login.html?error=denied", request, pending);
  }
  // A refreshed session has new cookies to hand back. Vercel does not
  // document setting cookies on a pass-through, so send the browser back to
  // the same address with them set; this happens about once an hour.
  if (pending.length && request.method === "GET") {
    return redirect(url.pathname + url.search, request, pending);
  }
  return next({ headers: { "Cache-Control": "private, no-cache" } });
}

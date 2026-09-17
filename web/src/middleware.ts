import { NextResponse, type NextRequest } from "next/server";
import { createMiddlewareClient } from "@/lib/supabase";
import { isPublicPath } from "@/lib/auth-paths";
import { isAllowedEmail } from "@/lib/allowed-emails";
import { gateDecision } from "@/lib/gate";

export async function middleware(request: NextRequest) {
  const response = NextResponse.next({ request });
  const supabase = createMiddlewareClient(request, response);
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const path = request.nextUrl.pathname;
  const isPublic = isPublicPath(path);
  const allowed = isAllowedEmail(user?.email, process.env.ALLOWED_EMAILS);

  const decision = gateDecision({ hasUser: !!user, allowed, isPublic, path });

  if (decision === "to-login") {
    const to = request.nextUrl.clone();
    to.pathname = "/login";
    to.searchParams.set("next", path);
    return redirectWithCookies(to, response);
  }
  if (decision === "to-home") {
    const to = request.nextUrl.clone();
    to.pathname = "/";
    to.search = "";
    return redirectWithCookies(to, response);
  }
  if (decision === "sign-out") {
    // A misconfigured deploy (say, an unset ALLOWED_EMAILS) must not end
    // the owner's sessions on every other device too — "local" only ends
    // this one, on this browser, which is all sign-out needs here.
    await supabase.auth.signOut({ scope: "local" });
    const to = request.nextUrl.clone();
    to.pathname = "/login";
    to.search = "";
    to.searchParams.set("error", "not-allowed");
    return redirectWithCookies(to, response);
  }
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};

/**
 * `getUser()` and `signOut()` above both write through the Supabase
 * client's `setAll`, which lands on `response` — a rotated access/refresh
 * token (auth-js refreshes inside `getUser()` once under ~90s of expiry)
 * or a cleared session cookie. `response` is not what any of the three
 * branches above return, though: each one needs to redirect, and
 * `NextResponse.redirect(...)` builds a brand-new response that starts
 * with no cookies of its own. This builds that new redirect and copies
 * `response`'s cookies onto it, so a rotated token or a cleared cookie
 * still reaches the browser instead of being silently dropped.
 */
function redirectWithCookies(to: URL, from: NextResponse) {
  const redirect = NextResponse.redirect(to);
  for (const cookie of from.cookies.getAll()) redirect.cookies.set(cookie);
  return redirect;
}

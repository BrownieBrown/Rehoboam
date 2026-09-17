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
    return NextResponse.redirect(to);
  }
  if (decision === "to-home") {
    const to = request.nextUrl.clone();
    to.pathname = "/";
    to.search = "";
    return NextResponse.redirect(to);
  }
  if (decision === "sign-out") {
    // `signOut()` calls the client's `setAll` above, which writes the
    // cleared cookies onto `response`. A redirect built as a fresh
    // `NextResponse.redirect(...)` would carry none of that, so build the
    // redirect from `response` and copy its cookies across explicitly.
    await supabase.auth.signOut();
    const to = request.nextUrl.clone();
    to.pathname = "/login";
    to.search = "";
    to.searchParams.set("error", "not-allowed");
    const redirect = NextResponse.redirect(to);
    for (const cookie of response.cookies.getAll()) redirect.cookies.set(cookie);
    return redirect;
  }
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};

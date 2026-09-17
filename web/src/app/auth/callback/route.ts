import { NextResponse, type NextRequest } from "next/server";
import { createServerClient } from "@/lib/supabase";
import { safeNext } from "@/lib/safe-next";
import { isAllowedEmail } from "@/lib/allowed-emails";

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  const next = safeNext(request.nextUrl.searchParams.get("next"), request.nextUrl.origin);
  if (code) {
    const supabase = await createServerClient();
    const { error, data } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      if (!isAllowedEmail(data.user?.email, process.env.ALLOWED_EMAILS)) {
        // A not-allowed account must never leave this route holding a
        // session, even for the single request that follows. "local"
        // scope is enough — it only needs to end here, not on every other
        // device this account (which isn't the owner) might hold a session
        // on.
        await supabase.auth.signOut({ scope: "local" });
        return NextResponse.redirect(new URL("/login?error=not-allowed", request.nextUrl.origin));
      }
      // `next` is already an absolute, resolved, same-origin URL string
      // (safeNext's whole job), so no second `new URL()` parse here.
      return NextResponse.redirect(next);
    }
  }
  return NextResponse.redirect(new URL("/login?error=link", request.nextUrl.origin));
}

import { NextResponse, type NextRequest } from "next/server";
import { createServerClient } from "@/lib/supabase";
import { safeNext } from "@/lib/safe-next";

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  const next = safeNext(request.nextUrl.searchParams.get("next"), request.nextUrl.origin);
  if (code) {
    const supabase = await createServerClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    // `next` is already an absolute, resolved, same-origin URL string
    // (safeNext's whole job), so no second `new URL()` parse here.
    if (!error) return NextResponse.redirect(next);
  }
  return NextResponse.redirect(new URL("/login?error=link", request.nextUrl.origin));
}

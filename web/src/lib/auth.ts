import "server-only";
import { cache } from "react";
import { redirect } from "next/navigation";
import { createServerClient } from "./supabase";
import { isAllowedEmail } from "./allowed-emails";

/**
 * Every data page calls this before it queries, and so does the `(app)`
 * layout before it reads the shell's facts. The middleware already redirects
 * an anonymous or not-allowed request; this is the second lock, so a request
 * the matcher somehow lets through still gets no data from either.
 *
 * The not-allowed case redirects here too, but cannot sign the user out — a
 * server component cannot clear cookies. That is fine: the middleware signs
 * them out on the very next request, and this component has already refused
 * to return any data on this one.
 *
 * `cache()` makes the layout's call and the page's call, within one request,
 * share a single `getUser()` round trip to Supabase Auth.
 */
export const requireSession = cache(async () => {
  const supabase = await createServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");
  if (!isAllowedEmail(user.email, process.env.ALLOWED_EMAILS)) redirect("/login?error=not-allowed");
  return user;
});

import "server-only";
import { cache } from "react";
import { redirect } from "next/navigation";
import { createServerClient } from "./supabase";

/**
 * Every data page calls this before it queries, and so does the `(app)`
 * layout before it reads the shell's facts. The middleware already redirects
 * an anonymous request; this is the second lock, so a request the matcher
 * somehow lets through still gets no data from either.
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
  return user;
});

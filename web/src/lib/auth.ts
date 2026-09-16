import "server-only";
import { redirect } from "next/navigation";
import { createServerClient } from "./supabase";

/**
 * Every page calls this before it queries. The middleware already redirects an
 * anonymous request, so this is the second lock: a route added later that the
 * matcher somehow misses still cannot reach the store.
 */
export async function requireSession() {
  const supabase = await createServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");
  return user;
}

import { createServerClient as createSSRClient, type CookieOptions } from "@supabase/ssr";
import { cookies } from "next/headers";
import type { NextRequest, NextResponse } from "next/server";

function env() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) throw new Error("NEXT_PUBLIC_SUPABASE_URL / _ANON_KEY are not set");
  return { url, key };
}

/** For server components and route handlers. */
export async function createServerClient() {
  const { url, key } = env();
  const store = await cookies();
  return createSSRClient(url, key, {
    cookies: {
      getAll: () => store.getAll(),
      setAll: (list: { name: string; value: string; options: CookieOptions }[]) => {
        try {
          list.forEach(({ name, value, options }) => store.set(name, value, options));
        } catch {
          // A server component cannot set cookies; the middleware refreshes them.
        }
      },
    },
  });
}

/** For middleware, where cookies ride on the response. */
export function createMiddlewareClient(request: NextRequest, response: NextResponse) {
  const { url, key } = env();
  return createSSRClient(url, key, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (list: { name: string; value: string; options: CookieOptions }[]) =>
        list.forEach(({ name, value, options }) => response.cookies.set(name, value, options)),
    },
  });
}

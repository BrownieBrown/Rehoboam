"use client";

import { useSearchParams } from "next/navigation";
import { loginErrorMessage } from "@/lib/login-error";

/**
 * One plain sentence per known `?error=` value, nothing for any other value
 * — the raw query value is never echoed into the page. The lookup itself
 * lives in `loginErrorMessage` (`lib/login-error.ts`, tested): this
 * component only renders whatever it returns. Kept as its own leaf (and
 * rendered inside a `<Suspense>` boundary by `page.tsx`) specifically so
 * `useSearchParams()` doesn't force the whole `/login` route dynamic: Next
 * statically renders everything above the boundary and renders only this
 * component on the client at request time, so the page stays prerendered
 * static with no data on it.
 */
export function LoginError() {
  const params = useSearchParams();
  const message = loginErrorMessage(params.get("error"));
  if (!message) return null;
  return <p className="mb-3 w-80 text-sm text-negative">{message}</p>;
}

"use client";

import { useSearchParams } from "next/navigation";

/**
 * One plain sentence per known `?error=` value, nothing for any other value
 * — the raw query value is never echoed into the page. Kept as its own leaf
 * (and rendered inside a `<Suspense>` boundary by `page.tsx`) specifically
 * so `useSearchParams()` doesn't force the whole `/login` route dynamic:
 * Next statically renders everything above the boundary and renders only
 * this component on the client at request time, so the page stays
 * prerendered static with no data on it.
 */
const MESSAGES: Record<string, string> = {
  link: "That sign-in link didn't work. Request a new one.",
  "not-allowed": "That account can't use this site.",
};

export function LoginError() {
  const params = useSearchParams();
  const error = params.get("error");
  const message = error ? MESSAGES[error] : undefined;
  if (!message) return null;
  return <p className="mb-3 w-80 text-sm text-negative">{message}</p>;
}

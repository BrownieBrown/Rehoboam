import { Suspense } from "react";
import { LoginError } from "./LoginError";
import { LoginForm } from "./LoginForm";

/**
 * A server component on purpose: the page itself reads no data and needs no
 * client runtime, so it stays prerendered static. The one thing that does
 * need the request's query string — the `?error=` message — is isolated in
 * `LoginError`, a client leaf wrapped in `<Suspense>` so only that leaf
 * renders on the client; the rest of this page still prerenders.
 */
export default function LoginPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-bg">
      <Suspense fallback={null}>
        <LoginError />
      </Suspense>
      <LoginForm />
    </main>
  );
}

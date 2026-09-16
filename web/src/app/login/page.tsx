"use client";

import { useActionState } from "react";
import { sendMagicLink } from "./actions";

export default function LoginPage() {
  const [state, action, pending] = useActionState(sendMagicLink, { message: "" });
  return (
    <main className="flex min-h-screen items-center justify-center bg-bg">
      <form action={action} className="w-80 rounded-lg border border-border bg-surface p-6">
        <h1 className="mb-1 text-lg font-bold text-text">Rehoboam</h1>
        <p className="mb-5 text-sm text-muted">Sign in with a link sent to your inbox.</p>
        <input
          type="email"
          name="email"
          required
          autoComplete="email"
          placeholder="you@example.com"
          className="mb-3 h-10 w-full rounded-md border border-border-strong bg-bg px-3 text-sm text-text outline-none focus:border-accent"
        />
        <button
          type="submit"
          disabled={pending}
          className="h-10 w-full rounded-md bg-accent text-sm font-semibold text-on-accent disabled:opacity-60"
        >
          {pending ? "Sending…" : "Send the link"}
        </button>
        {state.message ? <p className="mt-3 text-sm text-muted">{state.message}</p> : null}
      </form>
    </main>
  );
}

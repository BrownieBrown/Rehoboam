"use client";

import { useActionState } from "react";
import { sendMagicLink, signInWithPassword } from "./actions";

const FIELD =
  "mb-3 h-10 w-full rounded-md border border-border-strong bg-bg px-3 text-sm text-text outline-none focus:border-accent";

/**
 * The interactive part of `/login`: email and password first, and a
 * one-time link to the inbox as the fallback for a forgotten password.
 * Split out of `page.tsx` so the page itself can stay a server component
 * (see `LoginError` for why that matters).
 */
export function LoginForm() {
  const [signIn, signInAction, signingIn] = useActionState(signInWithPassword, { message: "" });
  const [link, linkAction, sending] = useActionState(sendMagicLink, { message: "" });
  return (
    <div className="w-80 rounded-lg border border-border bg-surface p-6">
      <h1 className="mb-1 text-lg font-bold text-text">Rehoboam</h1>
      <p className="mb-5 text-sm text-muted">Sign in with your email and password.</p>
      <form action={signInAction}>
        <input
          type="email"
          name="email"
          required
          autoComplete="username"
          aria-label="Email"
          placeholder="you@example.com"
          className={FIELD}
        />
        <input
          type="password"
          name="password"
          required
          autoComplete="current-password"
          aria-label="Password"
          placeholder="Password"
          className={FIELD}
        />
        <button
          type="submit"
          disabled={signingIn}
          className="h-10 w-full rounded-md bg-accent text-sm font-semibold text-on-accent disabled:opacity-60"
        >
          {signingIn ? "Signing in…" : "Sign in"}
        </button>
        {signIn.message ? (
          <p role="alert" className="mt-3 text-sm text-negative">
            {signIn.message}
          </p>
        ) : null}
      </form>
      <details className="mt-5 border-t border-border pt-4">
        <summary className="cursor-pointer text-sm text-muted">Forgot your password? Get a sign-in link.</summary>
        <form action={linkAction} className="mt-3">
          <input
            type="email"
            name="email"
            required
            autoComplete="email"
            aria-label="Email for the sign-in link"
            placeholder="you@example.com"
            className={FIELD}
          />
          <button
            type="submit"
            disabled={sending}
            className="h-10 w-full rounded-md border border-border-strong text-sm font-semibold text-text disabled:opacity-60"
          >
            {sending ? "Sending…" : "Send the link"}
          </button>
          {link.message ? <p className="mt-3 text-sm text-muted">{link.message}</p> : null}
        </form>
      </details>
    </div>
  );
}

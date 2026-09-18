"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { createServerClient } from "@/lib/supabase";
import { isAllowedEmail } from "@/lib/allowed-emails";
import { passwordFailureMessage } from "@/lib/login-error";

/**
 * The primary way in: the owner's email and password. Only the allow list
 * decides who stays signed in — a correct password for any other account
 * is signed straight back out, exactly as the callback does for a link.
 * `redirect()` throws, so neither call may sit inside a try/catch.
 */
/**
 * What the password form shows after an attempt. `email` is handed back so
 * the form can refill it: React clears a form after every action.
 */
export type SignInState = { message: string; email?: string };

export async function signInWithPassword(_prev: SignInState, form: FormData): Promise<SignInState> {
  const email = String(form.get("email") ?? "").trim();
  // Never trimmed: leading or trailing spaces can be part of a password.
  const password = String(form.get("password") ?? "");
  if (!email || !password) return { message: "Enter your email and password.", email };

  const supabase = await createServerClient();
  const { data, error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) return { message: passwordFailureMessage(error), email };

  if (!isAllowedEmail(data.user?.email, process.env.ALLOWED_EMAILS)) {
    await supabase.auth.signOut({ scope: "local" });
    redirect("/login?error=not-allowed");
  }
  redirect("/");
}

/** The fallback: a one-time link to the inbox, for a forgotten password. */
export async function sendMagicLink(_prev: { message: string }, form: FormData) {
  const email = String(form.get("email") ?? "").trim();
  if (!email) return { message: "Enter the address the account was created with." };

  const supabase = await createServerClient();
  const origin = (await headers()).get("origin") ?? "";
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: `${origin}/auth/callback`, shouldCreateUser: false },
  });
  // The same answer either way: never reveal whether an address has an account.
  return { message: error ? "Check your inbox." : "Check your inbox." };
}

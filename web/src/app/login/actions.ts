"use server";

import { headers } from "next/headers";
import { createServerClient } from "@/lib/supabase";

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

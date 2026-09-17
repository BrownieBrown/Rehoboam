const MESSAGES: Record<string, string> = {
  link: "That sign-in link didn't work. Request a new one.",
  "not-allowed": "That account can't use this site.",
};

/**
 * One plain sentence per known `?error=` value, `null` for anything else —
 * including inherited `Object.prototype` keys. A plain `MESSAGES[value]`
 * lookup also finds those: `MESSAGES["__proto__"]` is `Object.prototype`
 * itself (not a string — React throws trying to render it), and
 * `"constructor"`/`"toString"`/`"hasOwnProperty"`/`"valueOf"` each resolve
 * to a function that renders as an empty paragraph. `Object.hasOwn` checks
 * only `MESSAGES`'s own keys, so none of those ever match.
 */
export function loginErrorMessage(value: string | null): string | null {
  if (value === null) return null;
  return Object.hasOwn(MESSAGES, value) ? MESSAGES[value] : null;
}

/**
 * What a failed password sign-in says. Each sentence must be true in every
 * case that produces it: only Supabase's own `invalid_credentials` code may
 * say the password was wrong, and only a 429 may say to wait. Anything else
 * — an outage, an unconfirmed address, a response without a code — gets a
 * sentence that claims nothing about the credentials.
 */
export function passwordFailureMessage(error: { status?: number; code?: string }): string {
  if (error.code === "invalid_credentials") return "Wrong email or password.";
  if (error.status === 429 || error.code === "over_request_rate_limit") {
    return "Too many attempts. Wait a few minutes and try again.";
  }
  return "Sign-in didn't work. Try again, or use a sign-in link.";
}

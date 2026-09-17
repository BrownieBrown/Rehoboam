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

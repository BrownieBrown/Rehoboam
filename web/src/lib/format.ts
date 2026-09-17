export type Tone = "positive" | "negative" | "neutral";

export const DASH = "—";
const MINUS = "−";

const groups = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

/**
 * Exact euros with thousands separators. Never abbreviated — a standing rule.
 * A negative value (budget is the one that matters: going negative at
 * kickoff zeroes the whole matchday) uses the same U+2212 minus sign as
 * `signed`/`signedPct`, not `Intl`'s default ASCII hyphen-minus — one page,
 * one glyph for "negative".
 */
export function money(n: number | null | undefined): string {
  if (n === null || n === undefined) return DASH;
  if (n < 0) return `${MINUS}${groups.format(-n)}`;
  return groups.format(n);
}

export function num(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined) return DASH;
  return n.toFixed(digits);
}

function tone(n: number): Tone {
  return n > 0 ? "positive" : n < 0 ? "negative" : "neutral";
}

function withSign(n: number, body: string): string {
  if (n > 0) return `+${body}`;
  if (n < 0) return `${MINUS}${body}`;
  return body;
}

export function signed(n: number | null | undefined, digits = 1): { text: string; tone: Tone } {
  if (n === null || n === undefined) return { text: DASH, tone: "neutral" };
  return { text: withSign(n, Math.abs(n).toFixed(digits)), tone: tone(n) };
}

export function signedPct(n: number | null | undefined): { text: string; tone: Tone } {
  if (n === null || n === undefined) return { text: DASH, tone: "neutral" };
  return { text: withSign(n, `${Math.abs(n).toFixed(2)}%`), tone: tone(n) };
}

/** A 0-1 probability as a whole percent, e.g. 0.84 -> "84%"; a dash when unknown. */
export function pct(p: number | null | undefined): string {
  if (p === null || p === undefined) return DASH;
  return `${Math.round(p * 100)}%`;
}

/** Time left on a Kickbase listing. A manager's listing has no expiry. */
export function countdown(epoch: number | null | undefined, now = Date.now() / 1000): string {
  if (epoch === null || epoch === undefined) return DASH;
  const left = epoch - now;
  if (left <= 0) return "expired";
  if (left < 3600) return `${Math.round(left / 60)} min`;
  return `${(left / 3600).toFixed(1)} h`;
}

/** "08:01 UTC · 3 h ago" — the store's clock is UTC, so the page's is too. */
export function ago(epoch: number | null | undefined, now = Date.now() / 1000): string {
  if (epoch === null || epoch === undefined) return DASH;
  const d = new Date(epoch * 1000);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const past = Math.max(0, now - epoch);
  const distance =
    past < 3600 ? `${Math.round(past / 60)} min ago` : `${Math.round(past / 3600)} h ago`;
  return `${hh}:${mm} UTC · ${distance}`;
}

export const POSITION: Record<string, { short: string; token: string }> = {
  Goalkeeper: { short: "GK", token: "gk" },
  Defender: { short: "DEF", token: "def" },
  Midfielder: { short: "MID", token: "mid" },
  Forward: { short: "FW", token: "fw" },
};

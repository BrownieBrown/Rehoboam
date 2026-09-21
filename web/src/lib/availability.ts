import type { Tone } from "./format";

/** Kickbase's `st` code. 0 is fit; `rehoboam/h2h.py` treats 4 and 256 as out
 * and nothing in this codebase has established what 1, 2 and 16 mean, so
 * they are reported as unavailable rather than guessed at. */
export function availability(code: number | null): { label: string; tone: Tone } {
  if (code === null) return { label: "Unknown", tone: "neutral" };
  if (code === 0) return { label: "Fit", tone: "positive" };
  if (code === 4 || code === 256) return { label: "Out", tone: "negative" };
  return { label: "Unavailable", tone: "negative" };
}

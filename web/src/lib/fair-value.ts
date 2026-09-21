import type { Tone } from "@/lib/format";

/**
 * How a player's market value sits against his fair price (migration 022:
 * what the market usually pays, at his position, for a likely starter we
 * expect this many points from).
 *
 * The fit behind `fair_price` is loose on purpose-built data: measured on the
 * live store (2026-09-21, likely starters from 5 m up) the median miss was
 * 34% between 5 and 15 m and 26% above 15 m. So a small difference is
 * noise, not a bargain -- and this is the one place that decides how big a
 * difference has to be before the site calls it one.
 */
/**
 * How far fair price must sit from market value before the site says so.
 * Set at the fit's own typical miss (34% between 5 and 15 m): anything
 * closer is indistinguishable from the line being wrong about him.
 *
 * What the label does NOT mean: a week's backtest (2026-09-15 to 09-21, 195
 * likely starters from 5 m up) found no link between this gap and where his
 * market value went next -- "cheap by 35%+" rose 47% of the time, "expensive
 * by 35%+" 49%. So "cheap" reads "many expected points for the money", never
 * "his price will rise". `Next MV` is the column for that.
 */
export const NOISE_BAND_PCT = 35;

export type FairVerdict = {
  /** Fair price against market value, in percent: +25 means the market asks
   * 25% LESS than his expected points usually cost -- he is cheap. */
  gapPct: number;
  label: "cheap" | "fair" | "expensive";
  tone: Tone;
};

/** `null` when there is no fair price (not a likely starter, under 5 m, or
 * no prediction) or no usable market value -- the caller shows a dash. */
export function fairVerdict(
  fairPrice: number | null | undefined,
  marketValue: number | null | undefined,
): FairVerdict | null {
  if (fairPrice === null || fairPrice === undefined) return null;
  if (marketValue === null || marketValue === undefined || marketValue <= 0) return null;
  const gapPct = ((fairPrice - marketValue) / marketValue) * 100;

  if (gapPct >= NOISE_BAND_PCT) return { gapPct, label: "cheap", tone: "positive" };
  if (gapPct <= -NOISE_BAND_PCT) return { gapPct, label: "expensive", tone: "negative" };
  return { gapPct, label: "fair", tone: "neutral" };
}

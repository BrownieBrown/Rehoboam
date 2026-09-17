import { signedMoney, signedPct, type Tone } from "./format";

/** The header hint on every `Next MV` column. */
export const NEXT_MV_HINT = "Forecast for tonight's ~22:00 market-value update";

/**
 * The two halves of a `Next MV` cell. The tone follows the euro change: a
 * small move can round to 0.00 % and still be a rise or a fall.
 */
export function nextMv(
  pct: number | null,
  change: number | null,
): { pct: string; change: string; tone: Tone } | null {
  if (pct === null || change === null) return null;
  const money = signedMoney(change);
  return { pct: signedPct(pct).text, change: money.text, tone: money.tone };
}

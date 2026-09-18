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

/**
 * Why a page shows no forecast at all. Returned only when not one row on the
 * page carries a live one — the sentence says when forecasts are written, so
 * it is true whether the update has just happened or a data run failed.
 */
export function noForecastNote(rows: { next_mv_pct: number | null }[]): string | null {
  if (rows.length === 0) return null;
  if (rows.some((r) => r.next_mv_pct !== null)) return null;
  return "No market-value forecast is live right now. Each data run (about 07:00 and 19:00 Berlin) writes the forecast for that evening's update, and it stops being shown at 22:00, when Kickbase moves the values.";
}

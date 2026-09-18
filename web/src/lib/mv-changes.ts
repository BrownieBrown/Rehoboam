import type { ChartPoint } from "./chart";

/**
 * Consecutive day-over-day differences of a market-value series, newest
 * first, capped at `limit`. A day where the value did not move is dropped
 * entirely -- the "last changes" list is genuine moves, not a padded
 * calendar -- so the result can hold fewer than `limit` entries even when
 * `points` has plenty of history.
 */
export function changes(
  points: ChartPoint[],
  limit: number,
): { day: string; change: number }[] {
  const out: { day: string; change: number }[] = [];
  for (let i = points.length - 1; i > 0 && out.length < limit; i--) {
    const change = points[i].market_value - points[i - 1].market_value;
    if (change === 0) continue;
    out.push({ day: points[i].day, change });
  }
  return out;
}

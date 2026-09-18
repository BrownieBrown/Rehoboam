/** The market-value chart's range choices, shortest first -- also the exact
 * `?mv=` values the panel's range links read and write. */
export const RANGES = ["7d", "1m", "3m", "6m", "1y"] as const;
export type Range = (typeof RANGES)[number];

const DEFAULT: Range = "3m";

// Maps, not plain objects: a `?mv=__proto__` (or `constructor`, `toString`, …)
// must fall through to the default rather than resolving off Object.prototype.
const DAYS = new Map<Range, number>([
  ["7d", 7],
  ["1m", 30],
  ["3m", 90],
  ["6m", 180],
  ["1y", 365],
]);

const LABELS = new Map<Range, string>([
  ["7d", "7 days"],
  ["1m", "1 month"],
  ["3m", "3 months"],
  ["6m", "6 months"],
  ["1y", "1 year"],
]);

function toRange(raw: string | undefined): Range {
  return (RANGES as readonly string[]).includes(raw ?? "") ? (raw as Range) : DEFAULT;
}

/** The `playerMv` day-count for a raw `?mv=` value, defaulting to 3 months
 * (90 days) for anything unknown or absent. */
export function rangeDays(raw: string | undefined): number {
  return DAYS.get(toRange(raw))!;
}

/** The chart heading / aria-label's words for a raw `?mv=` value. */
export function rangeLabel(raw: string | undefined): string {
  return LABELS.get(toRange(raw))!;
}

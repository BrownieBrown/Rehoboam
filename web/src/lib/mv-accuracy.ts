import { compare } from "./calibration";
import { DASH, num } from "./format";

export type AccuracyInput = {
  scored: number;
  directional: number;
  direction_hits: number;
  mae_pct: number | null;
  baseline_mae_pct: number | null;
};

export type AccuracySummary = {
  updates: number;
  scored: number;
  maePct: number | null;
  baselineMaePct: number | null;
  directionRate: number | null;
};

const round2 = (n: number) => Math.round(n * 100) / 100;

/**
 * One summary over several updates, each weighted by how many forecasts it
 * scored. Rounded to the two decimals the page prints, so the verdict below
 * compares exactly the numbers a reader sees.
 */
export function summarize(rows: AccuracyInput[]): AccuracySummary {
  const used = rows.filter((r) => r.scored > 0 && r.mae_pct !== null && r.baseline_mae_pct !== null);
  const scored = used.reduce((n, r) => n + r.scored, 0);
  if (scored === 0) {
    return { updates: 0, scored: 0, maePct: null, baselineMaePct: null, directionRate: null };
  }
  const mae = used.reduce((n, r) => n + (r.mae_pct as number) * r.scored, 0) / scored;
  const baseline = used.reduce((n, r) => n + (r.baseline_mae_pct as number) * r.scored, 0) / scored;
  const directional = used.reduce((n, r) => n + r.directional, 0);
  const hits = used.reduce((n, r) => n + r.direction_hits, 0);
  return {
    updates: used.length,
    scored,
    maePct: round2(mae),
    baselineMaePct: round2(baseline),
    directionRate: directional > 0 ? round2(hits / directional) : null,
  };
}

/** The section's one sentence. Its verdict is exactly what the two numbers say. */
export function accuracySentence(s: AccuracySummary): string {
  if (s.scored === 0 || s.maePct === null || s.baselineMaePct === null) {
    return "No forecast has been scored yet. Each forecast is scored by the first data run after its update, usually the next morning.";
  }
  const updates = s.updates === 1 ? "the last update" : `the last ${s.updates} updates`;
  const forecasts = s.scored === 1 ? "1 forecast" : `${s.scored} forecasts`;
  const outcome = compare(s.maePct, s.baselineMaePct, "lower");
  const verdict = outcome === "beats" ? "better than" : outcome === "ties" ? "the same as" : "worse than";
  const direction =
    s.directionRate === null
      ? ""
      : ` It called the direction right ${Math.round(s.directionRate * 100)}% of the time.`;
  return `Over ${updates} (${forecasts}), the forecast missed by ${num(s.maePct, 2)} points of percent on average, ${verdict} "no change" at ${num(s.baselineMaePct, 2)}.${direction}`;
}

/** "95% of 200": hits among the forecasts where both the forecast and the update moved. */
export function directionRight(r: { directional: number; direction_hits: number }): string {
  if (r.directional === 0) return DASH;
  return `${Math.round((100 * r.direction_hits) / r.directional)}% of ${r.directional}`;
}

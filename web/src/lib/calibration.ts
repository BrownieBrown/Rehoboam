import { DASH, num } from "./format";

export type Better = "higher" | "lower";
export type Outcome = "beats" | "ties" | "loses" | "unknown";

/** Ours against the baseline. A tie is a tie — never reported as a loss. */
export function compare(ours: number | null, baseline: number | null, better: Better): Outcome {
  if (ours === null || baseline === null) return "unknown";
  if (ours === baseline) return "ties";
  const oursWins = better === "higher" ? ours > baseline : ours < baseline;
  return oursWins ? "beats" : "loses";
}

export const OUTCOME_TEXT: Record<Outcome, string> = {
  beats: "beats the baseline",
  ties: "ties the baseline",
  loses: "loses to the baseline",
  unknown: "not yet reported",
};

/**
 * Bar widths as fractions of the larger magnitude. A negative value (Spearman
 * can fall below zero) draws an EMPTY bar — never a bar as long as the
 * equivalent positive value — and the signed number beside it carries the truth.
 */
export function barWidths(ours: number, baseline: number): { ours: number; baseline: number } {
  const max = Math.max(ours, baseline, 0);
  if (max === 0) return { ours: 0, baseline: 0 };
  return { ours: Math.max(0, ours) / max, baseline: Math.max(0, baseline) / max };
}

export type Gate = {
  spearman_ok: boolean | null;
  regret_ok: boolean | null;
  consecutive_ok: number;
  required: number;
  integrity_clean_days: number;
  required_clean_days: number;
  passes: boolean;
};

/** The gate verdict from `services/calibration.gate_verdict`, as one sentence. Never JSON. */
export function gateSentence(gate: Gate | null): string {
  if (gate === null) return "No gate verdict yet. The first one comes with the first live report.";
  const reports = `${gate.consecutive_ok} of ${gate.required} consecutive reports beat the baseline`;
  const clean = `${num(gate.integrity_clean_days, 1)} of ${gate.required_clean_days} days free of integrity failures`;
  return gate.passes
    ? `Gate passed: ${reports}, and ${clean}. Trading can resume.`
    : `Gate not passed: ${reports}, and ${clean}.`;
}

/**
 * A row with `n === 0` has two distinct causes (`enrichment/calibrate.py`
 * ~:255-305), and only one of them means "the bot wasn't writing predictions
 * yet":
 *
 * - No player anywhere had a prediction before this kickoff — the empty
 *   report is written with `gate = None`. Only true for a **live** row: a
 *   backfill row's own predictions are what it scores, so an empty backfill
 *   never means "the bot wasn't running."
 * - Predictions existed, but every row for this matchday was stale (or there
 *   were no actuals to score against) — `build_report` still returns `n = 0`,
 *   but a live row's gate is computed regardless of `n`, so `gate` is
 *   *non-null* here. Saying "the bot wasn't writing them" would be false, and
 *   a real gate verdict exists to show instead (the caller renders
 *   `gateSentence` alongside this sentence in that case).
 */
export function emptyReportSentence(row: {
  backfill: boolean;
  gate: unknown;
  n_stale_rows: number;
}): string {
  if (!row.backfill && row.gate === null) {
    return "Settled with no predictions — this matchday finished before the bot was writing them.";
  }
  if (row.n_stale_rows > 0) {
    const were = row.n_stale_rows === 1 ? "was" : "were";
    return `No row could be scored — all ${row.n_stale_rows} ${were} stale.`;
  }
  return "No row could be scored.";
}

export { DASH };

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

export { DASH };

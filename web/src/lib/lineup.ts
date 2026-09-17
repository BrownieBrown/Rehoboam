const POSITIONS = ["Goalkeeper", "Defender", "Midfielder", "Forward"] as const;
const LABELS: Record<(typeof POSITIONS)[number], string> = {
  Goalkeeper: "GK",
  Defender: "DEF",
  Midfielder: "MID",
  Forward: "FW",
};

/** The one field `deriveLineup` needs - `SquadRow` (and the test's bare fixtures) both satisfy it. */
export type LineupPlayer = { position: string | null };

export type LineupGroup<T> = { label: string; players: T[] };

export type LineupCheck<T> = {
  groups: LineupGroup<T>[];
  /** The def-mid-fwd counts read straight off the eleven, e.g. "4-3-3". */
  derivedFormation: string;
  /**
   * `in_best_11` is a squad snapshot taken early in a session
   * (`_write_league_predictions`, `rehoboam/auto_trader.py`); `legalFormation`
   * is computed later from a squad re-fetched live (`_set_optimal_lineup`).
   * The league's Top-5 forced sale, or the emergency fill, can run in
   * between, so the two can genuinely disagree - true only when both are
   * known and they differ. A null `legalFormation` (unknown) is never a
   * mismatch.
   */
  mismatch: boolean;
};

/**
 * Groups the eleven by position with no count caps - every flagged player
 * renders, never silently sliced off by the formation string - and reports
 * whether the counts it actually finds disagree with the formation the
 * session submitted. Pure and DOM-free so it can run under vitest's node
 * environment; `Formation.tsx` is the only renderer.
 */
export function deriveLineup<T extends LineupPlayer>(
  eleven: T[],
  legalFormation: string | null,
): LineupCheck<T> {
  const byPosition = (position: string) => eleven.filter((p) => p.position === position);
  const groups = POSITIONS.map((position) => ({
    label: LABELS[position],
    players: byPosition(position),
  }));
  const [, def, mid, fw] = groups;
  const derivedFormation = `${def.players.length}-${mid.players.length}-${fw.players.length}`;
  const mismatch = legalFormation !== null && derivedFormation !== legalFormation;
  return { groups, derivedFormation, mismatch };
}

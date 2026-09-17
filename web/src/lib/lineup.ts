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
   * mismatch. Also true whenever a player couldn't be placed into one of the
   * four known positions (see `groups`' "Other" entry below): the derived
   * D-M-F count can't account for everyone, so it must never be presented as
   * agreeing with `legalFormation`, even if the three numbers happen to
   * match by coincidence.
   */
  mismatch: boolean;
};

/**
 * Groups the eleven by position with no count caps - every flagged player
 * renders, never silently sliced off by the formation string - and reports
 * whether the counts it actually finds disagree with the formation the
 * session submitted. Pure and DOM-free so it can run under vitest's node
 * environment; `Formation.tsx` is the only renderer.
 *
 * The store today only ever writes one of the four exact position strings,
 * so a fifth "Other" group is not reachable with current data - it exists
 * so a future bad row (an unrecognized position, `null`, stray whitespace)
 * still renders instead of silently vanishing, which is exactly the failure
 * class this helper was written to close. `groups` always accounts for
 * every player in `eleven` - "Other" is included only when non-empty.
 */
export function deriveLineup<T extends LineupPlayer>(
  eleven: T[],
  legalFormation: string | null,
): LineupCheck<T> {
  const byPosition = (position: string) => eleven.filter((p) => p.position === position);
  const groups: LineupGroup<T>[] = POSITIONS.map((position) => ({
    label: LABELS[position],
    players: byPosition(position),
  }));

  const other = eleven.filter((p) => !POSITIONS.some((position) => position === p.position));
  if (other.length > 0) groups.push({ label: "Other", players: other });

  const [, def, mid, fw] = groups;
  const derivedFormation = `${def.players.length}-${mid.players.length}-${fw.players.length}`;
  const mismatch =
    legalFormation !== null && (derivedFormation !== legalFormation || other.length > 0);
  return { groups, derivedFormation, mismatch };
}

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
   * True whenever either of `formationDiffers` or `unplaced > 0` holds -
   * kept only so a caller that just wants "is something wrong" doesn't have
   * to combine the two itself. A caller that needs to *say* what's wrong
   * must look at `formationDiffers` and `unplaced` separately (see
   * `lineupNotes`): they have different, mutually exclusive explanations,
   * and only one is ever certainly true at once.
   */
  mismatch: boolean;
  /**
   * Players `deriveLineup` could not place into one of the four known
   * positions (see `groups`' "Other" entry). Their presence means the
   * derived D-M-F count can't account for everyone, so the figure must
   * never be presented as agreeing with `legalFormation` - not even when
   * the three numbers happen to match by coincidence.
   */
  unplaced: number;
  /**
   * True only when both `legalFormation` and the derived D-M-F string are
   * known and they differ. `in_best_11` is a squad snapshot taken early in
   * a session (`_write_league_predictions`, `rehoboam/auto_trader.py`);
   * `legalFormation` is computed later from a squad re-fetched live
   * (`_set_optimal_lineup`). The league's Top-5 forced sale, or the
   * emergency fill, can run in between, so the two can genuinely disagree.
   * A null `legalFormation` (unknown) is never a difference. Meaningless
   * when `unplaced > 0` - the comparison itself isn't trustworthy then, so
   * `lineupNotes` never reads this field in that case.
   */
  formationDiffers: boolean;
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
  const unplaced = other.length;
  const formationDiffers = legalFormation !== null && derivedFormation !== legalFormation;
  const mismatch = formationDiffers || unplaced > 0;
  return { groups, derivedFormation, mismatch, unplaced, formationDiffers };
}

function pluralize<T>(n: number, singular: T, plural: T): T {
  return n === 1 ? singular : plural;
}

/**
 * One human-readable line per *certain* cause of a mismatch - never both at
 * once, because an unplaced player makes the formation comparison itself
 * untrustworthy: `deriveLineup`'s derived string can't account for a player
 * it couldn't place, so explaining a "formation difference" in that case
 * would assert something not actually established. Verified against the
 * bot: `_set_optimal_lineup` re-fetches the squad live and scores any
 * mid-session signing through a fallback, so "worked out at different
 * points in the session" is safe to say without asserting a specific cause
 * (a forced sale, an emergency fill, or something else) that this page
 * cannot itself confirm.
 */
export function lineupNotes(check: LineupCheck<unknown>, legalFormation: string | null): string[] {
  if (check.unplaced > 0) {
    const n = check.unplaced;
    const subject = pluralize(n, "1 player has", `${n} players have`);
    const noun = pluralize(n, "a position", "positions");
    const pronoun = pluralize(n, "It is", "They are");
    const formationPart =
      legalFormation === null
        ? "the submitted formation, which is unknown"
        : `the submitted formation, ${legalFormation}`;
    return [
      `${subject} ${noun} this page doesn't recognise. ${pronoun} listed under Other, so the ` +
        `figure can't be compared with ${formationPart}.`,
    ];
  }
  if (check.formationDiffers && legalFormation !== null) {
    return [
      `The lineup the session submitted used ${legalFormation}. The two are worked out at ` +
        `different points in the session, and the squad can change in between.`,
    ];
  }
  return [];
}

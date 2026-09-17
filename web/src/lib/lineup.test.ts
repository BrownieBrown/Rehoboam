import { describe, expect, it } from "vitest";
import { deriveLineup, lineupNotes } from "./lineup";

/**
 * `in_best_11` is a squad snapshot taken early in a session
 * (`_write_league_predictions`); `legal_formation` is computed later from a
 * squad re-fetched live (`_set_optimal_lineup`). The league's Top-5 forced
 * sale, or the emergency fill, can run in between, so the eleven and the
 * formation string can genuinely disagree - these tests exercise that gap.
 */
function gk(n: number) {
  return Array.from({ length: n }, () => ({ position: "Goalkeeper" }));
}
function def(n: number) {
  return Array.from({ length: n }, () => ({ position: "Defender" }));
}
function mid(n: number) {
  return Array.from({ length: n }, () => ({ position: "Midfielder" }));
}
function fw(n: number) {
  return Array.from({ length: n }, () => ({ position: "Forward" }));
}

/** Total players across every returned group - the "nothing vanished" invariant. */
function rendered(groups: { players: unknown[] }[]) {
  return groups.reduce((n, g) => n + g.players.length, 0);
}

const MATCHING = [...gk(1), ...def(4), ...mid(3), ...fw(3)];
const EXTRA_MID = [...gk(1), ...def(3), ...mid(4), ...fw(3)];
const ONE_STRAY = [...gk(1), ...def(4), ...mid(3), ...fw(2), { position: "Wingback" }];
const TWO_STRAYS = [
  ...gk(1),
  ...def(4),
  ...mid(3),
  ...fw(1),
  { position: "Wingback" },
  { position: "Sweeper" },
];

// Named cases reused by the "nothing vanishes" property test below and
// (where relevant) by their own dedicated behavioral test.
const CASES: [string, { position: string | null }[], string | null][] = [
  ["matching 4-3-3", MATCHING, "4-3-3"],
  ["forced sale: 4 DEF under 3-4-3", MATCHING, "3-4-3"],
  ["emergency fill: extra MID", EXTRA_MID, "4-3-3"],
  ["null formation", MATCHING, null],
  ["empty eleven", [], "4-3-3"],
  ["unknown position", [...gk(1), ...def(4), ...mid(3), ...fw(2), { position: "Wingback" }], "4-3-3"],
  ["null position", [...gk(1), ...def(4), ...mid(3), ...fw(2), { position: null }], "4-3-3"],
  [
    "trailing space in position",
    [...gk(1), ...def(4), ...mid(3), ...fw(2), { position: "Forward " }],
    "4-3-3",
  ],
];

describe("deriveLineup", () => {
  it("reports no mismatch when the eleven matches the submitted formation", () => {
    const { groups, derivedFormation, mismatch } = deriveLineup(MATCHING, "4-3-3");
    expect(derivedFormation).toBe("4-3-3");
    expect(mismatch).toBe(false);
    expect(groups.map((g) => [g.label, g.players.length])).toEqual([
      ["GK", 1],
      ["DEF", 4],
      ["MID", 3],
      ["FW", 3],
    ]);
  });

  it("flags a mismatch when a Top-5 forced sale shrinks the live formation but the stale eleven still shows every player", () => {
    // The eleven still names four defenders (predictions.in_best_11 never
    // refreshed after the sale); the session's submitted lineup went with
    // three. Every player is still present in the figure - nothing is
    // silently dropped - the mismatch is surfaced instead.
    const { derivedFormation, mismatch, groups } = deriveLineup(MATCHING, "3-4-3");
    expect(derivedFormation).toBe("4-3-3");
    expect(mismatch).toBe(true);
    expect(groups[1].players).toHaveLength(4); // all four defenders still render
  });

  it("flags a mismatch when the emergency fill adds a player the formation string does not account for", () => {
    const { derivedFormation, mismatch } = deriveLineup(EXTRA_MID, "4-3-3");
    expect(derivedFormation).toBe("3-4-3");
    expect(mismatch).toBe(true);
  });

  it("treats a null formation as unknown, not as a mismatch", () => {
    const { mismatch } = deriveLineup(MATCHING, null);
    expect(mismatch).toBe(false);
  });

  it("handles an empty eleven without throwing", () => {
    const { groups, derivedFormation, mismatch } = deriveLineup([], "4-3-3");
    expect(groups.every((g) => g.players.length === 0)).toBe(true);
    expect(derivedFormation).toBe("0-0-0");
    expect(mismatch).toBe(true);
  });

  // The class this whole helper exists to close: a player deriveLineup
  // cannot place by exact position string must still render, not vanish.
  describe("a player whose position isn't one of the four exact strings", () => {
    it("groups an unrecognized position under Other and reports a mismatch", () => {
      const eleven = [...gk(1), ...def(4), ...mid(3), ...fw(2), { position: "Wingback" }];
      const { groups, mismatch } = deriveLineup(eleven, "4-3-3");
      const other = groups.find((g) => g.label === "Other");
      expect(other?.players).toEqual([{ position: "Wingback" }]);
      expect(mismatch).toBe(true);
      expect(rendered(groups)).toBe(11);
    });

    it("groups a null position under Other and reports a mismatch", () => {
      const eleven = [...gk(1), ...def(4), ...mid(3), ...fw(2), { position: null }];
      const { groups, mismatch } = deriveLineup(eleven, "4-3-3");
      const other = groups.find((g) => g.label === "Other");
      expect(other?.players).toEqual([{ position: null }]);
      expect(mismatch).toBe(true);
      expect(rendered(groups)).toBe(11);
    });

    it("groups a position with stray whitespace under Other and reports a mismatch", () => {
      const eleven = [...gk(1), ...def(4), ...mid(3), ...fw(2), { position: "Forward " }];
      const { groups, mismatch } = deriveLineup(eleven, "4-3-3");
      const other = groups.find((g) => g.label === "Other");
      expect(other?.players).toEqual([{ position: "Forward " }]);
      // "Forward " is not "Forward" - it must not silently join the FW row,
      // which would both hide the data problem and corrupt the FW count.
      expect(groups.find((g) => g.label === "FW")?.players).toHaveLength(2);
      expect(mismatch).toBe(true);
      expect(rendered(groups)).toBe(11);
    });

    it("never presents Other as agreeing with a known formation, even by coincidence", () => {
      // Same D-M-F counts as a legit 4-3-3 (2 FW + 1 stray "looks like" 3),
      // but the stray can't actually be placed - must still mismatch.
      const eleven = [...gk(1), ...def(4), ...mid(3), ...fw(2), { position: "Wingback" }];
      const { mismatch } = deriveLineup(eleven, "4-3-2");
      expect(mismatch).toBe(true);
    });
  });

  it.each(CASES)("nothing vanishes: %s", (_name, eleven, formation) => {
    const check = deriveLineup(eleven, formation);
    expect(rendered(check.groups)).toBe(eleven.length);
  });
});

/**
 * `deriveLineup`'s `mismatch` has two independent causes - a formation
 * difference, and a player `deriveLineup` couldn't place. The two must
 * never be explained with the same sentence: a formation-difference cause
 * ("worked out at different points in the session") is not true when the
 * real cause is an unplaced player - the comparison itself isn't
 * trustworthy then, so `lineupNotes` must say only the unplaced sentence
 * and never the squad-change one in that case, even if the two D-M-F
 * strings happen to coincide.
 */
describe("lineupNotes", () => {
  it("returns nothing when the eleven matches the submitted formation", () => {
    const check = deriveLineup(MATCHING, "4-3-3");
    expect(lineupNotes(check, "4-3-3")).toEqual([]);
  });

  it("names the submitted formation when it differs and no player is unplaced", () => {
    const check = deriveLineup(MATCHING, "3-4-3");
    const notes = lineupNotes(check, "3-4-3");
    expect(notes).toEqual([
      "The lineup the session submitted used 3-4-3. The two are worked out at different " +
        "points in the session, and the squad can change in between.",
    ]);
  });

  it("gives only the unplaced sentence, singular, for one stray player - never the squad-change sentence", () => {
    const check = deriveLineup(ONE_STRAY, "4-3-3");
    const notes = lineupNotes(check, "4-3-3");
    expect(notes).toHaveLength(1);
    expect(notes[0]).toMatch(/^1 player has/);
    expect(notes[0]).toContain("It is listed under Other");
    expect(notes[0]).toContain("4-3-3");
    expect(notes[0]).not.toContain("squad can change");
  });

  it("uses the plural for two stray players", () => {
    const check = deriveLineup(TWO_STRAYS, "4-3-3");
    const notes = lineupNotes(check, "4-3-3");
    expect(notes).toHaveLength(1);
    expect(notes[0]).toMatch(/^2 players have/);
    expect(notes[0]).toContain("They are listed under Other");
  });

  it("says the formation is unknown when a stray player meets a null formation", () => {
    const check = deriveLineup(ONE_STRAY, null);
    const notes = lineupNotes(check, null);
    expect(notes).toHaveLength(1);
    expect(notes[0]).toContain("unknown");
  });

  it("gives only the unplaced sentence even when the D-M-F counts coincidentally match", () => {
    // Same case as deriveLineup's "never presents Other as agreeing ... by
    // coincidence" test above: 2 FW + 1 stray reads as "3" by count, so
    // formationDiffers is false here - but the stray still makes the
    // comparison untrustworthy, so the squad-change sentence must not appear.
    const check = deriveLineup(ONE_STRAY, "4-3-2");
    expect(check.formationDiffers).toBe(false);
    expect(check.unplaced).toBe(1);
    const notes = lineupNotes(check, "4-3-2");
    expect(notes).toHaveLength(1);
    expect(notes[0]).toMatch(/^1 player has/);
    expect(notes[0]).not.toContain("worked out at different points");
  });
});

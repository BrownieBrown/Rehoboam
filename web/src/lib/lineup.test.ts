import { describe, expect, it } from "vitest";
import { deriveLineup } from "./lineup";

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

describe("deriveLineup", () => {
  it("reports no mismatch when the eleven matches the submitted formation", () => {
    const eleven = [...gk(1), ...def(4), ...mid(3), ...fw(3)];
    const { groups, derivedFormation, mismatch } = deriveLineup(eleven, "4-3-3");
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
    const eleven = [...gk(1), ...def(4), ...mid(3), ...fw(3)];
    const { derivedFormation, mismatch, groups } = deriveLineup(eleven, "3-4-3");
    expect(derivedFormation).toBe("4-3-3");
    expect(mismatch).toBe(true);
    expect(groups[1].players).toHaveLength(4); // all four defenders still render
  });

  it("flags a mismatch when the emergency fill adds a player the formation string does not account for", () => {
    const eleven = [...gk(1), ...def(3), ...mid(4), ...fw(3)];
    const { derivedFormation, mismatch } = deriveLineup(eleven, "4-3-3");
    expect(derivedFormation).toBe("3-4-3");
    expect(mismatch).toBe(true);
  });

  it("treats a null formation as unknown, not as a mismatch", () => {
    const eleven = [...gk(1), ...def(4), ...mid(3), ...fw(3)];
    const { mismatch } = deriveLineup(eleven, null);
    expect(mismatch).toBe(false);
  });

  it("handles an empty eleven without throwing", () => {
    const { groups, derivedFormation, mismatch } = deriveLineup([], "4-3-3");
    expect(groups.every((g) => g.players.length === 0)).toBe(true);
    expect(derivedFormation).toBe("0-0-0");
    expect(mismatch).toBe(true);
  });
});

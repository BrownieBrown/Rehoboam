import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { RULES, integritySentence } from "./integrity";

// web/src/lib -> the repository root -> the bot's rule list.
const INTEGRITY_PY = new URL("../../../rehoboam/services/integrity.py", import.meta.url);

/** Every rule code the bot can raise, read from the Python source itself. */
function pythonRuleCodes(): string[] {
  const source = readFileSync(INTEGRITY_PY, "utf8");
  // `IntegrityFailure(` and its code may sit on different lines.
  const codes = [...source.matchAll(/IntegrityFailure\(\s*"(I\d+)"/g)].map((m) => m[1]);
  return [...new Set(codes)].sort();
}

describe("integritySentence", () => {
  it("turns a rule code into something a person reads", () => {
    expect(integritySentence("I4", "2 owned player(s) without a cost basis")).toBe(
      "Two squad players have no purchase price, so profit and loss cannot be judged for them.",
    );
    expect(integritySentence("I2", "only 10 fieldable after the emergency step")).toBe(
      "The squad cannot field a legal eleven.",
    );
  });

  it("falls back to the raw detail for a rule it does not know", () => {
    expect(integritySentence("I9", "something new")).toBe("I9: something new");
  });

  it("covers exactly the rules the bot can raise, as integrity.py lists them", () => {
    const python = pythonRuleCodes();
    // A regex that stopped matching would make the equality below vacuous.
    expect(python.length).toBeGreaterThanOrEqual(7);
    expect(Object.keys(RULES).sort()).toEqual(python);
  });
});

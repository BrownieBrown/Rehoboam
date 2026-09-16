import { describe, expect, it } from "vitest";
import { RULES, integritySentence } from "./integrity";

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

  it("covers every rule the bot can raise", () => {
    expect(Object.keys(RULES).sort()).toEqual(["I1", "I2", "I3", "I4", "I5", "I6", "I7"]);
  });
});

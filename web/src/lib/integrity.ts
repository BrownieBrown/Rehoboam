/**
 * One sentence per integrity rule the bot can raise. The last test in
 * integrity.test.ts asserts this map matches `rehoboam/services/integrity.py`,
 * so a new rule in the bot fails the site's suite rather than showing a code.
 */
export const RULES: Record<string, (detail: string) => string> = {
  I1: () => "The session could not tell when the next match kicks off.",
  I2: () => "The squad cannot field a legal eleven.",
  I3: () =>
    "The budget cannot cover its open offers even after selling what could be sold, risking a negative balance — and zero points for the matchday — at kickoff.",
  I4: (detail) => {
    const n = Number(detail.match(/^\d+/)?.[0] ?? 0);
    const word = ["No", "One", "Two", "Three", "Four", "Five"][n] ?? String(n);
    const plural = n === 1 ? "player has" : "players have";
    return `${word} squad ${plural} no purchase price, so profit and loss cannot be judged for them.`;
  },
  I5: () => "The session wrote no predictions, so calibration has nothing from it.",
  I6: () => "The lineup was not set although kickoff is close.",
  I7: () => "The ingestion app has not finished a run recently.",
};

export function integritySentence(rule: string, detail: string): string {
  const write = RULES[rule];
  return write ? write(detail) : `${rule}: ${detail}`;
}

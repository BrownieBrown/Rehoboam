import type { PlayerMatch } from "./queries";

export type FormEntry = {
  season: string;
  day_number: number;
  /** Null whenever he was not on the pitch: a stored 0 for an unused
   * substitute is Kickbase's placeholder, not a score. */
  points: number | null;
  role: "started" | "came on" | "did not play";
};

/** The newest `count` matchdays, oldest first, padded with empty entries so
 * the strip keeps its shape. Status is the authority on whether he played:
 * 5 started, 3 came on, everything else did not. */
export function formEntries(matches: PlayerMatch[], count = 5): FormEntry[] {
  const newest = matches.slice(0, count).reverse();
  const entries: FormEntry[] = newest.map((m) => {
    const role = m.status === 5 ? "started" : m.status === 3 ? "came on" : "did not play";
    return {
      season: m.season,
      day_number: m.day_number,
      points: role === "did not play" ? null : m.points,
      role,
    };
  });
  while (entries.length < count) {
    entries.unshift({ season: "", day_number: 0, points: null, role: "did not play" });
  }
  return entries;
}

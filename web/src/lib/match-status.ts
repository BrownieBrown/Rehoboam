/** Kickbase's per-match status, in words. Anything else names the raw number
 * rather than guessing — a status Kickbase adds later must not silently
 * become "not played". */
export function matchStatus(status: number | null): string {
  switch (status) {
    case 5:
      return "started";
    case 3:
      return "came on";
    case 4:
      return "unused sub";
    case 1:
      return "not in squad";
    case 0:
    case null:
      return "not played";
    default:
      return `status ${status}`;
  }
}

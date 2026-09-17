import { num } from "@/lib/format";
import { deriveLineup, lineupNotes } from "@/lib/lineup";
import type { SquadRow } from "@/lib/queries";

/**
 * The predicted best eleven (`predictions.in_best_11`), grouped by position -
 * never truncated by the formation string. `in_best_11` is a squad snapshot
 * taken early in a session, while `legal_formation` is computed later from a
 * squad re-fetched live: the league's Top-5 forced sale, or the emergency
 * fill, can run in between, so the two can genuinely disagree. A row capped
 * to the formation's count would silently drop a real starter with no
 * indication - a silently short figure is worse than an honest one - so this
 * renders every flagged player and, underneath, only what `lineupNotes` can
 * say is certainly true about any disagreement (never both a formation
 * difference and an unplaced-player explanation at once - see `lineup.ts`).
 */
export function Formation({ formation, eleven }: { formation: string | null; eleven: SquadRow[] }) {
  const check = deriveLineup(eleven, formation);
  const notes = lineupNotes(check, formation);

  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-text">Predicted best eleven</h2>
      {check.groups.map((row) => (
        <div key={row.label} className="flex flex-wrap justify-center gap-3">
          {row.players.map((p) => (
            <div
              key={p.player_id}
              className="flex w-40 flex-col gap-1 rounded-lg border border-border bg-surface p-3"
            >
              <span className="text-sm font-semibold text-text">{p.name}</span>
              <span className="text-xs text-muted">{p.team ?? "-"}</span>
              <span className="tnum text-lg font-bold text-text">{num(p.predicted_ep, 0)}</span>
              <span className="tnum text-xs text-muted">
                {p.p_start === null ? "-" : `${Math.round(p.p_start * 100)}% to start`}
              </span>
            </div>
          ))}
        </div>
      ))}
      {notes.map((note, i) => (
        <p key={i} className="text-xs text-muted">
          {note}
        </p>
      ))}
    </div>
  );
}

import { num } from "@/lib/format";
import { deriveLineup } from "@/lib/lineup";
import type { SquadRow } from "@/lib/queries";

/**
 * The predicted best eleven (`predictions.in_best_11`), grouped by position -
 * never truncated by the formation string. `in_best_11` is a squad snapshot
 * taken early in a session, while `legal_formation` is computed later from a
 * squad re-fetched live: the league's Top-5 forced sale, or the emergency
 * fill, can run in between, so the two can genuinely disagree. A row capped
 * to the formation's count would silently drop a real starter with no
 * indication - a silently short figure is worse than an honest one - so this
 * renders every flagged player and, when `deriveLineup` finds a mismatch,
 * says so underneath instead of hiding it.
 */
export function Formation({ formation, eleven }: { formation: string | null; eleven: SquadRow[] }) {
  const { groups, mismatch } = deriveLineup(eleven, formation);

  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-text">Predicted best eleven</h2>
      {groups.map((row) => (
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
      {mismatch ? (
        <p className="text-xs text-muted">
          The lineup the session submitted used {formation}. It differs from this predicted eleven
          because the squad changed during the session.
        </p>
      ) : null}
    </div>
  );
}

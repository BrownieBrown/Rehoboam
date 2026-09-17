import { num } from "@/lib/format";
import type { SquadRow } from "@/lib/queries";

/**
 * The eleven the session actually chose, laid out by its formation string
 * ("4-3-3" = 4 defenders, 3 midfielders, 3 forwards, keeper on top). No pitch
 * graphic: rows of cards read better at a glance and survive any formation.
 */
export function Formation({ formation, eleven }: { formation: string | null; eleven: SquadRow[] }) {
  const byPosition = (name: string) => eleven.filter((p) => p.position === name);
  const counts = (formation ?? "").split("-").map((n) => Number(n));
  const rows: { label: string; players: SquadRow[] }[] = [
    { label: "GK", players: byPosition("Goalkeeper").slice(0, 1) },
    { label: "DEF", players: byPosition("Defender").slice(0, counts[0] || 99) },
    { label: "MID", players: byPosition("Midfielder").slice(0, counts[1] || 99) },
    { label: "FW", players: byPosition("Forward").slice(0, counts[2] || 99) },
  ];

  return (
    <div className="flex flex-col gap-3">
      {rows.map((row) => (
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
    </div>
  );
}

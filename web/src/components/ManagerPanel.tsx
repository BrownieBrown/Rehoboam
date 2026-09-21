import Link from "next/link";
import {
  leagueManager,
  managerMatchdays,
  managerSquad,
  managerTransfers,
  type ManagerMatchday,
  type ManagerSquadRow,
  type ManagerTransfer,
} from "@/lib/queries";
import { availability } from "@/lib/availability";
import { hrefFor, type Params } from "@/lib/query-href";
import { ago, DASH, money, num, pct, POSITION, signed, signedMoney, type Tone } from "@/lib/format";
import { Pill } from "@/components/Pill";
import { ClubCrest, PlayerPhoto } from "@/components/PlayerPhoto";
import { PanelPlaceholder } from "@/components/PlayerPanel";

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

const DOT: Record<Tone, string> = {
  positive: "bg-positive",
  negative: "bg-negative",
  neutral: "bg-muted",
};

const LABEL = "text-[10px] font-semibold uppercase tracking-[0.08em] text-muted";
const BOX = "flex flex-col gap-2 rounded-lg border border-border bg-bg px-[13px] py-[11px]";

function Tile({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-lg border border-border bg-bg px-3 py-2.5">
      <span className={LABEL}>{label}</span>
      <div className="tnum text-lg font-semibold text-text">{children}</div>
    </div>
  );
}

/** One played matchday: his points and where that placed him that day. */
function MatchdayCell({ day, best }: { day: ManagerMatchday; best: boolean }) {
  return (
    <div
      className={`flex h-[50px] min-w-[58px] flex-1 flex-col items-center justify-center gap-px rounded-md border ${
        best ? "border-accent/40" : "border-border"
      }`}
    >
      <span className="text-[10px] text-muted">MD {day.day_number}</span>
      <span className="tnum text-[15px] font-semibold text-text">{num(day.matchday_points)}</span>
      <span className="tnum text-[9px] font-semibold uppercase tracking-[0.04em] text-muted">
        {day.rank_matchday === null ? DASH : `#${day.rank_matchday}`}
      </span>
    </div>
  );
}

function SquadLine({ row }: { row: ManagerSquadRow }) {
  const pos = POSITION[row.position ?? ""] ?? { short: row.position ?? DASH, token: "plain" };
  const avail = availability(row.availability);
  const gain = signedMoney(row.gain_loss);
  return (
    <tr className="border-t border-border">
      <td className="py-1.5 pr-2">
        {/* Opens him on Players, where the full player panel lives. */}
        <Link href={`/?player=${row.player_id}`} className="flex items-center gap-2 hover:text-accent">
          <PlayerPhoto path={row.image_path} name={row.player_name ?? "?"} size={30} />
          <span className="flex min-w-0 flex-col">
            <span className="flex items-center gap-1.5 truncate text-[13px] font-semibold text-text">
              <span
                title={avail.label}
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT[avail.tone]}`}
              />
              {row.player_name ?? row.player_id}
              {row.on_market ? <span className="text-[11px] font-medium text-accent">listed</span> : null}
            </span>
            <span className="flex items-center gap-1 text-[11px] text-muted">
              <ClubCrest path={row.crest_path} size={13} />
              {row.team ?? DASH}
            </span>
          </span>
        </Link>
      </td>
      <td className="px-2 text-right">
        <Pill tone={pos.token}>{pos.short}</Pill>
      </td>
      <td className="tnum px-2 text-right text-[13px] font-semibold text-accent">
        {num(row.predicted_ep, 0)}
      </td>
      <td className="tnum px-2 text-right text-[12px] text-text-dim">{pct(row.p_start)}</td>
      <td className="tnum px-2 text-right text-[12px] text-text-dim">{num(row.avg_points, 1)}</td>
      <td className="tnum px-2 text-right text-[12px] text-text-dim">{money(row.market_value)}</td>
      <td className={`tnum pl-2 text-right text-[12px] ${TONE[gain.tone]}`}>{gain.text}</td>
    </tr>
  );
}

function TransferLine({ transfer }: { transfer: ManagerTransfer }) {
  const bought = transfer.kind === "buy";
  return (
    <div className="flex items-center gap-2">
      <span
        className={`w-9 shrink-0 text-[10px] font-bold uppercase tracking-[0.04em] ${
          bought ? "text-positive" : "text-negative"
        }`}
      >
        {transfer.kind ?? DASH}
      </span>
      <Link
        href={`/?player=${transfer.player_id}`}
        className="min-w-0 flex-1 truncate text-[13px] font-semibold text-text hover:text-accent"
      >
        {transfer.player_name ?? transfer.player_id}
      </Link>
      <span className="tnum shrink-0 text-[12px] text-text-dim">{money(transfer.price)}</span>
      <span className="w-[92px] shrink-0 whitespace-nowrap text-right text-[11px] text-muted">
        {ago(transfer.transfer_at)}
      </span>
    </div>
  );
}

/**
 * One manager, docked beside the League table -- server-rendered and
 * URL-driven like `PlayerPanel`, whose placeholder frame it reuses so the
 * docked area never resizes. `selfPoints` is our own season total, so the
 * panel can say how far ahead of or behind us this manager is.
 */
export async function ManagerPanel({
  managerId,
  params,
  total,
  selfPoints,
}: {
  managerId: string;
  params: Params;
  /** How many managers the league has, for "3rd of 14". */
  total: number;
  selfPoints: number | null;
}): Promise<React.ReactElement> {
  const manager = await leagueManager(managerId);
  if (!manager) return <PanelPlaceholder message="That manager isn't in the league." />;

  const [squad, matchdays, transfers] = await Promise.all([
    managerSquad(managerId),
    managerMatchdays(managerId),
    managerTransfers(managerId, 12),
  ]);

  const bestDay = matchdays.reduce<number | null>(
    (best, d) => (d.matchday_points !== null && (best === null || d.matchday_points > best) ? d.matchday_points : best),
    null,
  );
  const vsUs =
    !manager.is_self && manager.total_points !== null && selfPoints !== null
      ? signed(manager.total_points - selfPoints, 0)
      : null;
  const pnl = signedMoney(manager.transfer_pnl);

  return (
    <div className="order-first flex min-w-0 flex-1 flex-col gap-3 rounded-lg border border-border bg-surface p-[18px] xl:order-none">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2.5">
            <h2 className="truncate text-[22px] font-bold text-text">{manager.name}</h2>
            {manager.is_self ? <Pill tone="accent">you</Pill> : null}
          </div>
          <div className="tnum text-[13px] text-text-dim">
            {manager.rank_overall === null ? "Not ranked" : `#${manager.rank_overall} of ${total}`}
            <span className="text-muted"> · after matchday {num(manager.day_number)}</span>
          </div>
        </div>
        <Link
          href={hrefFor("/league", params, { manager: null })}
          aria-label="Close"
          className="shrink-0 text-xl leading-none text-muted hover:text-text-dim"
        >
          ×
        </Link>
      </div>

      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        <Tile label="Total points">{num(manager.total_points)}</Tile>
        <Tile label={manager.is_self ? "Behind the leader" : "Against you"}>
          {manager.is_self ? (
            num(manager.points_behind_leader)
          ) : vsUs ? (
            // Ahead of us is bad news for us: flip the usual green-is-up tone.
            <span className={TONE[vsUs.tone === "positive" ? "negative" : vsUs.tone === "negative" ? "positive" : "neutral"]}>
              {vsUs.text}
            </span>
          ) : (
            DASH
          )}
        </Tile>
        <Tile label="Team value">{money(manager.team_value)}</Tile>
        <Tile label="Best eleven, expected">{num(manager.top11_ep, 0)}</Tile>
      </div>

      <div className={BOX}>
        <span className={LABEL}>Matchdays this season</span>
        {matchdays.length > 0 ? (
          <div className="flex gap-1.5 overflow-x-auto">
            {matchdays.map((d) => (
              <MatchdayCell
                key={d.day_number}
                day={d}
                best={bestDay !== null && d.matchday_points === bestDay}
              />
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted">No matchday recorded yet.</p>
        )}
      </div>

      <div className={BOX}>
        <div className="flex items-baseline justify-between gap-3">
          <span className={LABEL}>Squad · {squad.length}</span>
          <span className="tnum text-[11px] text-muted">
            Transfer result <span className={TONE[pnl.tone]}>{pnl.text}</span> · matchdays won{" "}
            {num(manager.matchday_wins)}
          </span>
        </div>
        {squad.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="text-[10px] font-semibold uppercase tracking-[0.06em] text-muted">
                  <th className="pb-1 pr-2 text-left">Player</th>
                  <th className="px-2 pb-1 text-right">Pos</th>
                  <th className="px-2 pb-1 text-right" title="Expected points, next matchday">EP</th>
                  <th className="px-2 pb-1 text-right">Start</th>
                  <th className="px-2 pb-1 text-right">Ø pts</th>
                  <th className="px-2 pb-1 text-right">Market value</th>
                  <th className="pb-1 pl-2 text-right" title="Market value against what he paid">Gain</th>
                </tr>
              </thead>
              <tbody>
                {squad.map((row) => (
                  <SquadLine key={row.player_id} row={row} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted">No squad snapshot stored for this manager.</p>
        )}
      </div>

      <div className={BOX}>
        <span className={LABEL}>
          Latest transfers · {manager.buys_7d} bought, {manager.sells_7d} sold this week
        </span>
        {transfers.length > 0 ? (
          <div className="flex flex-col gap-[7px]">
            {transfers.map((t) => (
              <TransferLine key={`${t.player_id}-${t.transfer_at}-${t.kind}`} transfer={t} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted">No transfers stored.</p>
        )}
      </div>
    </div>
  );
}

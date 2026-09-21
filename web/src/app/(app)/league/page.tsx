import Link from "next/link";
import { requireSession } from "@/lib/auth";
import { leagueManagers, LEAGUE_SORTS, type LeagueManager } from "@/lib/queries";
import { sortDir, sortKey } from "@/lib/sort";
import { hrefFor } from "@/lib/query-href";
import { money, num, signedMoney, type Tone } from "@/lib/format";
import { DataTable, type Column } from "@/components/DataTable";
import { ManagerPanel } from "@/components/ManagerPanel";
import { PanelPlaceholder } from "@/components/PlayerPanel";
import { Pill } from "@/components/Pill";
import { StatusHeader } from "@/components/StatusHeader";

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

export default async function LeaguePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  await requireSession();
  const params = await searchParams;
  const sort = sortKey(params.sort, LEAGUE_SORTS, "rank_overall");
  // A rank reads best first; every other column reads biggest first.
  const dir = params.dir ? sortDir(params.dir) : sort === "rank_overall" ? "asc" : "desc";
  const rows = await leagueManagers({ sort, dir });
  const self = rows.find((r) => r.is_self) ?? null;
  const day = rows.reduce<number | null>(
    (max, r) => (r.day_number !== null && (max === null || r.day_number > max) ? r.day_number : max),
    null,
  );

  const columns: Column<LeagueManager>[] = [
    { key: "rank_overall", label: "#", cell: (r) => num(r.rank_overall) },
    {
      key: "name",
      label: "Manager",
      align: "left",
      sortable: false,
      cell: (r) => (
        <Link
          href={hrefFor("/league", params, { manager: r.manager_id })}
          className={`flex items-center gap-2 text-sm font-semibold hover:text-accent ${
            params.manager === r.manager_id ? "text-accent" : "text-text"
          }`}
        >
          {r.name}
          {r.is_self ? <Pill tone="accent">you</Pill> : null}
        </Link>
      ),
    },
    { key: "total_points", label: "Points", cell: (r) => num(r.total_points) },
    {
      key: "points_behind_leader",
      label: "Behind",
      sortable: false,
      hint: "Points behind the leader",
      cell: (r) => <span className="text-text-dim">{num(r.points_behind_leader)}</span>,
    },
    {
      key: "matchday_points",
      label: "Last matchday",
      hint: "Points on the newest matchday, and where that placed him that day",
      cell: (r) => (
        <span>
          {num(r.matchday_points)}{" "}
          <span className="text-[11px] text-muted">
            {r.rank_matchday === null ? "" : `#${r.rank_matchday}`}
          </span>
        </span>
      ),
    },
    {
      key: "top11_ep",
      label: "Best XI, expected",
      hint: "His eleven highest expected scores for the next matchday, any positions: a ceiling, not his lineup",
      cell: (r) => <span className="font-semibold text-accent">{num(r.top11_ep, 0)}</span>,
    },
    { key: "team_value", label: "Team value", cell: (r) => money(r.team_value) },
    { key: "squad_size", label: "Squad", cell: (r) => num(r.squad_size) },
    {
      key: "transfer_pnl",
      label: "Transfer result",
      hint: "What his buying and selling has made or lost, as Kickbase reports it",
      cell: (r) => {
        const out = signedMoney(r.transfer_pnl);
        return <span className={TONE[out.tone]}>{out.text}</span>;
      },
    },
    { key: "matchday_wins", label: "MD wins", cell: (r) => num(r.matchday_wins) },
    {
      key: "buys_7d",
      label: "7 d deals",
      hint: "Bought / sold in the last seven days",
      cell: (r) => (
        <span className="text-text-dim">
          {r.buys_7d} / {r.sells_7d}
        </span>
      ),
    },
  ];

  return (
    <>
      <StatusHeader
        title="League"
        subtitle={`${rows.length} managers${day === null ? "" : ` · after matchday ${day}`}`}
      />
      <div className="flex min-h-0 flex-1 flex-col gap-6 px-6 pb-6 pt-3 xl:flex-row">
        <div className="min-w-0 xl:flex-[1.2]">
          <DataTable
            columns={columns}
            rows={rows}
            sort={sort}
            dir={dir}
            basePath="/league"
            query={{ manager: params.manager }}
            rowClass={(r) => (r.is_self ? "bg-accent/5" : "")}
          />
        </div>
        {params.manager ? (
          <ManagerPanel
            managerId={params.manager}
            params={params}
            total={rows.length}
            selfPoints={self?.total_points ?? null}
          />
        ) : (
          <PanelPlaceholder message="Pick a manager to see his squad, his matchdays and his transfers." />
        )}
      </div>
    </>
  );
}

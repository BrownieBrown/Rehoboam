import { requireSession } from "@/lib/auth";
import { clubs, players, selfName, PLAYER_SORTS, type PlayerRow } from "@/lib/queries";
import { sortDir, sortKey } from "@/lib/sort";
import { DataTable, type Column } from "@/components/DataTable";
import { Filters } from "@/components/Filters";
import { Pill } from "@/components/Pill";
import { StatusHeader } from "@/components/StatusHeader";
import { money, num, signed, signedPct, POSITION, type Tone } from "@/lib/format";

export const revalidate = 300;

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

function Trend({ pct, value }: { pct?: number | null; value?: number | null }) {
  const out = pct !== undefined ? signedPct(pct) : signed(value ?? null, 1);
  return <span className={TONE[out.tone]}>{out.text}</span>;
}

export default async function PlayersPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  await requireSession();
  const params = await searchParams;
  const sort = sortKey(params.sort, PLAYER_SORTS, "predicted_ep");
  const dir = sortDir(params.dir);
  const owner = params.owner === "mine" || params.owner === "free" ? params.owner : undefined;

  const [rows, clubList, me] = await Promise.all([
    players({ sort, dir, position: params.position, owner, club: params.club, q: params.q }),
    clubs(),
    selfName(),
  ]);

  const columns: Column<PlayerRow>[] = [
    {
      key: "name",
      label: "Player",
      align: "left",
      cell: (p) => (
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-semibold text-text">{p.name}</span>
          <span className="text-xs text-muted">{p.team ?? "-"}</span>
        </div>
      ),
    },
    {
      key: "position",
      label: "Pos",
      align: "left",
      cell: (p) => {
        const pos = POSITION[p.position] ?? { short: p.position, token: "plain" };
        return <Pill tone={pos.token}>{pos.short}</Pill>;
      },
    },
    { key: "market_value", label: "Market value", cell: (p) => money(p.market_value) },
    { key: "trend_24h_pct", label: "24h", cell: (p) => <Trend pct={p.trend_24h_pct} /> },
    { key: "trend_7d_pct", label: "7d", cell: (p) => <Trend pct={p.trend_7d_pct} /> },
    { key: "points", label: "Pts", cell: (p) => <b className="text-text">{num(p.points)}</b> },
    { key: "avg_points", label: "Avg", cell: (p) => num(p.avg_points, 1) },
    { key: "median_points", label: "Median", cell: (p) => num(p.median_points, 1) },
    { key: "points_per_million", label: "Pts / M", cell: (p) => num(p.points_per_million, 2) },
    { key: "appearances", label: "Apps", cell: (p) => num(p.appearances) },
    { key: "starts", label: "Starts", cell: (p) => num(p.starts) },
    {
      key: "owner",
      label: "Owner",
      align: "left",
      cell: (p) =>
        p.owner === me ? (
          <Pill tone="accent">{p.owner}</Pill>
        ) : p.owner === "Kickbase" ? (
          <span className="text-sm text-muted">free agent</span>
        ) : (
          <span className="text-sm text-text-dim">
            {p.owner}
            {p.listed ? <span className="text-muted"> - listed</span> : null}
          </span>
        ),
    },
    {
      key: "predicted_ep",
      label: "EP",
      cell: (p) => <b className="text-[15px] text-text">{num(p.predicted_ep, 0)}</b>,
    },
    {
      key: "p_start",
      label: "P(start)",
      cell: (p) => (p.p_start === null ? "-" : `${Math.round(p.p_start * 100)}%`),
    },
    { key: "fair_value_gap", label: "Fair", cell: (p) => <Trend value={p.fair_value_gap} /> },
  ];

  return (
    <>
      <StatusHeader title="Players" />
      <Filters clubs={clubList} params={params} />
      <div className="px-6 pb-6">
        <DataTable columns={columns} rows={rows} sort={sort} dir={dir} basePath="/" query={params} />
      </div>
    </>
  );
}

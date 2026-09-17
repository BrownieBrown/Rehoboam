import { requireSession } from "@/lib/auth";
import Link from "next/link";
import {
  clubs,
  playerCount,
  players,
  selfName,
  PLAYER_SORTS,
  type PlayerFilter,
  type PlayerRow,
} from "@/lib/queries";
import { sortDir, sortKey } from "@/lib/sort";
import { clampPage, pageHref, pageOffset, pageSummary, parsePage } from "@/lib/paging";
import { DataTable, type Column } from "@/components/DataTable";
import { FairPrice } from "@/components/FairPrice";
import { Filters } from "@/components/Filters";
import { Pill } from "@/components/Pill";
import { StatusHeader } from "@/components/StatusHeader";
import { DASH, money, num, pct, signed, signedPct, POSITION, type Tone } from "@/lib/format";
import { NextMvCell } from "@/components/NextMv";
import { NEXT_MV_HINT, noForecastNote } from "@/lib/next-mv";

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

const APPS_HINT = "Matches played this season, started or came on";
const STARTS_HINT = "Matches he started";
const FAIR_PRICE_HINT =

  "What his average points are worth at his position's going rate, from at least three appearances";

const PAGE_LINK =
  "inline-flex h-8 items-center rounded-md border border-border-strong px-3 text-[13px] font-semibold";

/** Previous / next, or the same label greyed out where there is no such page. */
function PageLink({ href, children }: { href: string | null; children: React.ReactNode }) {
  return href ? (
    <Link href={href} className={`${PAGE_LINK} text-text-dim hover:text-text`}>
      {children}
    </Link>
  ) : (
    <span className={`${PAGE_LINK} text-muted opacity-50`}>{children}</span>
  );
}

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
  const filter: PlayerFilter = { position: params.position, owner, club: params.club, q: params.q };
  const requested = parsePage(params.page);

  const [firstTry, clubList, me] = await Promise.all([
    players({ ...filter, sort, dir, offset: pageOffset(requested) }),
    clubs(),
    selfName(),
  ]);
  let page = requested;
  let rows = firstTry;
  let counted: number | null = null;
  if (rows.length === 0 && requested > 1) {
    // Past the last page (an edited URL, or rows that went away): show the
    // last page there is (page 1 when nothing matches) instead.
    counted = await playerCount(filter);
    page = clampPage(requested, counted);
    rows = await players({ ...filter, sort, dir, offset: pageOffset(page) });
  }
  const offset = pageOffset(page);
  // With rows, `total` was counted by the statement that returned them. With
  // none on page 1, the filters matched nothing. With none after the clamp,
  // the count that chose the page is the only number there is.
  const total = rows[0]?.total ?? counted ?? 0;
  const hasNext = offset + rows.length < total;

  const columns: Column<PlayerRow>[] = [
    {
      key: "name",
      label: "Player",
      align: "left",
      cell: (p) => (
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-semibold text-text">{p.name}</span>
          <span className="text-xs text-muted">{p.team ?? DASH}</span>
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
    {
      key: "fair_price",
      label: "Fair price",
      hint: FAIR_PRICE_HINT,
      cell: (p) => <FairPrice price={p.fair_price} marketValue={p.market_value} />,
    },
    { key: "trend_24h_pct", label: "24h", cell: (p) => <Trend pct={p.trend_24h_pct} /> },
    { key: "trend_7d_pct", label: "7d", cell: (p) => <Trend pct={p.trend_7d_pct} /> },
    {
      key: "next_mv_pct",
      label: "Next MV",
      hint: NEXT_MV_HINT,
      cell: (p) => <NextMvCell pct={p.next_mv_pct} change={p.next_mv_change} />,
    },
    { key: "points", label: "Pts", cell: (p) => <b className="text-text">{num(p.points)}</b> },
    { key: "avg_points", label: "Avg", cell: (p) => num(p.avg_points, 1) },
    { key: "median_points", label: "Median", cell: (p) => num(p.median_points, 1) },
    { key: "points_per_million", label: "Pts / M", cell: (p) => num(p.points_per_million, 2) },
    { key: "appearances", label: "Played", hint: APPS_HINT, cell: (p) => num(p.appearances) },
    { key: "starts", label: "Starts", hint: STARTS_HINT, cell: (p) => num(p.starts) },
    {
      key: "owner",
      label: "Owner",
      align: "left",
      // `player_table.owner` is 'Kickbase' for a free agent and 'market' for
      // one in the newest listing snapshot - both muted, never a manager's style.
      cell: (p) =>
        p.owner === me ? (
          <Pill tone="accent">{p.owner}</Pill>
        ) : p.owner === "Kickbase" || p.owner === "market" ? (
          <span className="text-sm text-muted">
            free agent{p.owner === "market" || p.listed ? " - listed" : null}
          </span>
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
      cell: (p) => pct(p.p_start),
    },
  ];

  return (
    <>
      <StatusHeader title="Players" />
      <Filters clubs={clubList} params={params} />
      <div className="flex items-center justify-between gap-4 px-6 py-3">
        <span className="tnum text-sm text-muted">{pageSummary(offset, rows.length, total)}</span>
        <div className="flex items-center gap-2">
          <PageLink href={page > 1 ? pageHref("/", params, page - 1) : null}>Previous</PageLink>
          <PageLink href={hasNext ? pageHref("/", params, page + 1) : null}>Next</PageLink>
        </div>
      </div>
      {noForecastNote(rows) ? (
        <p className="px-6 pt-1 text-sm text-muted">{noForecastNote(rows)}</p>
      ) : null}
      <div className="px-6 pb-6">
        {/* A new sort starts again at page 1. */}
        <DataTable
          columns={columns}
          rows={rows}
          sort={sort}
          dir={dir}
          basePath="/"
          query={{ ...params, page: undefined }}
        />
      </div>
    </>
  );
}

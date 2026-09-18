import { requireSession } from "@/lib/auth";
import { market, MARKET_SORTS, type MarketRow } from "@/lib/queries";
import { sortDir, sortKey } from "@/lib/sort";
import { DataTable, type Column } from "@/components/DataTable";
import { Pill } from "@/components/Pill";
import { StatusHeader } from "@/components/StatusHeader";
import { ago, countdown, DASH, money, num, pct, signedPct, POSITION, type Tone } from "@/lib/format";
import { FairPrice } from "@/components/FairPrice";
import { expiringWithin, listingsLine } from "@/lib/expiry";
import { bySeller, sellerScope } from "@/lib/market-filter";
import { hrefFor } from "@/lib/query-href";
import { NextMvCell } from "@/components/NextMv";
import { NEXT_MV_HINT, noForecastNote } from "@/lib/next-mv";
import Link from "next/link";

/** A filter chip: a plain link, filled when it is the active choice. */
function Chip({ href, active, children }: { href: string; active: boolean; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className={`inline-flex h-8 items-center rounded-md px-3 text-[13px] font-semibold ${
        active ? "bg-text text-bg" : "border border-border-strong text-text-dim"
      }`}
    >
      {children}
    </Link>
  );
}

const FAIR_PRICE_HINT =

  "What his average points are worth at his position's going rate, from at least three appearances";

const PPM_HINT = "Season points per million euros of market value";

/** A signed percentage in its tone, for the 24h move. */
function Trend({ pct }: { pct: number | null }) {
  const out = signedPct(pct);
  return <span className={TONE[out.tone]}>{out.text}</span>;
}

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

export default async function MarketPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  await requireSession();
  const params = await searchParams;
  const sort = sortKey(params.sort, MARKET_SORTS, "predicted_ep");
  const dir = sortDir(params.dir);
  const expiring = params.expiring === "6" ? 6 : undefined;
  const scope = sellerScope(params.from);

  const listings = await market({ sort, dir });
  const bySource = bySeller(listings, scope);
  const rows = expiring ? expiringWithin(bySource, expiring, Date.now() / 1000) : bySource;

  const columns: Column<MarketRow>[] = [
    {
      key: "name", label: "Player", align: "left",
      cell: (r) => (
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-semibold text-text">{r.name ?? r.player_id}</span>
          <span className="text-xs text-muted">{r.team ?? DASH}</span>
        </div>
      ),
    },
    {
      key: "position", label: "Pos", align: "left",
      cell: (r) => {
        const pos = POSITION[r.position ?? ""] ?? { short: r.position ?? DASH, token: "plain" };
        return <Pill tone={pos.token}>{pos.short}</Pill>;
      },
    },
    { key: "ask", label: "Ask", cell: (r) => money(r.ask) },
    { key: "market_value", label: "Market value", cell: (r) => money(r.market_value) },
    {
      key: "fair_price", label: "Fair price", hint: FAIR_PRICE_HINT,
      cell: (r) => <FairPrice price={r.fair_price} marketValue={r.market_value} />,
    },
    { key: "trend_24h_pct", label: "24h", cell: (r) => <Trend pct={r.trend_24h_pct} /> },
    {
      key: "points_per_million", label: "Pts / M", hint: PPM_HINT,
      cell: (r) => num(r.points_per_million, 2),
    },
    {
      key: "next_mv_pct", label: "Next MV", hint: NEXT_MV_HINT,
      cell: (r) => <NextMvCell pct={r.next_mv_pct} change={r.next_mv_change} />,
    },
    {
      key: "seller", label: "Seller", align: "left",
      cell: (r) =>
        r.is_ours ? <Pill tone="accent">{r.seller}</Pill> : (
          <span className="text-sm text-text-dim">{r.seller}</span>
        ),
    },
    { key: "expires_at", label: "Expires in", cell: (r) => countdown(r.expires_at) },
    {
      key: "predicted_ep", label: "EP",
      cell: (r) => <b className="text-[15px] text-text">{num(r.predicted_ep, 0)}</b>,
    },
    {
      key: "p_start", label: "P(start)",
      cell: (r) => pct(r.p_start),
    },
  ];

  // From the unfiltered snapshot: every row carries the same snapshot time,
  // and a filter that keeps nothing must not blank it.
  const snapshotAt = listings[0]?.snapshot_at ?? null;

  return (
    <>
      <StatusHeader
        title="Market"
        subtitle={`${listingsLine(rows.length, listings.length, { scope, hours: expiring })} · snapshot ${ago(snapshotAt)}`}
      />
      <div className="flex flex-wrap items-center gap-4 px-6 py-4">
        <div className="flex items-center gap-2">
          <Chip href={hrefFor("/market", params, { from: null })} active={scope === "kickbase"}>
            From Kickbase
          </Chip>
          <Chip href={hrefFor("/market", params, { from: "managers" })} active={scope === "managers"}>
            From managers
          </Chip>
          <Chip href={hrefFor("/market", params, { from: "all" })} active={scope === "all"}>
            All sellers
          </Chip>
        </div>
        <div className="flex items-center gap-2">
          <Chip href={hrefFor("/market", params, { expiring: null })} active={!expiring}>
            Any expiry
          </Chip>
          <Chip href={hrefFor("/market", params, { expiring: "6" })} active={expiring === 6}>
            Expiring under 6 h
          </Chip>
        </div>
      </div>
      {noForecastNote(rows) ? (
        <p className="px-6 pb-2 text-sm text-muted">{noForecastNote(rows)}</p>
      ) : null}
      <div className="px-6 pb-6">
        <DataTable
          columns={columns} rows={rows} sort={sort} dir={dir}
          basePath="/market" query={params}
        />
      </div>
    </>
  );
}

import { requireSession } from "@/lib/auth";
import { managers, market, MARKET_SORTS, type MarketRow } from "@/lib/queries";
import { sortDir, sortKey } from "@/lib/sort";
import { DataTable, type Column } from "@/components/DataTable";
import { Pill } from "@/components/Pill";
import { StatusHeader } from "@/components/StatusHeader";
import { ago, countdown, DASH, money, num, pct, signed, signedPct, POSITION, type Tone } from "@/lib/format";
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

const FAIR_PTS_HINT =
  "His average points minus what players at his position and price average — positive means he outscores his price";
const FAIR_PRICE_HINT =
  "What his average points are worth at his position's going rate, against his market value";

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

  const [listings, managerRows] = await Promise.all([market({ sort, dir }), managers()]);
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
      cell: (r) => money(r.fair_price),
    },
    {
      key: "next_mv_pct", label: "Next MV", hint: NEXT_MV_HINT,
      cell: (r) => <NextMvCell pct={r.next_mv_pct} change={r.next_mv_change} />,
    },
    {
      key: "over", label: "Ask vs MV", sortable: false,
      cell: (r) => {
        if (!r.market_value) return DASH;
        const out = signedPct(((r.ask - r.market_value) / r.market_value) * 100);
        return <span className={TONE[out.tone]}>{out.text}</span>;
      },
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
    {
      key: "fair_value_gap", label: "Fair pts", hint: FAIR_PTS_HINT,
      cell: (r) => {
        const out = signed(r.fair_value_gap, 1);
        return <span className={TONE[out.tone]}>{out.text}</span>;
      },
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
      <div className="px-6 pb-8">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-[0.08em] text-muted">
          Who owns what
        </h2>
        <div className="overflow-x-auto rounded-lg border border-border bg-surface">
          <table className="w-full border-collapse">
            <thead>
              <tr className="text-[11px] uppercase tracking-[0.08em] text-muted">
                <th className="h-10 border-b border-border-strong px-3 text-left">Manager</th>
                <th className="h-10 border-b border-border-strong px-3 text-right">Squad</th>
                <th className="h-10 border-b border-border-strong px-3 text-right">Team value</th>
                <th className="h-10 border-b border-border-strong px-3 text-left">
                  Top three by predicted points
                </th>
              </tr>
            </thead>
            <tbody>
              {managerRows.map((m) => (
                <tr key={m.manager_id}>
                  <td className="h-11 border-b border-border px-3 text-left">
                    {m.is_self ? (
                      <Pill tone="accent">{m.manager}</Pill>
                    ) : (
                      <span className="text-sm text-text-dim">{m.manager}</span>
                    )}
                  </td>
                  <td className="tnum h-11 border-b border-border px-3 text-right">
                    {m.squad_size}
                  </td>
                  <td className="tnum h-11 border-b border-border px-3 text-right">
                    {money(m.team_value)}
                  </td>
                  <td className="h-11 border-b border-border px-3 text-left text-sm text-muted">
                    {m.top.join(", ") || DASH}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

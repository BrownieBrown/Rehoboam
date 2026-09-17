import { requireSession } from "@/lib/auth";
import { managers, market, MARKET_SORTS, type MarketRow } from "@/lib/queries";
import { sortDir, sortKey } from "@/lib/sort";
import { DataTable, type Column } from "@/components/DataTable";
import { Pill } from "@/components/Pill";
import { StatusHeader } from "@/components/StatusHeader";
import { ago, countdown, DASH, money, num, pct, signed, signedPct, POSITION, type Tone } from "@/lib/format";
import Link from "next/link";

export const revalidate = 300;

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

  const [rows, managerRows] = await Promise.all([
    market({ sort, dir, expiringHours: expiring }),
    managers(),
  ]);

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
    { key: "offer_count", label: "Offers", cell: (r) => num(r.offer_count) },
    {
      key: "predicted_ep", label: "EP",
      cell: (r) => <b className="text-[15px] text-text">{num(r.predicted_ep, 0)}</b>,
    },
    {
      key: "p_start", label: "P(start)",
      cell: (r) => pct(r.p_start),
    },
    {
      key: "fair_value_gap", label: "Fair",
      cell: (r) => {
        const out = signed(r.fair_value_gap, 1);
        return <span className={TONE[out.tone]}>{out.text}</span>;
      },
    },
  ];

  const snapshotAt = rows[0]?.snapshot_at ?? null;

  return (
    <>
      <StatusHeader
        title="Market"
        subtitle={`${rows.length} listings · snapshot ${ago(snapshotAt)}`}
      />
      <div className="flex items-center gap-2 px-6 py-4">
        <Link
          href="/market"
          className={`inline-flex h-8 items-center rounded-md px-3 text-[13px] font-semibold ${
            expiring ? "border border-border-strong text-text-dim" : "bg-text text-bg"
          }`}
        >
          All
        </Link>
        <Link
          href="/market?expiring=6"
          className={`inline-flex h-8 items-center rounded-md px-3 text-[13px] font-semibold ${
            expiring ? "bg-text text-bg" : "border border-border-strong text-text-dim"
          }`}
        >
          Expiring under 6 h
        </Link>
      </div>
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
        <div className="overflow-hidden rounded-lg border border-border bg-surface">
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

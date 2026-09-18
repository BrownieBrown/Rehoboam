import { requireSession } from "@/lib/auth";
import { market, shellFacts, MARKET_SORTS, type MarketRow } from "@/lib/queries";
import { sortDir, sortKey } from "@/lib/sort";
import { Pill } from "@/components/Pill";
import { StatusHeader } from "@/components/StatusHeader";
import {
  ago,
  countdown,
  DASH,
  money,
  num,
  signed,
  signedMoney,
  signedPct,
  POSITION,
  type Tone,
} from "@/lib/format";
import { expiringWithin, listingsLine } from "@/lib/expiry";
import { bySeller, sellerScope } from "@/lib/market-filter";
import { hrefFor, type Params } from "@/lib/query-href";
import { NEXT_MV_HINT, noForecastNote } from "@/lib/next-mv";
import { PlayerPanel } from "@/components/PlayerPanel";
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

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

/** A signed percentage in its tone, for the 24h move. */
function Trend({ pct }: { pct: number | null }) {
  const out = signedPct(pct);
  return <span className={TONE[out.tone]}>{out.text}</span>;
}

/** A signed euro amount in its tone, for tonight's forecast move. */
function MoneyTone({ value }: { value: number | null }) {
  const out = signedMoney(value);
  return <span className={TONE[out.tone]}>{out.text}</span>;
}

/** Money on top, his gap to market value as a signed whole percent underneath
 * -- "vs MV", never "vs ask": the ask column is gone, and fair value only
 * ever gets compared to what the market actually values him at. */
function FairValueCell({ price, marketValue }: { price: number | null; marketValue: number | null }) {
  if (price === null) return <span className="text-sm text-muted">{DASH}</span>;
  const gap =
    marketValue !== null && marketValue !== 0
      ? signed(((price - marketValue) / marketValue) * 100, 0)
      : null;
  return (
    <div className="flex flex-col items-end gap-px">
      <span className="tnum text-[14px] text-text-dim">{money(price)}</span>
      {gap ? (
        <span className={`tnum text-[11px] font-semibold ${TONE[gap.tone]}`}>{gap.text} % vs MV</span>
      ) : null}
    </div>
  );
}

/** Time left on a listing, amber under six hours -- the one accent rule
 * every countdown on this page follows. */
function ExpiresLabel({ expiresAt, size }: { expiresAt: number | null; size: "13" | "12" }) {
  const soon = expiresAt !== null && expiresAt - Date.now() / 1000 < 6 * 3600;
  const sizeClass = size === "13" ? "text-[13px]" : "text-[12px]";
  return (
    <span
      className={`tnum whitespace-nowrap ${sizeClass} font-semibold ${soon ? "text-accent" : "text-text-dim"}`}
    >
      {countdown(expiresAt)}
    </span>
  );
}

const RULE_COLOR: Record<string, string> = {
  gk: "bg-gk",
  def: "bg-def",
  mid: "bg-mid",
  fw: "bg-fw",
};

/** A new sort starts again at page one; clicking the active key flips its
 * direction, the same toggle `DataTable`'s own sort links use. */
function sortHref(params: Params, sort: string, dir: "asc" | "desc", key: string) {
  return hrefFor("/market", params, { sort: key, dir: key === sort && dir === "desc" ? "asc" : "desc" });
}

function SortLink({
  href,
  active,
  dir,
  children,
}: {
  href: string;
  active: boolean;
  dir: "asc" | "desc";
  children: React.ReactNode;
}) {
  return (
    <Link href={href} className={`hover:text-text ${active ? "text-accent" : ""}`}>
      {children}
      {active ? (dir === "desc" ? " ▾" : " ▴") : ""}
    </Link>
  );
}

/** One player's active listing, for the panel's amber "Listed by ..." strip
 * -- `null` when the open player isn't among the rows the current filters
 * kept (a seller or expiry chip filtered him out from under the open panel). */
function listingFor(
  rows: MarketRow[],
  playerId: string,
): { seller: string; expiresAt: number | null } | null {
  const row = rows.find((r) => r.player_id === playerId);
  return row ? { seller: row.seller, expiresAt: row.expires_at } : null;
}

/**
 * The full ten-column table, shown only when nothing is open -- four band
 * groups (Who / What he costs / What he scores / The listing) above the
 * column headers, exact widths lifted from the approved artboard
 * (`docs/superpowers/design-refs/market-table.html`). Every row is a link
 * to open that player, replacing the old click-the-name-only affordance now
 * that the panel docks beside the table instead of covering it.
 */
function FullMarketTable({
  rows,
  sort,
  dir,
  params,
}: {
  rows: MarketRow[];
  sort: string;
  dir: "asc" | "desc";
  params: Params;
}) {
  const link = (key: string) => sortHref(params, sort, dir, key);
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-surface">
      <div className="min-w-[1168px]">
        <div className="flex h-7 items-center border-b border-border bg-bg">
          <div className="w-[286px] shrink-0 px-3 text-[10px] font-semibold uppercase tracking-[0.1em] text-muted/70">
            Who
          </div>
          <div className="w-[530px] shrink-0 border-l border-border px-3 text-[10px] font-semibold uppercase tracking-[0.1em] text-muted/70">
            What he costs
          </div>
          <div className="w-[146px] shrink-0 border-l border-border px-3 text-[10px] font-semibold uppercase tracking-[0.1em] text-muted/70">
            What he scores
          </div>
          <div className="w-[206px] shrink-0 border-l border-border px-3 text-[10px] font-semibold uppercase tracking-[0.1em] text-muted/70">
            The listing
          </div>
        </div>
        <div className="flex h-10 items-center border-b border-border-strong">
          <div className="w-[230px] shrink-0 px-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
            <SortLink href={link("name")} active={sort === "name"} dir={dir}>
              Player
            </SortLink>
          </div>
          <div className="w-[56px] shrink-0 px-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
            <SortLink href={link("position")} active={sort === "position"} dir={dir}>
              Pos
            </SortLink>
          </div>
          <div className="w-[150px] shrink-0 border-l border-border px-3 text-right text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
            <SortLink href={link("market_value")} active={sort === "market_value"} dir={dir}>
              Market value
            </SortLink>
          </div>
          <div
            title={FAIR_PRICE_HINT}
            className="w-[152px] shrink-0 px-3 text-right text-[11px] font-semibold uppercase tracking-[0.08em] text-muted"
          >
            <SortLink href={link("fair_price")} active={sort === "fair_price"} dir={dir}>
              Fair value
            </SortLink>
          </div>
          <div className="w-[110px] shrink-0 px-3 text-right text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
            <SortLink href={link("trend_24h_pct")} active={sort === "trend_24h_pct"} dir={dir}>
              24 h
            </SortLink>
          </div>
          <div
            title={NEXT_MV_HINT}
            className="w-[118px] shrink-0 px-3 text-right text-[11px] font-semibold uppercase tracking-[0.08em] text-muted"
          >
            <SortLink href={link("next_mv_pct")} active={sort === "next_mv_pct"} dir={dir}>
              Tonight
            </SortLink>
          </div>
          <div className="w-[66px] shrink-0 border-l border-border px-3 text-right text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
            <SortLink href={link("predicted_ep")} active={sort === "predicted_ep"} dir={dir}>
              EP
            </SortLink>
          </div>
          <div
            title={PPM_HINT}
            className="w-[80px] shrink-0 px-3 text-right text-[11px] font-semibold uppercase tracking-[0.08em] text-muted"
          >
            <SortLink href={link("points_per_million")} active={sort === "points_per_million"} dir={dir}>
              Pts / M
            </SortLink>
          </div>
          <div className="w-[116px] shrink-0 border-l border-border px-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
            <SortLink href={link("seller")} active={sort === "seller"} dir={dir}>
              Seller
            </SortLink>
          </div>
          <div className="w-[90px] shrink-0 px-3 text-right text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
            <SortLink href={link("expires_at")} active={sort === "expires_at"} dir={dir}>
              Expires
            </SortLink>
          </div>
        </div>
        {rows.map((row) => {
          const pos = POSITION[row.position ?? ""] ?? { short: row.position ?? DASH, token: "plain" };
          const rule = RULE_COLOR[pos.token] ?? "bg-border-strong";
          return (
            <Link
              key={row.player_id}
              href={hrefFor("/market", params, { player: row.player_id })}
              className="flex h-12 items-center border-b border-border hover:bg-bg/60"
            >
              <div className="flex w-[230px] min-w-0 shrink-0 items-center gap-2.5 px-3">
                <span className={`h-7 w-[3px] shrink-0 rounded-sm ${rule}`} />
                <div className="flex min-w-0 flex-col gap-px">
                  <span className="truncate text-sm font-semibold text-text">
                    {row.name ?? row.player_id}
                  </span>
                  <span className="truncate text-[11px] text-muted">{row.team ?? DASH}</span>
                </div>
              </div>
              <div className="w-[56px] shrink-0 px-3">
                <Pill tone={pos.token}>{pos.short}</Pill>
              </div>
              <div className="tnum w-[150px] shrink-0 border-l border-border px-3 text-right text-sm font-semibold text-text">
                {money(row.market_value)}
              </div>
              <div className="w-[152px] shrink-0 px-3">
                <FairValueCell price={row.fair_price} marketValue={row.market_value} />
              </div>
              <div className="tnum w-[110px] shrink-0 px-3 text-right text-[13px] font-semibold">
                <Trend pct={row.trend_24h_pct} />
              </div>
              <div className="tnum w-[118px] shrink-0 px-3 text-right text-[13px]">
                <MoneyTone value={row.next_mv_change} />
              </div>
              <div className="tnum w-[66px] shrink-0 border-l border-border px-3 text-right text-base font-semibold text-text">
                {num(row.predicted_ep, 0)}
              </div>
              <div className="tnum w-[80px] shrink-0 px-3 text-right text-[13px] text-text-dim">
                {num(row.points_per_million, 1)}
              </div>
              <div className="w-[116px] min-w-0 shrink-0 border-l border-border px-3">
                {row.is_ours ? (
                  <Pill tone="accent">{row.seller}</Pill>
                ) : (
                  <span className="truncate text-[13px] text-text-dim">{row.seller}</span>
                )}
              </div>
              <div className="w-[90px] shrink-0 px-3 text-right">
                <ExpiresLabel expiresAt={row.expires_at} size="13" />
              </div>
            </Link>
          );
        })}
        {rows.length === 0 ? <p className="p-6 text-center text-sm text-muted">Nothing here yet.</p> : null}
      </div>
    </div>
  );
}

/**
 * The four-column table shown beside the open panel -- Player, Market value,
 * EP, Ends -- so the rows stay put instead of being covered. `selected`
 * highlights the open player's row the same way `PlayerList` does.
 */
function NarrowMarketTable({
  rows,
  sort,
  dir,
  params,
  selected,
}: {
  rows: MarketRow[];
  sort: string;
  dir: "asc" | "desc";
  params: Params;
  selected: string;
}) {
  const link = (key: string) => sortHref(params, sort, dir, key);
  return (
    <div className="w-full shrink-0 overflow-x-auto rounded-lg border border-border bg-surface xl:w-[484px]">
      <div className="min-w-[484px]">
      <div className="flex h-10 items-center border-b border-border-strong">
        <div className="w-[190px] shrink-0 px-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
          <SortLink href={link("name")} active={sort === "name"} dir={dir}>
            Player
          </SortLink>
        </div>
        <div className="w-[140px] shrink-0 border-l border-border px-3 text-right text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
          <SortLink href={link("market_value")} active={sort === "market_value"} dir={dir}>
            Market value
          </SortLink>
        </div>
        <div className="w-[56px] shrink-0 border-l border-border px-2 text-right text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
          <SortLink href={link("predicted_ep")} active={sort === "predicted_ep"} dir={dir}>
            EP
          </SortLink>
        </div>
        <div className="w-[98px] shrink-0 border-l border-border px-3 text-right text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
          <SortLink href={link("expires_at")} active={sort === "expires_at"} dir={dir}>
            Ends
          </SortLink>
        </div>
      </div>
      {rows.map((row) => {
        const isSelected = row.player_id === selected;
        const pos = POSITION[row.position ?? ""] ?? { short: row.position ?? DASH, token: "plain" };
        const rule = isSelected ? "bg-accent" : (RULE_COLOR[pos.token] ?? "bg-border-strong");
        return (
          <Link
            key={row.player_id}
            href={hrefFor("/market", params, { player: row.player_id })}
            className={`flex h-12 items-center border-b border-border ${isSelected ? "bg-accent/8" : ""}`}
          >
            <div className="flex w-[190px] min-w-0 shrink-0 items-center gap-[9px] px-3">
              <span className={`h-7 w-[3px] shrink-0 rounded-sm ${rule}`} />
              <div className="flex min-w-0 flex-col gap-px">
                <span className="truncate text-[13px] font-semibold text-text">
                  {row.name ?? row.player_id}
                </span>
                <span className="truncate text-[10px] text-muted">{row.team ?? DASH}</span>
              </div>
            </div>
            <div className="tnum w-[140px] shrink-0 border-l border-border px-3 text-right text-[13px] font-semibold text-text">
              {money(row.market_value)}
            </div>
            <div className="tnum w-[56px] shrink-0 border-l border-border px-2 text-right text-[15px] font-semibold text-text">
              {num(row.predicted_ep, 0)}
            </div>
            <div className="w-[98px] shrink-0 border-l border-border px-3 text-right">
              <ExpiresLabel expiresAt={row.expires_at} size="12" />
            </div>
          </Link>
        );
      })}
      {rows.length === 0 ? <p className="p-6 text-center text-sm text-muted">Nothing here yet.</p> : null}
      </div>
    </div>
  );
}

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

  const [listings, facts] = await Promise.all([market({ sort, dir }), shellFacts()]);
  const bySource = bySeller(listings, scope);
  const rows = expiring ? expiringWithin(bySource, expiring, Date.now() / 1000) : bySource;

  // From the unfiltered snapshot: every row carries the same snapshot time,
  // and a filter that keeps nothing must not blank it.
  const snapshotAt = listings[0]?.snapshot_at ?? null;

  return (
    <>
      <StatusHeader
        title="Market"
        subtitle={`${listingsLine(rows.length, listings.length, { scope, hours: expiring })} · snapshot ${ago(snapshotAt)}`}
        right={
          <div className="flex flex-col items-end">
            <span className="text-[11px] uppercase tracking-[0.08em] text-muted">Budget</span>
            <b className="tnum text-2xl font-bold text-text">{money(facts.budget)}</b>
            {facts.lastSessionAt !== null ? (
              <span className="text-xs text-muted">as of {ago(facts.lastSessionAt)}</span>
            ) : null}
          </div>
        }
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
        {params.player ? (
          <div className="flex flex-col gap-6 xl:flex-row">
            <NarrowMarketTable rows={rows} sort={sort} dir={dir} params={params} selected={params.player} />
            <PlayerPanel
              playerId={params.player}
              basePath="/market"
              params={params}
              listing={listingFor(rows, params.player)}
            />
          </div>
        ) : (
          <FullMarketTable rows={rows} sort={sort} dir={dir} params={params} />
        )}
      </div>
    </>
  );
}

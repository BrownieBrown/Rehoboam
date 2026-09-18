import Link from "next/link";
import {
  playerMatches,
  playerMv,
  playerRow,
  playerSeasons,
  selfName,
  type PlayerMatch,
  type PlayerSeason,
} from "@/lib/queries";
import { Pill } from "@/components/Pill";
import { FairPrice } from "@/components/FairPrice";
import { NextMvCell } from "@/components/NextMv";
import { NEXT_MV_HINT } from "@/lib/next-mv";
import { sparkline } from "@/lib/sparkline";
import { matchStatus } from "@/lib/match-status";
import { DASH, money, num, pct, signedPct, POSITION, type Tone } from "@/lib/format";

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

// Same hints as the Players/Market table headers -- copied, not imported:
// those columns keep their own local copies too (see page.tsx / market/page.tsx).
const APPS_HINT = "Matches played this season, started or came on";
const STARTS_HINT = "Matches he started";
const FAIR_PRICE_HINT =
  "What his average points are worth at his position's going rate, from at least three appearances";
const PPM_HINT = "Season points per million euros of market value";

const CHART_W = 600;
const CHART_H = 120;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;

/** The stored `match_date` is free-form text; only print it when it looks
 * like an ISO date, else a dash rather than a raw, possibly-confusing value. */
function matchDateCell(matchDate: string | null): string {
  return matchDate && ISO_DATE.test(matchDate) ? matchDate.slice(0, 10) : DASH;
}

function Trend({ value }: { value: number | null }) {
  const out = signedPct(value);
  return <span className={TONE[out.tone]}>{out.text}</span>;
}

function Stat({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1" title={hint}>
      <span className="text-[11px] uppercase tracking-[0.08em] text-muted">{label}</span>
      <div className="tnum text-sm text-text">{children}</div>
    </div>
  );
}

/** Amber pill when it is ours, muted "free agent" for an unowned player, the
 * owning manager's name otherwise -- the same three-way rule the Players
 * page's owner column uses. */
function OwnerBadge({ owner, me, listed }: { owner: string; me: string | null; listed: boolean }) {
  if (owner === me) return <Pill tone="accent">{owner}</Pill>;
  if (owner === "Kickbase" || owner === "market") {
    return (
      <span className="text-sm text-muted">
        free agent{owner === "market" || listed ? " - listed" : ""}
      </span>
    );
  }
  return (
    <span className="text-sm text-text-dim">
      {owner}
      {listed ? <span className="text-muted"> - listed</span> : null}
    </span>
  );
}

function OpponentCell({ opponent, isHome }: { opponent: string | null; isHome: number | null }) {
  if (opponent === null) return <span className="text-muted">{DASH}</span>;
  const side = isHome === 1 ? "H" : isHome === 0 ? "A" : null;
  return (
    <span className="text-text-dim">
      {opponent}
      {side ? <span className="text-muted"> ({side})</span> : null}
    </span>
  );
}

function SeasonsTable({ seasons }: { seasons: PlayerSeason[] }) {
  if (seasons.length === 0) {
    return <p className="text-sm text-muted">No season has any recorded matches.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full border-collapse">
        <thead>
          <tr>
            {["Season", "Played", "Starts", "Points", "Average", "Median", "Best", "Minutes"].map(
              (label, i) => (
                <th
                  key={label}
                  className={`h-9 whitespace-nowrap border-b border-border-strong px-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted ${
                    i === 0 ? "text-left" : "text-right"
                  }`}
                >
                  {label}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {seasons.map((s) => (
            <tr key={s.season}>
              <td className="h-10 whitespace-nowrap border-b border-border px-3 text-left text-sm text-text">
                {s.season}
              </td>
              <td className="tnum h-10 whitespace-nowrap border-b border-border px-3 text-right">
                {num(s.appearances)}
              </td>
              <td className="tnum h-10 whitespace-nowrap border-b border-border px-3 text-right">
                {num(s.starts)}
              </td>
              <td className="tnum h-10 whitespace-nowrap border-b border-border px-3 text-right">
                {num(s.points)}
              </td>
              <td className="tnum h-10 whitespace-nowrap border-b border-border px-3 text-right">
                {num(s.avg_points, 1)}
              </td>
              <td className="tnum h-10 whitespace-nowrap border-b border-border px-3 text-right">
                {num(s.median_points, 1)}
              </td>
              <td className="tnum h-10 whitespace-nowrap border-b border-border px-3 text-right">
                {num(s.best_points)}
              </td>
              <td className="tnum h-10 whitespace-nowrap border-b border-border px-3 text-right">
                {num(s.minutes)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MatchesTable({ matches }: { matches: PlayerMatch[] }) {
  if (matches.length === 0) {
    return <p className="text-sm text-muted">No matches recorded yet.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full border-collapse">
        <thead>
          <tr>
            {["Season", "Matchday", "Date", "Opponent", "Points", "Minutes", "Status"].map(
              (label, i) => (
                <th
                  key={label}
                  className={`h-9 whitespace-nowrap border-b border-border-strong px-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted ${
                    i === 0 || i === 3 || i === 6 ? "text-left" : "text-right"
                  }`}
                >
                  {label}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {matches.map((m) => (
            <tr key={`${m.season}-${m.day_number}`}>
              <td className="h-10 whitespace-nowrap border-b border-border px-3 text-left text-sm text-text-dim">
                {m.season}
              </td>
              <td className="tnum h-10 whitespace-nowrap border-b border-border px-3 text-right">
                {m.day_number}
              </td>
              <td className="tnum h-10 whitespace-nowrap border-b border-border px-3 text-left">
                {matchDateCell(m.match_date)}
              </td>
              <td className="h-10 whitespace-nowrap border-b border-border px-3 text-left text-sm">
                <OpponentCell opponent={m.opponent} isHome={m.is_home} />
              </td>
              <td className="tnum h-10 whitespace-nowrap border-b border-border px-3 text-right">
                {num(m.points)}
              </td>
              <td className="tnum h-10 whitespace-nowrap border-b border-border px-3 text-right">
                {num(m.minutes)}
              </td>
              <td className="h-10 whitespace-nowrap border-b border-border px-3 text-left text-sm text-text-dim">
                {matchStatus(m.status)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * A server-rendered, URL-driven overlay: `playerId` comes from `?player=`,
 * `closeHref` is the same URL with it stripped (`hrefFor(..., { player: null })`
 * — see page.tsx and market/page.tsx). No client JavaScript, so opening and
 * closing are plain navigations.
 */
export async function PlayerOverlay({
  playerId,
  closeHref,
}: {
  playerId: string;
  closeHref: string;
}) {
  const row = await playerRow(playerId);
  // A hand-edited `?player=` naming a player the store doesn't have (or one
  // that has since left the universe) must not break the page under it.
  if (row === null) return null;

  const [seasons, matches, mv, me] = await Promise.all([
    playerSeasons(playerId),
    playerMatches(playerId, 12),
    playerMv(playerId, 180),
    selfName(),
  ]);

  const pos = POSITION[row.position] ?? { short: row.position, token: "plain" };
  const spark = sparkline(mv, CHART_W, CHART_H);

  return (
    <>
      <Link href={closeHref} aria-label="Close" className="fixed inset-0 z-40 bg-black/60" />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={row.name}
        className="fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[min(48rem,92vw)] max-w-3xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-border bg-surface"
      >
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-border bg-surface px-5 py-4">
          <div className="flex min-w-0 flex-col gap-1.5">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-lg font-bold text-text">{row.name}</h2>
              <Pill tone={pos.token}>{pos.short}</Pill>
            </div>
            <div className="flex items-center gap-2 text-sm text-muted">
              <span>{row.team ?? DASH}</span>
              <OwnerBadge owner={row.owner} me={me} listed={row.listed} />
            </div>
          </div>
          <Link
            href={closeHref}
            className="shrink-0 text-sm font-semibold text-text-dim hover:text-text"
          >
            Close
          </Link>
        </div>

        <div className="flex flex-col gap-6 p-5">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-5">
            <Stat label="Market value">{money(row.market_value)}</Stat>
            <Stat label="Fair price" hint={FAIR_PRICE_HINT}>
              <FairPrice price={row.fair_price} marketValue={row.market_value} />
            </Stat>
            <Stat label="Next MV" hint={NEXT_MV_HINT}>
              <NextMvCell pct={row.next_mv_pct} change={row.next_mv_change} />
            </Stat>
            <Stat label="24h">
              <Trend value={row.trend_24h_pct} />
            </Stat>
            <Stat label="7d">
              <Trend value={row.trend_7d_pct} />
            </Stat>
            <Stat label="EP">
              <b className="text-[15px] text-text">{num(row.predicted_ep, 0)}</b>
            </Stat>
            <Stat label="P(start)">{pct(row.p_start)}</Stat>
            <Stat label="Pts / M" hint={PPM_HINT}>
              {num(row.points_per_million, 2)}
            </Stat>
            <Stat label="Played" hint={APPS_HINT}>
              {num(row.appearances)}
            </Stat>
            <Stat label="Starts" hint={STARTS_HINT}>
              {num(row.starts)}
            </Stat>
          </div>

          <div>
            <h3 className="mb-3 text-sm font-semibold uppercase tracking-[0.08em] text-muted">
              Market value, last 180 days
            </h3>
            {spark ? (
              <>
                <svg
                  role="img"
                  aria-label="Market value, last 180 days"
                  viewBox={`0 0 ${CHART_W} ${CHART_H}`}
                  className="h-28 w-full text-accent"
                >
                  <polyline points={spark.path} fill="none" stroke="currentColor" strokeWidth={2} />
                </svg>
                <div className="mt-2 flex items-center justify-between text-xs text-muted">
                  <span>
                    {spark.first.day} · {money(spark.first.market_value)}
                  </span>
                  <span>
                    {spark.last.day} · {money(spark.last.market_value)}
                  </span>
                </div>
              </>
            ) : (
              <p className="text-sm text-muted">
                Not enough market-value history to draw a line.
              </p>
            )}
          </div>

          <div>
            <h3 className="mb-3 text-sm font-semibold uppercase tracking-[0.08em] text-muted">
              Seasons
            </h3>
            <SeasonsTable seasons={seasons} />
          </div>

          <div>
            <h3 className="mb-3 text-sm font-semibold uppercase tracking-[0.08em] text-muted">
              Last matches
            </h3>
            <MatchesTable matches={matches} />
          </div>
        </div>
      </div>
    </>
  );
}

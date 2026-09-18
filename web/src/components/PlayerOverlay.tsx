import Link from "next/link";
import {
  playerMatches,
  playerMv,
  playerProfile,
  playerSeasonGrid,
  playerSeasons,
  selfName,
  type PlayerMatch,
  type PlayerSeason,
} from "@/lib/queries";
import { Pill } from "@/components/Pill";
import { FairPrice } from "@/components/FairPrice";
import { NextMvCell } from "@/components/NextMv";
import { NEXT_MV_HINT } from "@/lib/next-mv";
import { chart } from "@/lib/chart";
import { changes } from "@/lib/mv-changes";
import { RANGES, rangeDays, rangeLabel, type Range } from "@/lib/mv-range";
import { hrefFor, type Params } from "@/lib/query-href";
import { DASH, money, num, pct, signed, signedMoney, POSITION, type Tone } from "@/lib/format";

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

const FAIR_PRICE_HINT =
  "What his average points are worth at his position's going rate, from at least three appearances";
const PPM_HINT = "Season points per million euros of market value";

// The chart's own coordinate space -- the svg is scaled to fill its box by
// `viewBox` + `preserveAspectRatio="none"`, so these are arbitrary but fixed.
const CHART_W = 720;
const CHART_H = 200;

// Last changes: how many of the range's day-over-day moves to list.
const CHANGES_LIMIT = 10;

/** "2025/2026" -> "2025/26": the season label a tile prints beside its value. */
function seasonLabel(season: string): string {
  const [start, end] = season.split("/");
  return end && end.length === 4 ? `${start}/${end.slice(2)}` : season;
}

/** "2025/2026" -> "25/26": the short form the form strip uses next to each matchday. */
function shortSeason(season: string): string {
  const [start, end] = season.split("/");
  return end ? `${start.slice(-2)}/${end.slice(-2)}` : season;
}

/** A tile's number-vs-zero colour, without its sign -- `signed()` already
 * computes this; this just borrows its `tone` half. */
function toneOf(n: number | null): Tone {
  return signed(n, 0).tone;
}

function Tile({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className="flex flex-col gap-1 rounded-lg border border-border bg-surface px-4 py-3"
      title={hint}
    >
      <span className="text-[11px] uppercase tracking-[0.08em] text-muted">{label}</span>
      <div className="tnum text-lg font-semibold text-text">{children}</div>
    </div>
  );
}

function Money({ value }: { value: number | null }) {
  const out = signedMoney(value);
  return <span className={TONE[out.tone]}>{out.text}</span>;
}

/** "Overall" / "Position": a rank on its own, no denominator -- `web_player_profile`
 * carries `rank_overall`/`rank_position` but not how many players were ranked,
 * and nothing else the panel queries does either, so showing "of N" here
 * would be a number this page never actually read. */
function RankTile({ label, rank }: { label: string; rank: number | null }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-surface px-4 py-3">
      <span className="text-[11px] uppercase tracking-[0.08em] text-muted">{label}</span>
      {rank === null ? (
        <div className="flex items-baseline gap-2">
          <span className="text-lg font-semibold text-muted">{DASH}</span>
          <span className="text-xs text-muted">not ranked</span>
        </div>
      ) : (
        <span className="tnum text-lg font-semibold text-text"># {rank}</span>
      )}
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

/** A range filter link in the same look `Filters.tsx`'s chips use. */
function RangeChip({ href, active, children }: { href: string; active: boolean; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className={`inline-flex h-7 items-center rounded-[6px] px-2.5 text-xs font-medium ${
        active ? "bg-accent text-on-accent" : "bg-surface text-text-dim hover:text-text"
      }`}
    >
      {children}
    </Link>
  );
}

function FormBox({ match }: { match: PlayerMatch }) {
  return (
    <div className="flex w-20 flex-col items-center gap-1 rounded-lg border border-border bg-surface px-2 py-2">
      <span className="text-[10px] uppercase tracking-[0.06em] text-muted">MD {match.day_number}</span>
      <span className="text-[10px] text-muted">{shortSeason(match.season)}</span>
      <span className={`tnum text-sm font-semibold ${TONE[toneOf(match.points)]}`}>
        {num(match.points)}
      </span>
    </div>
  );
}

/** One matchday of a season strip -- blank (muted, no number) for a fixture
 * that hasn't kicked off yet, the same "is it in the future" test
 * `playerMatches` uses for its own played-only filter. `playerSeasonGrid`
 * doesn't carry `status`, so a null `match_at` (unparsed date text) is
 * treated as played rather than hidden -- it is the rarer case, and hiding a
 * real score behind a blank is the worse mistake of the two. */
function GridCell({ dayNumber, points, matchAt }: { dayNumber: number; points: number | null; matchAt: string | null }) {
  const future = matchAt !== null && Date.parse(matchAt) > Date.now();
  return (
    <div className="flex h-11 w-11 flex-col items-center justify-center gap-0.5 rounded-md border border-border bg-surface">
      <span className="text-[9px] uppercase tracking-[0.04em] text-muted">{dayNumber}</span>
      <span className={future ? "text-xs text-muted" : `tnum text-xs font-semibold ${TONE[toneOf(points)]}`}>
        {future ? "" : num(points)}
      </span>
    </div>
  );
}

/**
 * A server-rendered, full-screen, URL-driven overlay: `playerId` comes from
 * `?player=`, `closeHref` is the same URL with it stripped, and the chart's
 * range comes from `?mv=` inside `params` (see page.tsx and market/page.tsx
 * -- `hrefFor`, `mv-range.ts`). No client JavaScript, so opening, closing and
 * changing the range are all plain navigations.
 */
export async function PlayerOverlay({
  playerId,
  closeHref,
  params,
}: {
  playerId: string;
  closeHref: string;
  params: Params;
}) {
  const profile = await playerProfile(playerId);
  // A hand-edited `?player=` naming a player the store doesn't have (or one
  // that has since left the universe) must not break the page under it.
  if (profile === null) return null;

  const [seasons, matches, mv, me] = await Promise.all([
    playerSeasons(playerId),
    playerMatches(playerId, 5),
    playerMv(playerId, rangeDays(params.mv)),
    selfName(),
  ]);

  // Cap at the four newest seasons: `playerSeasonGrid` is one query per
  // season, and `playerSeasons` can span many years -- fourteen seasons
  // would fire fourteen extra queries for one overlay.
  const gridSeasons = seasons.slice(0, 4);
  const grids = await Promise.all(gridSeasons.map((s) => playerSeasonGrid(playerId, s.season)));

  const pos = POSITION[profile.position] ?? { short: profile.position, token: "plain" };
  // `closeHref` is `hrefFor(path, params, { player: null })` -- either
  // `path` alone (no other params survived) or `path?...` -- so splitting on
  // "?" recovers the page's own base path without a separate prop for it.
  const basePath = closeHref.split("?")[0];
  const activeRange: Range = (RANGES as readonly string[]).includes(params.mv ?? "")
    ? (params.mv as Range)
    : "3m";

  const latestSeason = seasons[0] ?? null;
  const medianLabel = latestSeason ? `Median ${seasonLabel(latestSeason.season)}` : "Median";

  const avgMinutes =
    profile.seconds_played !== null && profile.appearances !== null && profile.appearances > 0
      ? profile.seconds_played / 60 / profile.appearances
      : null;

  const out = chart(mv, CHART_W, CHART_H);
  const recentChanges = changes(mv, CHANGES_LIMIT);
  const formMatches = [...matches].reverse(); // oldest left

  return (
    <>
      <Link href={closeHref} aria-label="Close" className="fixed inset-0 z-40 bg-black/60" />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={profile.name}
        className="fixed inset-0 z-50 overflow-y-auto bg-bg"
      >
        <div className="mx-auto max-w-6xl px-6 py-6">
          <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-border bg-bg py-4">
            <div className="flex min-w-0 flex-col gap-1.5">
              <div className="flex items-center gap-2">
                <h2 className="truncate text-2xl font-bold text-text">{profile.name}</h2>
                <Pill tone={pos.token}>{pos.short}</Pill>
              </div>
              <div className="flex items-center gap-2 text-sm text-muted">
                <span>{profile.team ?? DASH}</span>
                <OwnerBadge owner={profile.owner} me={me} listed={profile.listed} />
              </div>
            </div>
            <Link
              href={closeHref}
              className="shrink-0 text-sm font-semibold text-text-dim hover:text-text"
            >
              Close
            </Link>
          </div>

          <div className="flex flex-col gap-8 py-6">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
              <Tile label="Market value">{money(profile.market_value)}</Tile>
              <Tile label="Total points">{num(profile.points)}</Tile>
              <Tile label="Ø points">{num(profile.avg_points, 1)}</Tile>
              <Tile label={medianLabel}>{num(latestSeason?.median_points ?? null, 1)}</Tile>
              <Tile label="Pts / M" hint={PPM_HINT}>
                {num(profile.points_per_million, 2)}
              </Tile>
              <Tile label="Ø previous season">{num(profile.avg_points_prev, 1)}</Tile>
              <Tile label="Trend 24 h">
                <Money value={profile.trend_24h_eur} />
              </Tile>
              <Tile label="Trend 1 week">
                <Money value={profile.trend_7d_eur} />
              </Tile>
              <Tile label="Fair value" hint={FAIR_PRICE_HINT}>
                <FairPrice price={profile.fair_price} marketValue={profile.market_value} />
              </Tile>
              <Tile label="Expected points">{num(profile.predicted_ep, 0)}</Tile>
              <Tile label="Start probability">{pct(profile.p_start)}</Tile>
              <Tile label="Next update" hint={NEXT_MV_HINT}>
                <NextMvCell pct={profile.next_mv_pct} change={profile.next_mv_change} />
              </Tile>
            </div>

            <div>
              <h3 className="mb-3 text-sm font-semibold uppercase tracking-[0.08em] text-muted">
                Ranking
              </h3>
              <div className="grid grid-cols-2 gap-3 sm:max-w-xs">
                <RankTile label="Overall" rank={profile.rank_overall} />
                <RankTile label="Position" rank={profile.rank_position} />
              </div>
            </div>

            <div>
              <h3 className="mb-3 text-sm font-semibold uppercase tracking-[0.08em] text-muted">
                Form
              </h3>
              {formMatches.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {formMatches.map((m) => (
                    <FormBox key={`${m.season}-${m.day_number}`} match={m} />
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted">No matches recorded yet.</p>
              )}
              <div className="mt-3 grid grid-cols-3 gap-3 sm:grid-cols-6">
                <Tile label="Matches">{num(profile.appearances)}</Tile>
                <Tile label="Ø minutes">{num(avgMinutes, 0)}</Tile>
                <Tile label="Goals">{num(profile.goals)}</Tile>
                <Tile label="Assists">{num(profile.assists)}</Tile>
                <Tile label="Yellow">{num(profile.yellow_cards)}</Tile>
                <Tile label="Red">{num(profile.red_cards)}</Tile>
              </div>
            </div>

            <div>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <h3 className="text-sm font-semibold uppercase tracking-[0.08em] text-muted">
                  Market value, {rangeLabel(params.mv)}
                </h3>
                <div className="flex items-center gap-1.5">
                  {RANGES.map((r) => (
                    <RangeChip
                      key={r}
                      href={hrefFor(basePath, params, { mv: r === "3m" ? null : r })}
                      active={activeRange === r}
                    >
                      {r}
                    </RangeChip>
                  ))}
                </div>
              </div>
              {out ? (
                <>
                  <svg
                    viewBox={`0 0 ${CHART_W} ${CHART_H}`}
                    preserveAspectRatio="none"
                    role="img"
                    aria-label={`Market value, ${rangeLabel(params.mv)}`}
                    className="h-48 w-full"
                  >
                    <g className="text-accent">
                      <path d={out.area} fill="currentColor" fillOpacity={0.15} stroke="none" />
                      <path
                        d={out.line}
                        fill="none"
                        stroke="currentColor"
                        strokeWidth={2}
                        vectorEffect="non-scaling-stroke"
                      />
                      <circle cx={out.high.x} cy={out.high.y} r={4} fill="currentColor" />
                      <circle cx={out.low.x} cy={out.low.y} r={4} fill="currentColor" />
                    </g>
                  </svg>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-muted sm:grid-cols-4">
                    <span>
                      High · {money(out.high.point.market_value)} · {out.high.point.day}
                    </span>
                    <span>
                      Low · {money(out.low.point.market_value)} · {out.low.point.day}
                    </span>
                    <span>
                      First · {money(out.first.market_value)} · {out.first.day}
                    </span>
                    <span>
                      Last · {money(out.last.market_value)} · {out.last.day}
                    </span>
                  </div>
                </>
              ) : (
                <p className="text-sm text-muted">
                  Not enough market-value history in this range to draw a line.
                </p>
              )}
            </div>

            <div>
              <h3 className="mb-3 text-sm font-semibold uppercase tracking-[0.08em] text-muted">
                Last changes
              </h3>
              {recentChanges.length > 0 ? (
                <div className="flex flex-col gap-1.5">
                  {recentChanges.map((c) => {
                    const m = signedMoney(c.change);
                    return (
                      <div
                        key={c.day}
                        className="flex items-center justify-between border-b border-border py-1 text-sm"
                      >
                        <span className="text-text-dim">{c.day}</span>
                        <span className={`tnum ${TONE[m.tone]}`}>{m.text}</span>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="text-sm text-muted">No market-value moves in this range.</p>
              )}
            </div>

            <div>
              <h3 className="mb-3 text-sm font-semibold uppercase tracking-[0.08em] text-muted">
                Season progress
              </h3>
              {gridSeasons.length > 0 ? (
                <div className="flex flex-col gap-4">
                  {gridSeasons.map((s: PlayerSeason, i) => (
                    <div key={s.season}>
                      <h4 className="mb-2 text-sm font-semibold text-text">{s.season}</h4>
                      <div className="flex flex-wrap gap-1.5">
                        {grids[i].map((row) => (
                          <GridCell
                            key={row.day_number}
                            dayNumber={row.day_number}
                            points={row.points}
                            matchAt={row.match_at}
                          />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted">He has not played a match in any recorded season.</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

import Link from "next/link";
import {
  playerFixtures,
  playerMatches,
  playerMv,
  playerProfile,
  selfName,
  type PlayerFixture,
} from "@/lib/queries";
import { chart } from "@/lib/chart";
import { formEntries, type FormEntry } from "@/lib/form";
import { availability } from "@/lib/availability";
import { RANGES, rangeDays, rangeLabel, type Range } from "@/lib/mv-range";
import { hrefFor, type Params } from "@/lib/query-href";
import {
  countdown,
  DASH,
  money,
  num,
  pct,
  POSITION,
  signed,
  signedMoney,
  type Tone,
} from "@/lib/format";
import { Pill } from "@/components/Pill";
import { ClubCrest, PlayerPhoto } from "@/components/PlayerPhoto";

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

/** The badge next to the name: same tone palette as everywhere else, at 15%
 * background — Tailwind's `/opacity` modifier on a theme colour token, the
 * same pattern `Pill.tsx`'s position tones already use. */
const AVAILABILITY_TONE: Record<Tone, string> = {
  positive: "bg-positive/15 text-positive",
  negative: "bg-negative/15 text-negative",
  neutral: "bg-muted/15 text-muted",
};

/** "Forward" -> "forwards" for the "Rank, <plural>" cell. Falls back to the
 * short position code lower-cased for anything not in Kickbase's four. */
const POSITION_PLURAL: Record<string, string> = {
  Goalkeeper: "goalkeepers",
  Defender: "defenders",
  Midfielder: "midfielders",
  Forward: "forwards",
};

// The chart's own coordinate space, lifted from the approved artboard's
// viewBox — arbitrary but fixed, since `preserveAspectRatio="none"` scales it
// to fill its box regardless.
const CHART_W = 632;
const CHART_H = 96;

function ordinal(n: number): string {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
  switch (n % 10) {
    case 1:
      return `${n}st`;
    case 2:
      return `${n}nd`;
    case 3:
      return `${n}rd`;
    default:
      return `${n}th`;
  }
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** "Sat 13:30 UTC" for a kickoff within the next week, "11 Oct" further out --
 * both read off the UTC clock, the same convention `format.ts`'s `ago()`
 * uses for the store's clock, and the near branch marks it explicitly the
 * same way `ago()` does. `null`/unparsed dates fall back to `DASH`, same as
 * every other missing value on this panel. */
function kickoffLabel(iso: string | null, now = Date.now()): string {
  if (iso === null) return DASH;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return DASH;
  const d = new Date(t);
  if (t - now < 7 * 24 * 3600 * 1000) {
    const hh = String(d.getUTCHours()).padStart(2, "0");
    const mm = String(d.getUTCMinutes()).padStart(2, "0");
    return `${WEEKDAYS[d.getUTCDay()]} ${hh}:${mm} UTC`;
  }
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
}

/** "21 Jun", or "today" for the store's own UTC calendar date -- `day` is a
 * plain `YYYY-MM-DD` string (see `playerMv`), so "today" is read off the UTC
 * clock too, the same convention `kickoffLabel` and `format.ts`'s `ago()`
 * use for the store's clock. Falls back to the raw string on anything that
 * doesn't parse as `YYYY-MM-DD`, rather than throwing on a stray value. */
function formatDay(day: string, now = new Date()): string {
  if (day === now.toISOString().slice(0, 10)) return "today";
  const [, monthStr, dayStr] = day.split("-");
  const month = MONTHS[Number(monthStr) - 1];
  return month ? `${Number(dayStr)} ${month}` : day;
}

/** A tile's number-vs-zero colour -- `signed()` already computes this; this
 * just borrows its `tone` half, the same trick the old overlay used. */
function toneOf(n: number | null): Tone {
  return signed(n, 0).tone;
}

function Money({ value }: { value: number | null }) {
  const out = signedMoney(value);
  return <span className={TONE[out.tone]}>{out.text}</span>;
}

/**
 * The docked panel's own frame (border, `bg-surface`, padding) with nothing
 * in it but one muted, centred line -- what stands in for the real panel
 * whenever there isn't one to show. Two callers, never left to drift apart:
 * `PlayerPanel` itself renders this when `playerId` names no player the
 * store has, and the page's own "nothing picked yet" placeholder (Players'
 * `EmptyPanel`, Market's equivalent) renders it with its own message. Same
 * frame either way, so the docked area never resizes when the panel swaps
 * for its placeholder or back.
 */
export function PanelPlaceholder({ message }: { message: string }) {
  return (
    <div className="flex min-w-0 flex-1 items-center justify-center rounded-lg border border-border bg-surface p-[18px]">
      <p className="text-sm text-muted">{message}</p>
    </div>
  );
}

/** Amber pill when the owner is us, plain "free agent" for an unowned
 * player, the owning manager's name otherwise -- the same three-way rule
 * the Players page's owner column uses. */
function OwnerLabel({ owner, listed, me }: { owner: string; listed: boolean; me: string | null }) {
  if (owner === me) return <Pill tone="accent">{owner}</Pill>;
  if (owner === "Kickbase" || owner === "market") {
    return (
      <span className="text-muted">
        free agent{owner === "market" || listed ? " - listed" : ""}
      </span>
    );
  }
  return (
    <span className="text-text-dim">
      {owner}
      {listed ? <span className="text-muted"> - listed</span> : null}
    </span>
  );
}

/** `n of total`, or `DASH` / "not ranked" when there is no rank at all. */
function RankCell({
  label,
  value,
  total,
  first,
}: {
  label: string;
  value: number | null;
  total: number | null;
  first?: boolean;
}) {
  return (
    <div
      className={`flex flex-1 flex-col gap-0.5 px-[13px] py-[9px] ${first ? "" : "border-l border-border"}`}
    >
      <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
        {label}
      </span>
      {value === null ? (
        <div className="flex items-baseline gap-2">
          <span className="tnum text-[15px] font-semibold text-muted">{DASH}</span>
          <span className="text-[12px] text-muted">not ranked</span>
        </div>
      ) : (
        <div className="flex items-baseline gap-1.5">
          <span className="tnum text-[15px] font-semibold text-text">{value}</span>
          <span className="text-[12px] text-muted">of {total ?? DASH}</span>
        </div>
      )}
    </div>
  );
}

/** "1st · 9 pts · GD +9", or the shared not-ranked state when the club has no
 * stored table row. */
function ClubRankCell({
  place,
  points,
  goalDifference,
}: {
  place: number | null;
  points: number | null;
  goalDifference: number | null;
}) {
  return (
    <div className="flex flex-1 flex-col gap-0.5 border-l border-border px-[13px] py-[9px]">
      <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
        Club in the table
      </span>
      {place === null ? (
        <div className="flex items-baseline gap-2">
          <span className="tnum text-[15px] font-semibold text-muted">{DASH}</span>
          <span className="text-[12px] text-muted">not ranked</span>
        </div>
      ) : (
        <div className="flex items-baseline gap-1.5">
          <span className="tnum text-[15px] font-semibold text-text">{ordinal(place)}</span>
          <span className="tnum text-[12px] text-muted">
            {num(points)} pts · GD {signed(goalDifference, 0).text}
          </span>
        </div>
      )}
    </div>
  );
}

function Tile({
  label,
  accent,
  children,
}: {
  label: string;
  accent?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-0.5 rounded-lg border border-border bg-bg px-3 py-2.5">
      <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
        {label}
      </span>
      <div className={`tnum text-lg font-semibold ${accent ? "text-accent" : "text-text"}`}>
        {children}
      </div>
    </div>
  );
}

/** One cell of the five-matchday form strip. `entry.day_number === 0` is
 * `formEntries`' own padding, on the right, for "we don't have this many
 * played matchdays yet" -- there is no real matchday to name, so it draws
 * with a dashed border and a dash instead of a number, the same signal the
 * artboard uses for a fixture that has not been played. A real matchday he
 * did not feature in keeps the solid border (it is a recorded result, just
 * with nothing to show) and simply omits the START/SUB role line. */
function FormCell({ entry }: { entry: FormEntry }) {
  const hasDay = entry.day_number > 0;
  const roleLabel = entry.role === "started" ? "START" : entry.role === "came on" ? "SUB" : "";
  return (
    <div
      className={`flex h-[50px] flex-1 flex-col items-center justify-center gap-px rounded-md border ${
        hasDay ? "border-border" : "border-dashed border-border"
      }`}
    >
      <span className="text-[10px] text-muted">{hasDay ? `MD ${entry.day_number}` : DASH}</span>
      <span className={`tnum text-[15px] font-semibold ${TONE[toneOf(entry.points)]}`}>
        {num(entry.points)}
      </span>
      {roleLabel ? (
        <span className="text-[9px] font-semibold uppercase tracking-[0.04em] text-muted">
          {roleLabel}
        </span>
      ) : null}
    </div>
  );
}

function FixtureRow({ fixture }: { fixture: PlayerFixture }) {
  return (
    <div className="flex items-center gap-2">
      <span className="tnum w-8 shrink-0 text-[11px] text-muted">MD {fixture.day_number}</span>
      <span
        className={`w-[14px] shrink-0 text-[11px] font-bold ${fixture.is_home ? "text-accent" : "text-muted"}`}
      >
        {fixture.is_home ? "H" : "A"}
      </span>
      <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-text">
        {fixture.opponent ?? DASH}
      </span>
      <span className="tnum shrink-0 text-[11px] text-muted">
        {fixture.opponent_place === null ? DASH : ordinal(fixture.opponent_place)}
      </span>
      <span className="tnum w-24 shrink-0 whitespace-nowrap text-right text-[11px] text-text-dim">
        {kickoffLabel(fixture.kickoff_at)}
      </span>
    </div>
  );
}

/** A range filter link in the chart header, sized to the artboard's compact
 * chips (20px tall) rather than the app's usual filter-chip height. */
function RangeChip({
  href,
  active,
  children,
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className={`inline-flex h-5 items-center rounded-[5px] px-[7px] text-[11px] font-semibold ${
        active ? "bg-accent/15 text-accent" : "text-muted hover:text-text-dim"
      }`}
    >
      {children}
    </Link>
  );
}

/**
 * One player panel, embedded (not a modal) by both Players and Market —
 * server-rendered, URL-driven, no client JavaScript. `playerId` and the
 * chart's range (`params.mv`) come from the caller's own query params;
 * `basePath` plus `params` build the Close link and the range links via
 * `hrefFor`, which is why every link on this panel keeps every other param
 * the page had. `listing` is Market's own addition (an active ask on this
 * player) — Players never passes it, so the amber strip only ever appears
 * from Market.
 *
 * Renders `PanelPlaceholder` -- not `null` -- when `playerId` names no
 * player the store has, so a hand-edited or stale `?player=` swaps the real
 * panel for a message in the same frame rather than leaving a hole beside
 * the list. (It returned `null` when this panel was a full-screen overlay,
 * where "nothing" was the correct empty state; docked beside a list, nothing
 * reads as broken layout instead.)
 */
export async function PlayerPanel({
  playerId,
  basePath,
  params,
  listing,
}: {
  playerId: string;
  basePath: string;
  params: Params;
  /** Market passes the one thing it adds; Players passes nothing. */
  listing?: { seller: string; expiresAt: number | null } | null;
}): Promise<React.ReactElement> {
  const profile = await playerProfile(playerId);
  // A hand-edited `?player=` naming a player the store doesn't have (or one
  // that has since left the universe) must not break the page under it.
  if (!profile) return <PanelPlaceholder message="That player isn't in the store." />;

  const days = rangeDays(params.mv);
  const [matches, fixtures, mv, me] = await Promise.all([
    playerMatches(playerId, 5),
    playerFixtures(playerId, 3),
    playerMv(playerId, days),
    selfName(),
  ]);

  const pos = POSITION[profile.position] ?? { short: profile.position, token: "plain" };
  const plural = POSITION_PLURAL[profile.position] ?? `${pos.short.toLowerCase()}s`;
  const avail = availability(profile.availability);
  const closeHref = hrefFor(basePath, params, { player: null });
  const activeRange: Range = (RANGES as readonly string[]).includes(params.mv ?? "")
    ? (params.mv as Range)
    : "3m";

  // Minutes come straight from seconds played; Ø minutes divides by
  // appearances and must never divide by zero or a missing count.
  const minutesTotal = profile.seconds_played !== null ? profile.seconds_played / 60 : null;
  const avgMinutes =
    profile.seconds_played !== null && profile.appearances !== null && profile.appearances > 0
      ? profile.seconds_played / 60 / profile.appearances
      : null;

  const form = formEntries(matches, 5);
  const out = chart(mv, CHART_W, CHART_H);

  const seasonStats: { label: string; value: string; tone?: string }[] = [
    { label: "Matches", value: num(profile.appearances) },
    { label: "Started", value: num(profile.starts) },
    { label: "Minutes", value: num(minutesTotal, 0) },
    { label: "Ø min", value: num(avgMinutes, 0) },
    { label: "Goals", value: num(profile.goals) },
    { label: "Assists", value: num(profile.assists) },
    { label: "Yellow", value: num(profile.yellow_cards), tone: "text-mid" },
    { label: "Red", value: num(profile.red_cards), tone: "text-negative" },
  ];

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-3 rounded-lg border border-border bg-surface p-[18px]">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3.5">
          <PlayerPhoto path={profile.image_path} name={profile.name} size={66} />
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2.5">
              <h2 className="truncate text-[22px] font-bold text-text">{profile.name}</h2>
              <span
                className={`inline-flex h-[21px] shrink-0 items-center rounded-md px-2 text-[11px] font-bold uppercase tracking-[0.04em] ${AVAILABILITY_TONE[avail.tone]}`}
              >
                {avail.label}
              </span>
            </div>
            <div className="flex items-center gap-2 text-[13px]">
              <Pill tone={pos.token}>{pos.short}</Pill>
              <ClubCrest path={profile.crest_path} size={16} />
              <span className="text-text-dim">{profile.team ?? DASH}</span>
              <span className="text-muted">·</span>
              <OwnerLabel owner={profile.owner} listed={profile.listed} me={me} />
            </div>
          </div>
        </div>
        <Link
          href={closeHref}
          aria-label="Close"
          className="shrink-0 text-xl leading-none text-muted hover:text-text-dim"
        >
          ×
        </Link>
      </div>

      {listing ? (
        <div className="rounded-lg border border-accent/30 bg-accent/10 px-[13px] py-2 text-sm font-medium text-accent">
          Listed by {listing.seller} · closes in {countdown(listing.expiresAt)}
        </div>
      ) : null}

      <div className="flex items-stretch overflow-hidden rounded-lg border border-border bg-bg">
        <RankCell
          label="Rank overall"
          value={profile.rank_overall}
          total={profile.ranked_overall_total}
          first
        />
        <RankCell
          label={`Rank, ${plural}`}
          value={profile.rank_position}
          total={profile.ranked_position_total}
        />
        <ClubRankCell
          place={profile.club_place}
          points={profile.club_points}
          goalDifference={profile.club_goal_difference}
        />
      </div>

      <div className="grid grid-cols-5 gap-2.5">
        <Tile label="Market value">{money(profile.market_value)}</Tile>
        <Tile label="Expected points" accent>
          {num(profile.predicted_ep, 0)}
        </Tile>
        <Tile label="Ø points">{num(profile.avg_points, 1)}</Tile>
        <Tile label="Points per million">{num(profile.points_per_million, 1)}</Tile>
        <Tile label="Start probability">{pct(profile.p_start)}</Tile>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-col gap-2 rounded-lg border border-border bg-bg px-[13px] py-[11px]">
            <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
              This season
            </span>
            <div className="grid grid-cols-4 gap-2">
              {seasonStats.map((s) => (
                <div key={s.label} className="flex flex-col gap-px">
                  <span className="text-[10px] text-muted">{s.label}</span>
                  <span className={`tnum text-sm font-semibold ${s.tone ?? "text-text"}`}>
                    {s.value}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-2 rounded-lg border border-border bg-bg px-[13px] py-[11px]">
            <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
              Form — last five matchdays
            </span>
            <div className="flex gap-1.5">
              {form.map((entry, i) => (
                <FormCell key={`${entry.season}-${entry.day_number}-${i}`} entry={entry} />
              ))}
            </div>
          </div>
        </div>

        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-col gap-2 rounded-lg border border-border bg-bg px-[13px] py-[11px]">
            <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
              Next three matches
            </span>
            {fixtures.length > 0 ? (
              <div className="flex flex-col gap-[7px]">
                {fixtures.map((f) => (
                  <FixtureRow key={`${f.season}-${f.day_number}`} fixture={f} />
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted">No fixtures scheduled.</p>
            )}
          </div>

          <div className="flex flex-col gap-2 rounded-lg border border-border bg-bg px-[13px] py-[11px]">
            <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
              Market value moves
            </span>
            <div className="grid grid-cols-2 gap-2">
              <div className="flex flex-col gap-px">
                <span className="text-[10px] text-muted">Last 24 h</span>
                <span className="tnum text-sm font-semibold">
                  <Money value={profile.trend_24h_eur} />
                </span>
              </div>
              <div className="flex flex-col gap-px">
                <span className="text-[10px] text-muted">Last week</span>
                <span className="tnum text-sm font-semibold">
                  <Money value={profile.trend_7d_eur} />
                </span>
              </div>
              <div className="flex flex-col gap-px">
                <span className="text-[10px] text-muted">Tonight, forecast</span>
                <span className="tnum text-sm font-semibold">
                  <Money value={profile.next_mv_change} />
                </span>
              </div>
              <div className="flex flex-col gap-px">
                <span className="text-[10px] text-muted">Fair value</span>
                <span className="tnum text-sm font-semibold text-text">
                  {money(profile.fair_price)}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-1.5 rounded-lg border border-border bg-bg px-[13px] py-[11px]">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
            Market value
          </span>
          <div className="flex items-center gap-[3px]">
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
              aria-label={`Market value over ${rangeLabel(params.mv)}`}
              className="block min-h-[56px] w-full grow"
            >
              <line
                x1={0}
                y1={CHART_H / 3}
                x2={CHART_W}
                y2={CHART_H / 3}
                className="text-border"
                stroke="currentColor"
                strokeWidth={1}
              />
              <line
                x1={0}
                y1={(2 * CHART_H) / 3}
                x2={CHART_W}
                y2={(2 * CHART_H) / 3}
                className="text-border"
                stroke="currentColor"
                strokeWidth={1}
              />
              <g className="text-accent">
                <path d={out.area} fill="currentColor" fillOpacity={0.15} stroke="none" />
                <path
                  d={out.line}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  vectorEffect="non-scaling-stroke"
                />
                <circle cx={out.high.x} cy={out.high.y} r={3.5} fill="currentColor" />
                <circle cx={out.low.x} cy={out.low.y} r={3.5} fill="currentColor" />
              </g>
            </svg>
            <div className="flex items-baseline justify-between gap-3">
              <span className="tnum text-[11px] text-muted">
                Low {money(out.low.point.market_value)} · {formatDay(out.low.point.day)}
              </span>
              <span className="tnum text-[11px] text-muted">
                High {money(out.high.point.market_value)} · {formatDay(out.high.point.day)}
              </span>
            </div>
          </>
        ) : (
          <p className="text-sm text-muted">Not enough market-value history to draw a line.</p>
        )}
      </div>
    </div>
  );
}

import Link from "next/link";
import { PLAYER_SORTS, type PlayerRow } from "@/lib/queries";
import { hrefFor, type Params } from "@/lib/query-href";
import { sortDir, sortKey } from "@/lib/sort";
import { DASH, money, num, pct, signed, signedPct, POSITION, type Tone } from "@/lib/format";
import { availability } from "@/lib/availability";
import { ClubCrest, PlayerPhoto } from "@/components/PlayerPhoto";

/** Every key `PLAYER_SORTS` can carry, as a literal union rather than plain
 * `string` -- what lets `SORT_LABEL` and `rankedFigureText` below be checked
 * against it exhaustively, so a key added to `PLAYER_SORTS` without a label
 * or a figure formatter fails the build instead of silently rendering a
 * blank list header and no figure (REH: fair_value_gap review round 1). */
type SortKey = (typeof PLAYER_SORTS)[number];

/** Human wording for every `PLAYER_SORTS` key -- the same phrase serves the
 * "Ranked by …" sentence and each sort link below it, so the two can never
 * say different things about the same key. `Record<SortKey, string>` (not
 * `Record<string, string>`) means TypeScript rejects this object if it is
 * missing a key `PLAYER_SORTS` has, or carries one it doesn't. */
const SORT_LABEL: Record<SortKey, string> = {
  name: "name",
  position: "position",
  market_value: "market value",
  trend_24h_pct: "24h change",
  trend_7d_pct: "7d change",
  next_mv_pct: "next MV change",
  fair_price: "fair price",
  fair_value_gap: "fair value gap",
  points: "points",
  avg_points: "average points",
  median_points: "median points",
  points_per_million: "points per million",
  appearances: "appearances",
  starts: "starts",
  owner: "owner",
  predicted_ep: "expected points",
  p_start: "start probability",
};

const RULE_COLOR: Record<string, string> = {
  gk: "bg-gk",
  def: "bg-def",
  mid: "bg-mid",
  fw: "bg-fw",
};

const LABEL_COLOR: Record<string, string> = {
  gk: "text-gk",
  def: "text-def",
  mid: "text-mid",
  fw: "text-fw",
};

/** The fitness dot beside each row's name -- same tone palette as
 * everywhere else, solid background rather than the panel badge's 15%
 * tint, since a 6 px dot has no room for a label to make the tint legible. */
const DOT_TONE: Record<Tone, string> = {
  positive: "bg-positive",
  negative: "bg-negative",
  neutral: "bg-muted",
};

/** A new sort starts again at page 1 -- `hrefFor` already drops `page` for
 * every link, the same rule `DataTable`'s own sort links followed. Clicking
 * the active key flips its direction; clicking a different one starts it
 * at desc, same toggle `DataTable` used. */
function sortHref(basePath: string, params: Params, sort: SortKey, dir: "asc" | "desc", key: SortKey) {
  return hrefFor(basePath, params, {
    sort: key,
    dir: key === sort && dir === "desc" ? "asc" : "desc",
  });
}

/**
 * The figure shown above a row's market value -- whatever `sort` currently
 * ranks by, formatted the way that column was formatted in the old table.
 * `name`/`position`/`owner` carry no magnitude worth ranking a number by --
 * the row's own name/position/club text already says it -- so those three
 * show no top figure, just the market value.
 *
 * The switch is exhaustive over `SortKey`: the `default` branch assigns
 * `sort` to a `never`-typed binding, so a `PLAYER_SORTS` key added without a
 * `case` here fails the build rather than silently falling through to null.
 */
function rankedFigureText(row: PlayerRow, sort: SortKey): string | null {
  switch (sort) {
    case "market_value":
      return money(row.market_value);
    case "fair_price":
      return money(row.fair_price);
    case "fair_value_gap":
      return signed(row.fair_value_gap, 1).text;
    case "trend_24h_pct":
      return signedPct(row.trend_24h_pct).text;
    case "trend_7d_pct":
      return signedPct(row.trend_7d_pct).text;
    case "next_mv_pct":
      return signedPct(row.next_mv_pct).text;
    case "points":
      return num(row.points, 0);
    case "avg_points":
      return num(row.avg_points, 1);
    case "median_points":
      return num(row.median_points, 1);
    case "points_per_million":
      return num(row.points_per_million, 1);
    case "appearances":
      return num(row.appearances, 0);
    case "starts":
      return num(row.starts, 0);
    case "p_start":
      return pct(row.p_start);
    case "predicted_ep":
      return num(row.predicted_ep, 0);
    case "name":
    case "position":
    case "owner":
      return null;
    default: {
      const exhaustive: never = sort;
      return exhaustive;
    }
  }
}

function Row({
  row,
  isSelected,
  basePath,
  params,
  sort,
}: {
  row: PlayerRow;
  isSelected: boolean;
  basePath: string;
  params: Params;
  sort: SortKey;
}) {
  const pos = POSITION[row.position] ?? { short: row.position, token: "plain" };
  const ruleClass = isSelected ? "bg-accent" : (RULE_COLOR[pos.token] ?? "bg-border-strong");
  const labelClass = LABEL_COLOR[pos.token] ?? "text-muted";
  const figure = rankedFigureText(row, sort);
  const avail = availability(row.availability);
  return (
    <Link
      href={hrefFor(basePath, params, { player: row.player_id })}
      className={`flex h-14 items-center gap-3 border-b border-border pr-3.5 ${
        isSelected ? "bg-accent/8" : ""
      }`}
    >
      <span className={`h-14 w-[3px] shrink-0 ${ruleClass}`} />
      <PlayerPhoto path={row.image_path} name={row.name} size={34} />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex min-w-0 items-center gap-1.5">
          <span
            className={`truncate text-[15px] font-semibold ${isSelected ? "text-text" : "text-text-dim"}`}
          >
            {row.name}
          </span>
          <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT_TONE[avail.tone]}`} />
        </div>
        <div className="flex min-w-0 items-center gap-1.5">
          <span className={`text-[11px] font-semibold ${labelClass}`}>{pos.short}</span>
          <ClubCrest path={row.crest_path} size={14} />
          <span className="truncate text-xs text-muted">{row.team ?? DASH}</span>
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-0.5">
        {figure !== null ? (
          <span
            className={`tnum text-[17px] font-semibold ${isSelected ? "text-accent" : "text-text"}`}
          >
            {figure}
          </span>
        ) : null}
        <span className="tnum text-xs text-muted">{money(row.market_value)}</span>
      </div>
    </Link>
  );
}

/**
 * The ranked list Players shows beside `PlayerPanel` -- server-rendered, no
 * client JavaScript, every row a `<Link>` that sets `?player=`. `sort`/`dir`
 * are read back off `params` with the same allow-list and fallback the page
 * used to build `rows` in the first place, so the header and the row order
 * can never disagree about what "ranked by" means.
 *
 * The sort links here are this component's own addition, not lifted from the
 * artboard (which shows one static "Ranked by expected points" line): the
 * old table gave every `PLAYER_SORTS` key a working sort link, and this list
 * replaces that table, so it must keep every one of them working too.
 */
export function PlayerList({
  rows,
  selected,
  basePath,
  params,
}: {
  rows: PlayerRow[];
  selected: string | undefined;
  basePath: string;
  params: Params;
}) {
  const sort = sortKey(params.sort, PLAYER_SORTS, "predicted_ep");
  const dir = sortDir(params.dir);

  return (
    <div className="flex min-h-0 w-full flex-col xl:w-[400px] xl:shrink-0">
      <div className="flex flex-col gap-1.5 px-0.5 pb-2.5">
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
          Ranked by {SORT_LABEL[sort]}
        </span>
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {PLAYER_SORTS.map((key) => (
            <Link
              key={key}
              href={sortHref(basePath, params, sort, dir, key)}
              className={`text-[11px] font-medium ${
                key === sort ? "text-accent" : "text-muted hover:text-text-dim"
              }`}
            >
              {SORT_LABEL[key]}
              {key === sort ? (dir === "desc" ? " ▾" : " ▴") : ""}
            </Link>
          ))}
        </div>
      </div>
      <div className="flex flex-col overflow-hidden rounded-lg border border-border bg-surface">
        {rows.map((row) => (
          <Row
            key={row.player_id}
            row={row}
            isSelected={row.player_id === selected}
            basePath={basePath}
            params={params}
            sort={sort}
          />
        ))}
        {rows.length === 0 ? (
          <p className="p-6 text-center text-sm text-muted">Nothing here yet.</p>
        ) : null}
      </div>
    </div>
  );
}

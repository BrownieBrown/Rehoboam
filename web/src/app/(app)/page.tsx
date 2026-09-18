import { requireSession } from "@/lib/auth";
import Link from "next/link";
import { clubs, playerCount, players, PLAYER_SORTS, type PlayerFilter } from "@/lib/queries";
import { sortDir, sortKey } from "@/lib/sort";
import { clampPage, pageHref, pageOffset, pageSummary, parsePage } from "@/lib/paging";
import { Filters } from "@/components/Filters";
import { PlayerList } from "@/components/PlayerList";
import { PlayerPanel } from "@/components/PlayerPanel";
import { StatusHeader } from "@/components/StatusHeader";
import { noForecastNote } from "@/lib/next-mv";

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

/** What the docked panel shows before a player is picked -- matches
 * `PlayerPanel`'s own frame (border, surface, padding) so swapping between
 * the two never jumps the layout. */
function EmptyPanel() {
  return (
    <div className="flex min-w-0 flex-1 items-center justify-center rounded-lg border border-border bg-surface p-[18px]">
      <p className="text-sm text-muted">Pick a player to see everything we know about him.</p>
    </div>
  );
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

  const [firstTry, clubList] = await Promise.all([
    players({ ...filter, sort, dir, offset: pageOffset(requested) }),
    clubs(),
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
      <div className="flex min-h-0 flex-1 flex-col gap-6 px-6 pb-6 xl:flex-row">
        <PlayerList rows={rows} selected={params.player} basePath="/" params={params} />
        {params.player ? (
          <PlayerPanel playerId={params.player} basePath="/" params={params} />
        ) : (
          <EmptyPanel />
        )}
      </div>
    </>
  );
}

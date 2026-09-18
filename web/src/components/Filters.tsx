import Link from "next/link";
import { POSITION } from "@/lib/format";
import { hrefFor, type Params } from "@/lib/query-href";

/** Every Players filter link keeps the rest of the current state. */
function playersHref(params: Params, overrides: Record<string, string | null>): string {
  return hrefFor("/", params, overrides);
}

function Chip({
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
      className={`inline-flex h-7 items-center rounded-[6px] px-2.5 text-xs font-medium ${
        active ? "bg-accent text-on-accent" : "bg-surface text-text-dim hover:text-text"
      }`}
    >
      {children}
    </Link>
  );
}

/**
 * Server component, no client JavaScript. Position and ownership are single-
 * value choices, so each option is a plain link that sets or clears exactly
 * one search param while carrying the rest along (`playersHref`). Club and free
 * text need a value the URL bar can't offer as a click target, so those two
 * live in a GET form instead - its hidden inputs carry every other filter
 * that's currently active, so submitting the form doesn't clobber a chip
 * chosen a moment ago.
 */
export function Filters({ clubs, params }: { clubs: string[]; params: Params }) {
  const position = params.position;
  const owner = params.owner;

  return (
    <div className="flex flex-wrap items-center gap-4 border-b border-border px-6 py-3">
      <div className="flex items-center gap-1.5">
        <Chip href={playersHref(params, { position: null })} active={!position}>
          All
        </Chip>
        {Object.entries(POSITION).map(([full, meta]) => (
          <Chip key={full} href={playersHref(params, { position: full })} active={position === full}>
            {meta.short}
          </Chip>
        ))}
      </div>

      <div className="flex items-center gap-1.5">
        <Chip href={playersHref(params, { owner: null })} active={!owner}>
          Everyone
        </Chip>
        <Chip href={playersHref(params, { owner: "mine" })} active={owner === "mine"}>
          My squad
        </Chip>
        <Chip href={playersHref(params, { owner: "free" })} active={owner === "free"}>
          Free agents
        </Chip>
      </div>

      <form method="get" action="/" className="flex items-center gap-2">
        {/* Carries the rest of the current filter state through a plain GET
            submit, so picking a club or typing a search term keeps the
            active position/ownership chips and the current sort. */}
        <input type="hidden" name="sort" value={params.sort ?? ""} />
        <input type="hidden" name="dir" value={params.dir ?? ""} />
        {position ? <input type="hidden" name="position" value={position} /> : null}
        {owner ? <input type="hidden" name="owner" value={owner} /> : null}
        <select
          name="club"
          defaultValue={params.club ?? ""}
          className="h-8 rounded-[6px] border border-border-strong bg-bg px-2 text-xs text-text-dim outline-none focus:border-accent"
        >
          <option value="">All clubs</option>
          {clubs.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <input
          type="text"
          name="q"
          defaultValue={params.q ?? ""}
          placeholder="Search player or club"
          className="h-8 w-52 rounded-[6px] border border-border-strong bg-bg px-2 text-xs text-text placeholder:text-muted outline-none focus:border-accent"
        />
        <button
          type="submit"
          className="h-8 rounded-[6px] bg-accent px-3 text-xs font-semibold text-on-accent"
        >
          Filter
        </button>
      </form>
    </div>
  );
}

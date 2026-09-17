"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { countdown, money } from "@/lib/format";
import type { ShellFacts } from "@/lib/queries";

const ENTRIES: { href: string; label: string }[] = [
  { href: "/", label: "Players" },
  { href: "/squad", label: "Squad & lineup" },
  { href: "/market", label: "Market" },
  { href: "/health", label: "Calibration & health" },
];

function humanizeLineup(result: string | null): string {
  if (!result) return "not attempted";
  return result.replace("_", " ");
}

/**
 * The `(app)` layout renders one Sidebar beside all four data pages without
 * knowing which — there is no server-side pathname in a shared layout short
 * of a middleware header this task doesn't own — so `current` is an optional
 * override and, absent one, this is the one piece of the shell that reads
 * the route itself via `usePathname`. Everything else here is static markup
 * built from `facts`, already fetched server-side by the layout.
 */
export function Sidebar({ facts, current }: { facts: ShellFacts; current?: string }) {
  const pathname = usePathname();
  const active = current ?? pathname;

  return (
    <aside className="flex w-56 shrink-0 flex-col justify-between border-r border-border bg-sidebar px-3 py-4">
      <div>
        <div className="mb-4 px-2 text-sm font-bold tracking-wide text-text">Rehoboam</div>
        <nav className="flex flex-col gap-1">
          {ENTRIES.map((e) => {
            const isActive = e.href === "/" ? active === "/" : active?.startsWith(e.href);
            return (
              <Link
                key={e.href}
                href={e.href}
                className={`h-9 rounded-[6px] px-2.5 text-sm font-medium leading-9 ${
                  isActive ? "bg-accent/15 text-accent" : "text-text-dim hover:text-text"
                }`}
              >
                {e.label}
              </Link>
            );
          })}
        </nav>
      </div>

      <div className="rounded-lg border border-border bg-surface p-3 text-xs">
        <dl className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            <dt className="text-muted">Next kickoff</dt>
            <dd className="tnum text-text-dim">{countdown(facts.nextKickoff)}</dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-muted">Lineup</dt>
            <dd className="text-text-dim">{humanizeLineup(facts.lineupResult)}</dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-muted">Budget</dt>
            <dd className="tnum text-text-dim">{money(facts.budget)}</dd>
          </div>
        </dl>
        {facts.lastSessionAt !== null && facts.stale ? (
          <p className="mt-2 text-negative">Session data is over 14 h old.</p>
        ) : null}
      </div>
    </aside>
  );
}

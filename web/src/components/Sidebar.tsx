"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ShellFacts } from "@/lib/queries";

const ENTRIES: { href: string; label: string }[] = [
  { href: "/", label: "Players" },
  { href: "/squad", label: "Squad & lineup" },
  { href: "/market", label: "Market" },
  { href: "/health", label: "Calibration & health" },
];

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

      {facts.lastSessionAt !== null && facts.stale ? (
        <p className="px-1 text-xs text-negative">Session data is over 14 h old.</p>
      ) : null}
    </aside>
  );
}

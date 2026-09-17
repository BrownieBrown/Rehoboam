import type { ReactNode } from "react";
import { ago, DASH } from "@/lib/format";
import { shellFacts } from "@/lib/queries";

/**
 * Labels the countdown only — the Function timer itself runs at 08:00 and
 * 20:00 UTC (`deploy/azure_function/function_app.py`'s cron "0 0 8,20 * * *").
 * If that schedule ever changes, this label goes stale with it; it does not
 * read the schedule back from anywhere live.
 */
function nextSessionLabel(now: Date = new Date()): string {
  const hour = now.getUTCHours();
  const next = hour < 8 ? 8 : hour < 20 ? 20 : 8;
  return `${String(next).padStart(2, "0")}:00 UTC`;
}

/** The default count line every page gets when it doesn't pass its own `subtitle`. */
async function defaultSubtitle(): Promise<string> {
  const facts = await shellFacts();
  const players = facts.players !== null ? `${facts.players} players` : "player count unknown";
  // The start of the newest live trading session, not the store's last write:
  // the ingestion app also writes at 05:00 and 17:00 UTC. A dash when there is
  // no such session or the store did not answer; `shellFacts` cannot tell the
  // two apart. ago() also names the distance ("3 h ago"); the header only
  // wants the clock time, and the sidebar warns when the session is stale.
  const last =
    facts.lastSessionAt !== null ? ago(facts.lastSessionAt).split(" · ")[0] : DASH;
  return `${players} · last session ${last} · next session ${nextSessionLabel()}`;
}

/** The title, the count line (or a page's own `subtitle`), and an optional right slot. */
export async function StatusHeader({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle?: string;
  right?: ReactNode;
}) {
  const line = subtitle ?? (await defaultSubtitle());
  return (
    <header className="flex items-center justify-between gap-6 border-b border-border px-6 py-5">
      <div className="min-w-0">
        <h1 className="text-lg font-bold text-text">{title}</h1>
        <p className="mt-1 truncate text-sm text-muted">{line}</p>
      </div>
      {right ? (
        <div className="flex shrink-0 items-center gap-5 text-sm text-text-dim">{right}</div>
      ) : null}
    </header>
  );
}

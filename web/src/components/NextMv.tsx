import { DASH, type Tone } from "@/lib/format";
import { nextMv } from "@/lib/next-mv";

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

/** Percent over euros, or a dash when there is no forecast for the next update. */
export function NextMvCell({ pct, change }: { pct: number | null; change: number | null }) {
  const out = nextMv(pct, change);
  if (!out) return <span className="text-muted">{DASH}</span>;
  return (
    <div className="flex flex-col items-end gap-0.5">
      <span className={TONE[out.tone]}>{out.pct}</span>
      <span className="text-xs text-muted">{out.change}</span>
    </div>
  );
}

import { DASH, type Tone } from "@/lib/format";
import { nextMv } from "@/lib/next-mv";

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

/** Percent over euros, or a dash when there is no forecast for the next update. */
export function NextMvCell({
  pct,
  change,
  align = "right",
}: {
  pct: number | null;
  change: number | null;
  /** Table cells (the default) right-align; the player panel's tile grid
   * passes "left" so this cell reads like its plain-number neighbours. */
  align?: "left" | "right";
}) {
  const out = nextMv(pct, change);
  if (!out) return <span className="text-muted">{DASH}</span>;
  return (
    <div className={`flex flex-col gap-0.5 ${align === "left" ? "items-start" : "items-end"}`}>
      <span className={TONE[out.tone]}>{out.pct}</span>
      <span className="text-xs text-muted">{out.change}</span>
    </div>
  );
}

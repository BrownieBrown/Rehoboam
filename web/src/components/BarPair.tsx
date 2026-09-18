import { barWidths, compare, OUTCOME_TEXT, type Better } from "@/lib/calibration";
import { num, signed } from "@/lib/format";

const VERDICT_TONE: Record<string, string> = {
  beats: "text-positive",
  loses: "text-negative",
  ties: "text-muted",
  unknown: "text-muted",
};

const BAR_TONE: Record<string, string> = {
  beats: "bg-positive",
  loses: "bg-negative",
  ties: "bg-muted",
  unknown: "bg-muted",
};

/**
 * Ours against the baseline on one scale. `betterIs` decides which side reads
 * as the winner; `compare`/`barWidths` (web/src/lib/calibration.ts) decide the
 * verdict text and the bar widths, so a tie can never render as a loss and a
 * negative value can never draw a bar as long as the equivalent positive one.
 *
 * `signedValue` puts the U+2212/"+"-signed text beside the bar instead of a
 * bare magnitude — for Spearman, which can fall below zero, the sign is the
 * only thing that still shows a negative value once its bar has gone empty.
 * Top-eleven regret never goes negative, so it stays a bare magnitude.
 */
export function BarPair({
  label,
  ours,
  baseline,
  betterIs,
  digits = 2,
  signedValue = false,
}: {
  label: string;
  ours: number | null;
  baseline: number | null;
  betterIs: Better;
  digits?: number;
  signedValue?: boolean;
}) {
  if (ours === null || baseline === null) {
    return (
      <div className="flex flex-col gap-1">
        <span className="text-xs text-muted">{label}</span>
        <span className="text-sm text-muted">not yet reported</span>
      </div>
    );
  }
  const outcome = compare(ours, baseline, betterIs);
  const widths = barWidths(ours, baseline);
  const format = (value: number) => (signedValue ? signed(value, digits).text : num(value, digits));
  const bar = (width: number, value: number, tone: string) => (
    <div className="flex items-center gap-2">
      <div className="h-2 flex-1 overflow-hidden rounded-sm bg-border">
        <div className={`h-full ${tone}`} style={{ width: `${width * 100}%` }} />
      </div>
      <span className="tnum w-16 text-right text-xs text-text-dim">{format(value)}</span>
    </div>
  );
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-muted">{label}</span>
        <span className={`text-xs font-semibold ${VERDICT_TONE[outcome]}`}>
          {OUTCOME_TEXT[outcome]}
        </span>
      </div>
      {bar(widths.ours, ours, BAR_TONE[outcome])}
      {bar(widths.baseline, baseline, "bg-muted")}
    </div>
  );
}

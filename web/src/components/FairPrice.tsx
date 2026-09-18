import { DASH, money, signedMoney, type Tone } from "@/lib/format";

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

/**
 * The fair price over its distance from today's market value: a negative gap
 * means the market asks more than his scoring is worth at his position's rate.
 * Both halves come from the same two numbers, so they can never disagree.
 */
export function FairPrice({
  price,
  marketValue,
  align = "right",
}: {
  price: number | null;
  marketValue: number | null;
  /** Table cells (the default) right-align; the player panel's tile grid
   * passes "left" so this cell reads like its plain-number neighbours. */
  align?: "left" | "right";
}) {
  if (price === null) return <span className="text-muted">{DASH}</span>;
  const gap = marketValue === null ? null : signedMoney(price - marketValue);
  return (
    <div className={`flex flex-col gap-0.5 ${align === "left" ? "items-start" : "items-end"}`}>
      <span className="text-text">{money(price)}</span>
      {gap ? <span className={`text-xs ${TONE[gap.tone]}`}>{gap.text}</span> : null}
    </div>
  );
}

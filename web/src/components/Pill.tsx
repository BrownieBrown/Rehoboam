const TONES: Record<string, string> = {
  accent: "bg-accent text-on-accent",
  muted: "text-muted",
  plain: "text-text-dim",
  gk: "bg-gk/15 text-gk",
  def: "bg-def/15 text-def",
  mid: "bg-mid/15 text-mid",
  fw: "bg-fw/15 text-fw",
};

export function Pill({ tone = "plain", children }: { tone?: string; children: React.ReactNode }) {
  const style = TONES[tone] ?? TONES.plain;
  // 6 px radius on chips (the visual system's rule) — not the brief's literal
  // 4px, which would be the only chip in the app off the shared radius scale.
  const box = tone === "muted" || tone === "plain" ? "" : "px-2 rounded-[6px]";
  return (
    <span className={`inline-flex h-[22px] items-center text-xs font-semibold ${box} ${style}`}>
      {children}
    </span>
  );
}

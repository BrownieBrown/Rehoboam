import { requireSession } from "@/lib/auth";
import { squad, splitSquad, latestSessionRules, type SquadRow } from "@/lib/queries";
import { DataTable, type Column } from "@/components/DataTable";
import { Formation } from "@/components/Formation";
import { Pill } from "@/components/Pill";
import { StatusHeader } from "@/components/StatusHeader";
import { DASH, money, num, pct, signedMoney, POSITION, type Tone } from "@/lib/format";
import { integritySentence } from "@/lib/integrity";

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

/** One line per rule the newest real session raised. Nothing when it raised none. */
function IntegrityBlock({ rules }: { rules: { rule: string; detail: string }[] }) {
  if (rules.length === 0) return null;
  return (
    <div className="mx-6 mb-6 flex flex-col gap-1 rounded-lg border border-border bg-surface p-4 text-sm text-text-dim">
      {rules.map((r) => (
        <p key={r.rule}>&bull; {integritySentence(r.rule, r.detail)}</p>
      ))}
    </div>
  );
}

export default async function SquadPage() {
  await requireSession();
  // Both queries agree on "newest real session" independently (each filters
  // app = 'function' and dry_run = 0 itself), so they can run in parallel
  // without risking a header/table mismatch across two different sessions.
  const [rows, rules] = await Promise.all([squad(), latestSessionRules()]);

  // Case 1 of 3: no non-dry-run session has ever run. There is no session
  // row at all, so there is nothing to put in the header's right slot either.
  if (rows.length === 0) {
    return (
      <>
        <StatusHeader title="Squad & lineup" />
        <div className="px-6 pb-6">
          <p className="text-sm text-muted">No completed session has recorded a squad yet.</p>
        </div>
      </>
    );
  }

  const { session, players } = splitSquad(rows);

  const header = (
    <StatusHeader
      title="Squad & lineup"
      right={
        <>
          <span>
            Budget <b className="tnum text-text">{money(session?.budget ?? null)}</b>
          </span>
          <span>
            Sellable <b className="tnum text-text">{money(session?.sellable_value ?? null)}</b>
          </span>
        </>
      }
    />
  );

  // Case 2 of 3: the session ran and recorded its budget and formation, but
  // its roster write failed (or legitimately owned nothing) - `web_squad`'s
  // left join surfaces that as one row whose player_id is null. The session
  // facts (and any integrity rules it raised) are still real, so the header
  // and the integrity block render normally; only the eleven and the table
  // are missing.
  if (players.length === 0) {
    return (
      <>
        {header}
        <IntegrityBlock rules={rules} />
        <div className="px-6 pb-6">
          <p className="text-sm text-muted">
            The last session recorded no squad. Its predictions write failed or the squad was
            empty.
          </p>
        </div>
      </>
    );
  }

  // Case 3 of 3: a real roster. Not user-sortable - squad() always orders by
  // in_best_11 then predicted_ep, so every column header renders as a plain
  // label (sort="" matches no column key, and none is declared sortable).
  const columns: Column<SquadRow>[] = [
    {
      key: "name",
      label: "Player",
      align: "left",
      sortable: false,
      cell: (p) => (
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-semibold text-text">{p.name}</span>
          <span className="text-xs text-muted">{p.team ?? DASH}</span>
        </div>
      ),
    },
    {
      key: "position",
      label: "Pos",
      align: "left",
      sortable: false,
      cell: (p) => {
        const pos = POSITION[p.position ?? ""] ?? { short: p.position ?? DASH, token: "plain" };
        return <Pill tone={pos.token}>{pos.short}</Pill>;
      },
    },
    {
      key: "predicted_ep",
      label: "EP",
      sortable: false,
      cell: (p) => <b className="text-[15px] text-text">{num(p.predicted_ep, 0)}</b>,
    },
    {
      key: "p_start",
      label: "P(start)",
      sortable: false,
      cell: (p) => pct(p.p_start),
    },
    {
      key: "market_value",
      label: "Market value",
      sortable: false,
      cell: (p) => money(p.market_value),
    },
    // money()/signed() already render an em dash for null - never pass a
    // fallback of 0 here, or a never-purchased player reads as "bought free".
    { key: "cost_basis", label: "Cost basis", sortable: false, cell: (p) => money(p.cost_basis) },
    {
      key: "gain_loss",
      label: "Gain/loss",
      sortable: false,
      cell: (p) => {
        const out = signedMoney(p.gain_loss);
        return <span className={TONE[out.tone]}>{out.text}</span>;
      },
    },
    { key: "points", label: "Pts", sortable: false, cell: (p) => num(p.points) },
    { key: "avg_points", label: "Avg", sortable: false, cell: (p) => num(p.avg_points, 1) },
    {
      key: "in_best_11",
      label: "In XI",
      sortable: false,
      cell: (p) => (p.in_best_11 ? "yes" : DASH),
    },
  ];

  return (
    <>
      {header}
      <div className="flex flex-col gap-6 px-6 pb-6">
        <Formation
          formation={session?.legal_formation ?? null}
          eleven={players.filter((p) => p.in_best_11)}
        />
        <IntegrityBlock rules={rules} />
        <DataTable
          columns={columns}
          rows={players}
          sort=""
          dir="desc"
          basePath="/squad"
          rowClass={(p) => (p.in_best_11 ? "" : "opacity-60")}
        />
      </div>
    </>
  );
}

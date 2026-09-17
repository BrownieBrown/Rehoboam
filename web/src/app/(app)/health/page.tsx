import { requireSession } from "@/lib/auth";
import {
  calibration,
  mvAccuracy,
  playerNames,
  sessions,
  type CalibrationRow,
  type MvAccuracyRow,
  type SessionRow,
} from "@/lib/queries";
import { DataTable, type Column } from "@/components/DataTable";
import { BarPair } from "@/components/BarPair";
import { StatusHeader } from "@/components/StatusHeader";
import { ago, DASH, money, num, signed } from "@/lib/format";
import { integritySentence } from "@/lib/integrity";
import { emptyReportSentence, gateSentence, type Gate } from "@/lib/calibration";
import { accuracySentence, directionRight, summarize } from "@/lib/mv-accuracy";

/** Newest matchday first; within a matchday, live before backfill (the order `calibration()` already returns). */
function byMatchday(rows: CalibrationRow[]): CalibrationRow[][] {
  const groups = new Map<string, CalibrationRow[]>();
  for (const row of rows) {
    const key = `${row.season}::${row.day_number}`;
    const group = groups.get(key);
    if (group) group.push(row);
    else groups.set(key, [row]);
  }
  return [...groups.values()].sort((a, b) => {
    const seasonCmp = b[0].season.localeCompare(a[0].season);
    return seasonCmp !== 0 ? seasonCmp : b[0].day_number - a[0].day_number;
  });
}

/** One report row inside a matchday card: a live report or a leak-free backfill, never hiding one behind the other. */
function ReportRow({
  row,
  names,
}: {
  row: CalibrationRow;
  names: Record<string, string>;
}) {
  const label = row.backfill ? "Leak-free backfill" : "Live";
  if (row.n === 0) {
    // An empty report has two distinct causes (see emptyReportSentence's doc
    // comment): no predictions existed yet, or predictions existed but every
    // row was stale. Only the second still carries a real gate verdict for a
    // live row - render it too, rather than let the empty-report sentence
    // hide it.
    const gate = !row.backfill ? (row.gate as Gate | null) : null;
    return (
      <div className="flex flex-col gap-2 border-t border-border pt-4 first:border-t-0 first:pt-0">
        <span className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">
          {label}
        </span>
        <p className="text-sm text-muted">{emptyReportSentence(row)}</p>
        {gate !== null ? <p className="text-sm text-text-dim">{gateSentence(gate)}</p> : null}
      </div>
    );
  }
  const bias = signed(row.bias, 2);
  return (
    <div className="flex flex-col gap-4 border-t border-border pt-4 first:border-t-0 first:pt-0">
      <span className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">{label}</span>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <BarPair
          label="Spearman"
          ours={row.spearman}
          baseline={row.baseline_spearman}
          betterIs="higher"
          digits={2}
          signedValue
        />
        <BarPair
          label="Top-eleven regret"
          ours={row.top11_regret}
          baseline={row.baseline_top11_regret}
          betterIs="lower"
          digits={0}
        />
      </div>
      <p className="text-sm text-text-dim">
        n = {num(row.n)} · {num(row.n_stale_rows)} stale rows excluded · MAE {num(row.mae, 1)} ·
        bias {bias.text}
      </p>
      {row.backfill ? (
        <p className="text-sm text-muted">
          Backfill reports carry no gate verdict — only a live report does.
        </p>
      ) : (
        <p className="text-sm text-text-dim">{gateSentence(row.gate as Gate | null)}</p>
      )}
      {row.worst.length > 0 ? (
        <ul className="flex flex-col gap-1 text-sm text-text-dim">
          {row.worst.map((w) => (
            <li key={w.player_id}>
              {names[w.player_id] ?? w.player_id} ({w.position}) — predicted{" "}
              {num(w.predicted, 0)}, scored {num(w.actual, 0)}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** Trading-session result: the lineup outcome. Ingest result: how far the sweep got. */
function ResultCell({ row }: { row: SessionRow }) {
  if (row.mode === "ingest") {
    const stoppedByDeadline = row.stopped_by === "deadline";
    return (
      <span className="text-sm text-text-dim">
        {num(row.status_written)} / {num(row.universe_size)}
        {row.stopped_by ? (
          <>
            {" "}
            <span className={stoppedByDeadline ? "text-negative" : "text-muted"}>
              {row.stopped_by}
            </span>
          </>
        ) : null}
      </span>
    );
  }
  return <span className="text-sm text-text-dim">{row.lineup_result ?? DASH}</span>;
}

export default async function HealthPage() {
  await requireSession();

  const [calibrationRows, sessionRows, mvRows] = await Promise.all([
    calibration(),
    sessions(30),
    mvAccuracy(),
  ]);
  const ids = [...new Set(calibrationRows.flatMap((r) => r.worst.map((w) => w.player_id)))];
  const names = await playerNames(ids);
  const matchdays = byMatchday(calibrationRows);

  const columns: Column<SessionRow>[] = [
    { key: "started_at", label: "Time", align: "left", sortable: false, cell: (r) => ago(r.started_at) },
    {
      key: "app",
      label: "App",
      align: "left",
      sortable: false,
      cell: (r) => (
        <div className="flex flex-col gap-0.5">
          <span className="text-sm text-text">
            {r.app} · {r.mode}
          </span>
          {r.dry_run === 1 ? <span className="text-xs text-muted">dry run</span> : null}
        </div>
      ),
    },
    { key: "duration_s", label: "Duration", sortable: false, cell: (r) => `${num(r.duration_s, 0)} s` },
    { key: "result", label: "Result", align: "left", sortable: false, cell: (r) => <ResultCell row={r} /> },
    { key: "requests", label: "Requests", sortable: false, cell: (r) => num(r.requests) },
    {
      key: "league",
      label: "League",
      sortable: false,
      cell: (r) =>
        r.league_teams !== null && r.league_fixtures !== null
          ? `${num(r.league_teams)}/${num(r.league_fixtures)}`
          : DASH,
    },
    {
      key: "errors",
      label: "Errors",
      sortable: false,
      cell: (r) => <span className={r.errors > 0 ? "text-negative" : ""}>{num(r.errors)}</span>,
    },
    {
      key: "integrity",
      label: "Integrity",
      align: "left",
      sortable: false,
      cell: (r) =>
        r.integrity_rules.length === 0 ? (
          DASH
        ) : (
          <span className="flex flex-wrap gap-x-1 text-negative">
            {r.integrity_rules.map((rule, i) => (
              <span key={rule} title={integritySentence(rule, r.integrity_details[rule] ?? "")}>
                {rule}
                {i < r.integrity_rules.length - 1 ? "," : ""}
              </span>
            ))}
          </span>
        ),
    },
  ];

  const pp = (n: number | null) => (n === null ? DASH : `${num(n, 2)} pp`);
  const mvColumns: Column<MvAccuracyRow>[] = [
    { key: "target_day", label: "Update", align: "left", sortable: false, cell: (r) => r.target_day },
    { key: "scored", label: "Scored", sortable: false, cell: (r) => num(r.scored) },
    { key: "unscorable", label: "Unscorable", sortable: false, cell: (r) => num(r.unscorable) },
    { key: "direction", label: "Direction right", sortable: false, cell: (r) => directionRight(r) },
    { key: "mae_pct", label: "Avg miss", sortable: false, cell: (r) => pp(r.mae_pct) },
    { key: "baseline_mae_pct", label: "No change", sortable: false, cell: (r) => pp(r.baseline_mae_pct) },
    { key: "mae_eur", label: "Avg miss (€)", sortable: false, cell: (r) => money(r.mae_eur) },
    { key: "baseline_mae_eur", label: "No change (€)", sortable: false, cell: (r) => money(r.baseline_mae_eur) },
  ];

  return (
    <>
      <StatusHeader title="Calibration & health" />
      <div className="flex flex-col gap-6 px-6 pb-8">
        <div>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-[0.08em] text-muted">
            Calibration by matchday
          </h2>
          {matchdays.length === 0 ? (
            <p className="text-sm text-muted">No calibration reports yet.</p>
          ) : (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {matchdays.map((rows) => (
                <div
                  key={`${rows[0].season}-${rows[0].day_number}`}
                  className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-4"
                >
                  <span className="text-sm font-semibold text-text">
                    Matchday {rows[0].day_number} · {rows[0].season}
                  </span>
                  {rows.map((row) => (
                    <ReportRow key={`${row.backfill}`} row={row} names={names} />
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
        <div>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-[0.08em] text-muted">
            Market-value forecast
          </h2>
          <p className="mb-3 text-sm text-text-dim">{accuracySentence(summarize(mvRows))}</p>
          {mvRows.length > 0 ? (
            <DataTable columns={mvColumns} rows={mvRows} sort="" dir="desc" basePath="/health" />
          ) : null}
        </div>
        <div>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-[0.08em] text-muted">
            Session runs
          </h2>
          <DataTable
            columns={columns}
            rows={sessionRows}
            sort=""
            dir="desc"
            basePath="/health"
          />
        </div>
      </div>
    </>
  );
}

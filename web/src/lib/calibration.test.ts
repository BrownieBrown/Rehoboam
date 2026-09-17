import { describe, expect, it } from "vitest";
import {
  barWidths,
  compare,
  emptyReportSentence,
  gateSentence,
  OUTCOME_TEXT,
  type Gate,
} from "./calibration";

describe("compare", () => {
  it("higher-is-better: ours ahead beats the baseline", () => {
    expect(compare(5, 3, "higher")).toBe("beats");
  });

  it("higher-is-better: ours behind loses to the baseline", () => {
    expect(compare(3, 5, "higher")).toBe("loses");
  });

  it("higher-is-better: equal values tie - never reported as a loss", () => {
    expect(compare(5, 5, "higher")).toBe("ties");
  });

  it("lower-is-better: ours below beats the baseline", () => {
    expect(compare(3, 5, "lower")).toBe("beats");
  });

  it("lower-is-better: ours above loses to the baseline", () => {
    expect(compare(5, 3, "lower")).toBe("loses");
  });

  it("lower-is-better: equal values tie", () => {
    expect(compare(5, 5, "lower")).toBe("ties");
  });

  it("either side null is unknown, not a loss", () => {
    expect(compare(null, 5, "higher")).toBe("unknown");
    expect(compare(5, null, "higher")).toBe("unknown");
    expect(compare(null, null, "lower")).toBe("unknown");
  });
});

describe("barWidths", () => {
  it("gives the larger value a full-width bar and scales the other against it", () => {
    expect(barWidths(10, 5)).toEqual({ ours: 1, baseline: 0.5 });
    expect(barWidths(5, 10)).toEqual({ ours: 0.5, baseline: 1 });
  });

  it("draws a negative value as an empty bar, never as long as the equivalent positive", () => {
    expect(barWidths(-3, 5)).toEqual({ ours: 0, baseline: 1 });
    expect(barWidths(5, -3)).toEqual({ ours: 1, baseline: 0 });
  });

  it("both zero gives zero widths, not a divide-by-zero", () => {
    expect(barWidths(0, 0)).toEqual({ ours: 0, baseline: 0 });
  });

  it("both negative gives zero widths - there is nothing positive to show", () => {
    expect(barWidths(-3, -5)).toEqual({ ours: 0, baseline: 0 });
    expect(barWidths(-5, -3)).toEqual({ ours: 0, baseline: 0 });
  });
});

describe("gateSentence", () => {
  const FORBIDDEN = /null|undefined|NaN|[{}]/;

  it("says there is no verdict yet when the gate is null", () => {
    const text = gateSentence(null);
    expect(text).toBe("No gate verdict yet. The first one comes with the first live report.");
    expect(text).not.toMatch(FORBIDDEN);
  });

  it("reads as passed for a passing gate", () => {
    const gate: Gate = {
      spearman_ok: true,
      regret_ok: true,
      consecutive_ok: 3,
      required: 3,
      integrity_clean_days: 7,
      required_clean_days: 7,
      passes: true,
    };
    const text = gateSentence(gate);
    expect(text).toBe(
      "Gate passed: 3 of 3 consecutive reports beat the baseline, and 7.0 of 7 days free of integrity failures. Trading can resume.",
    );
    expect(text).not.toMatch(FORBIDDEN);
  });

  it("reads as not passed for a failing gate", () => {
    const gate: Gate = {
      spearman_ok: false,
      regret_ok: true,
      consecutive_ok: 1,
      required: 3,
      integrity_clean_days: 2.5,
      required_clean_days: 7,
      passes: false,
    };
    const text = gateSentence(gate);
    expect(text).toBe(
      "Gate not passed: 1 of 3 consecutive reports beat the baseline, and 2.5 of 7 days free of integrity failures.",
    );
    expect(text).not.toMatch(FORBIDDEN);
  });

  it("still reads correctly when spearman_ok and regret_ok are both null - the no-report case Python produces", () => {
    const gate: Gate = {
      spearman_ok: null,
      regret_ok: null,
      consecutive_ok: 0,
      required: 3,
      integrity_clean_days: 7,
      required_clean_days: 7,
      passes: false,
    };
    const text = gateSentence(gate);
    expect(text).toBe(
      "Gate not passed: 0 of 3 consecutive reports beat the baseline, and 7.0 of 7 days free of integrity failures.",
    );
    expect(text).not.toMatch(FORBIDDEN);
  });
});

describe("emptyReportSentence", () => {
  const FORBIDDEN = /null|undefined|NaN|[{}]/;
  const someGate: Gate = {
    spearman_ok: null,
    regret_ok: null,
    consecutive_ok: 0,
    required: 3,
    integrity_clean_days: 7,
    required_clean_days: 7,
    passes: false,
  };

  it("a live row with a null gate: no predictions existed before this kickoff", () => {
    const text = emptyReportSentence({ backfill: false, gate: null, n_stale_rows: 0 });
    expect(text).toBe(
      "Settled with no predictions — this matchday finished before the bot was writing them.",
    );
    expect(text).not.toMatch(FORBIDDEN);
  });

  it("a live row with a gate and stale rows: predictions existed but nothing could be scored", () => {
    const text = emptyReportSentence({ backfill: false, gate: someGate, n_stale_rows: 45 });
    expect(text).toBe("No row could be scored — all 45 were stale.");
    expect(text).not.toMatch(FORBIDDEN);
  });

  it("a live row with a gate and no stale rows: nothing to score, and it wasn't staleness", () => {
    const text = emptyReportSentence({ backfill: false, gate: someGate, n_stale_rows: 0 });
    expect(text).toBe("No row could be scored.");
    expect(text).not.toMatch(FORBIDDEN);
  });

  it("a backfill row with stale rows: backfill never carries a gate, but the reason is still staleness", () => {
    const text = emptyReportSentence({ backfill: true, gate: null, n_stale_rows: 12 });
    expect(text).toBe("No row could be scored — all 12 were stale.");
    expect(text).not.toMatch(FORBIDDEN);
  });

  it("singular versus plural stale count", () => {
    expect(emptyReportSentence({ backfill: false, gate: someGate, n_stale_rows: 1 })).toBe(
      "No row could be scored — all 1 was stale.",
    );
    expect(emptyReportSentence({ backfill: false, gate: someGate, n_stale_rows: 2 })).toBe(
      "No row could be scored — all 2 were stale.",
    );
  });
});

describe("OUTCOME_TEXT", () => {
  it("covers every Outcome the module can produce", () => {
    expect(Object.keys(OUTCOME_TEXT).sort()).toEqual(["beats", "loses", "ties", "unknown"]);
  });
});

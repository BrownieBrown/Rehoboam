# REH-78 Dominance Rule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace REH-75's degenerate dominance rule with a non-degenerate one, add the realised-hold view it reports beside itself, and re-run `diagnose-flips` under REH-77's fix.

**Architecture:** A new pure function `dominant_loss_mechanisms` lives beside the old `dominant_mechanism` in `flip_diagnosis.py`; the old one is left untouched so the re-run is a controlled comparison. `run_diagnosis` gains a second evaluation of the same identity at each trip's sale instant. `flip_report.py` renders the registered verdict, the per-horizon verdicts, and the hold view — appending blocks rather than editing the horizon sweep table, so the sweep stays byte-comparable against REH-75's appendix.

**Tech Stack:** Python 3.12, pytest, uv, SQLite (read-only), Typer CLI.

**Spec:** `docs/superpowers/specs/2026-08-20-reh-78-dominance-rule-design.md`

## Global Constraints

- The rule text in the spec's "The re-registered rule" section is **binding and quotable**. Implementation matches it; if implementation and rule disagree, the rule wins and the code changes.
- `dominant_mechanism` and its three existing tests are **not modified, not renamed, not deleted**.
- The horizon sweep table in `format_report` is **not modified** — no new columns, no width changes. New content is appended as separate blocks.
- Eligibility is `signed contribution < 0`. Exactly zero is not eligible.
- Co-dominance band: `abs(winner) - abs(other) <= 0.20 * abs(winner)`, matching the old rule's arithmetic (`<=`, so exactly 20% is co-dominant).
- The sale-instant market value uses `TrainingCorpus.market_value_at` (at-or-before), never `mv_nearest`.
- Money is integer euros throughout. No floats in any total.
- Branch: `marcobraun2013/reh-78-re-register-a-non-degenerate-dominance-rule-before-the-next`. Never commit to `main`.
- Do not run `black` on whole files — the repo is not black-clean and it causes collateral churn. `pre-commit` handles staged files.

______________________________________________________________________

### Task 1: The re-registered rule

**Files:**

- Modify: `rehoboam/diagnostics/flip_diagnosis.py` (add after `dominant_mechanism`, which ends at line 278)
- Test: `tests/test_diagnostics/test_diagnosis_run.py`

**Interfaces:**

- Consumes: `Decomposition`, `signed_contributions(totals) -> dict[str, int]` (both already exist)
- Produces:
  - `dominant_loss_mechanisms(totals: Decomposition, *, tie_band: float = 0.20) -> tuple[str, ...]` — eligible terms ordered by magnitude descending; `()` means *no loss to explain*
  - `agreement_label(a: tuple[str, ...], b: tuple[str, ...]) -> str` — `"identical"` | `"overlapping"` | `"disjoint"`

> **Step 3 of this task is authored by Marco, not by an agent.** The stub, docstring, and tests are prepared; the predicate body is his. Do not fill it in.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_diagnostics/test_diagnosis_run.py`, and add `agreement_label` plus `dominant_loss_mechanisms` to the existing import block at the top of that file:

```python
# --- REH-78: the re-registered rule -------------------------------------

# The population totals REH-75 published (results doc section 3), pre-REH-77.
# Entry premium is stored UNNEGATED on Decomposition, so +116_401_328 here is
# a signed contribution of -116_401_328 -- which is what makes it eligible.
REH75_TOTALS = {
    14: Decomposition(
        selection=-64_936_734, exit_timing=+126_081_998, entry_premium=116_401_328
    ),
    21: Decomposition(
        selection=-115_271_263, exit_timing=+176_416_527, entry_premium=116_401_328
    ),
    30: Decomposition(
        selection=-116_527_447, exit_timing=+177_672_711, entry_premium=116_401_328
    ),
    45: Decomposition(
        selection=-141_559_888, exit_timing=+202_705_152, entry_premium=116_401_328
    ),
    60: Decomposition(
        selection=-164_802_412, exit_timing=+225_947_676, entry_premium=116_401_328
    ),
}


def test_a_positive_term_is_never_eligible_however_large():
    """The defect REH-78 exists to fix: exit timing is +EUR177.7M at H=30 and
    was named the dominant mechanism OF A LOSS. A gain cannot cause a loss."""
    assert "exit_timing" not in dominant_loss_mechanisms(REH75_TOTALS[30])


def test_the_five_published_horizons_return_the_pre_registered_verdicts():
    """Pinned to numbers that existed before this code did (design doc,
    'What the rule returns, stated before it is run')."""
    assert dominant_loss_mechanisms(REH75_TOTALS[14]) == ("entry_premium",)
    assert dominant_loss_mechanisms(REH75_TOTALS[21]) == ("entry_premium", "selection")
    assert dominant_loss_mechanisms(REH75_TOTALS[30]) == ("selection", "entry_premium")
    assert dominant_loss_mechanisms(REH75_TOTALS[45]) == ("selection", "entry_premium")
    assert dominant_loss_mechanisms(REH75_TOTALS[60]) == ("selection",)


def test_the_old_rule_still_returns_its_degenerate_answer():
    """Kept callable on purpose: it makes the re-run a controlled comparison
    (same data, two rules) instead of a claim about deleted code."""
    assert dominant_mechanism(REH75_TOTALS[30]) == "exit_timing"


def test_no_negative_term_means_no_loss_to_explain():
    assert (
        dominant_loss_mechanisms(
            Decomposition(selection=100, exit_timing=50, entry_premium=0)
        )
        == ()
    )


def test_exactly_zero_is_not_negative_and_not_eligible():
    verdict = dominant_loss_mechanisms(
        Decomposition(selection=-1_000, exit_timing=0, entry_premium=0)
    )
    assert verdict == ("selection",)


def test_a_gap_at_exactly_the_band_is_co_dominant():
    """`<=`, matching the old rule's arithmetic: 800 is exactly 20% below 1000."""
    verdict = dominant_loss_mechanisms(
        Decomposition(selection=-1_000, exit_timing=-800, entry_premium=0)
    )
    assert verdict == ("selection", "exit_timing")


def test_a_gap_outside_the_band_names_one_term():
    verdict = dominant_loss_mechanisms(
        Decomposition(selection=-1_000, exit_timing=-799, entry_premium=0)
    )
    assert verdict == ("selection",)


def test_verdicts_are_ordered_by_magnitude_descending():
    verdict = dominant_loss_mechanisms(
        Decomposition(selection=-900, exit_timing=-1_000, entry_premium=0)
    )
    assert verdict == ("exit_timing", "selection")


def test_agreement_labels():
    assert agreement_label(("selection",), ("selection",)) == "identical"
    assert (
        agreement_label(("selection", "entry_premium"), ("entry_premium",))
        == "overlapping"
    )
    assert agreement_label(("selection",), ("entry_premium",)) == "disjoint"
    assert agreement_label((), ()) == "identical"
    assert agreement_label((), ("entry_premium",)) == "disjoint"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/test_diagnostics/test_diagnosis_run.py -q`
Expected: FAIL — `ImportError: cannot import name 'dominant_loss_mechanisms'`

- [ ] **Step 3: Write the implementation (AUTHORED BY MARCO)**

Add to `rehoboam/diagnostics/flip_diagnosis.py`, directly below `dominant_mechanism`. Prepare exactly this stub and stop:

```python
def dominant_loss_mechanisms(
    totals: Decomposition, *, tie_band: float = 0.20
) -> tuple[str, ...]:
    """Apply REH-78's re-registered rule (design doc 2026-08-20).

    Only NEGATIVE signed contributions are eligible: a term that reduced the
    loss cannot be its cause. Exactly zero is not negative. The largest
    eligible magnitude is the dominant loss mechanism; any other eligible term
    within `tie_band` of it is co-dominant and returned alongside, ordered by
    magnitude descending. An empty tuple means NO LOSS TO EXPLAIN -- the only
    circumstance in which this rule declines to answer.

    This replaces `dominant_mechanism`, which is retained above so the re-run
    can print both. Do not delete that one.
    """
    # TODO(marco): the predicate. `signed_contributions(totals)` gives the
    # three signed sums; the band is `abs(winner) - abs(other) <= tie_band *
    # abs(winner)`, matching the old rule's `<=` arithmetic.


def agreement_label(a: tuple[str, ...], b: tuple[str, ...]) -> str:
    """How two verdict sets relate: identical, overlapping, or disjoint.

    Two empty sets are identical; one empty against one non-empty is disjoint.
    """
    if a == b:
        return "identical"
    return "overlapping" if set(a) & set(b) else "disjoint"
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_diagnostics/test_diagnosis_run.py -q`
Expected: PASS, including the three pre-existing `dominant_mechanism` tests.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/diagnostics/flip_diagnosis.py tests/test_diagnostics/test_diagnosis_run.py
git commit -m "feat(diagnostics): the re-registered dominance rule (REH-78)"
```

______________________________________________________________________

### Task 2: The realised-hold view

**Files:**

- Modify: `rehoboam/diagnostics/flip_diagnosis.py` — `TripRow` (line 155), `DiagnosisResult` (line 166), `run_diagnosis` (line 281)
- Test: `tests/test_diagnostics/test_run_diagnosis.py`

**Interfaces:**

- Consumes: `decompose(trip, *, mv_buy, mv_h)`, `TrainingCorpus.market_value_at(player_id, at)`
- Produces:
  - `TripRow.at_hold: Decomposition | None`
  - `DiagnosisResult.hold_censored: int`
  - `totals_at_hold(result: DiagnosisResult) -> Decomposition`

No new decomposition function: the hold identity IS `decompose(trip, mv_buy=mv_buy, mv_h=mv_sell)` — selection `mv_sell - mv_buy`, exit `s - mv_sell`, premium unchanged.

- [ ] **Step 1: Write the failing tests**

First extend the shared fixture in `tests/test_diagnostics/test_run_diagnosis.py`. Add a sixth trip to `_TRIPS`:

```python
    # A snapshot 0.5 days AFTER sell_date is numerically nearer than the one a
    # day before it. `mv_nearest` would pick it, leaking post-sale price action
    # into the exit term; `market_value_at` must not. The +14d and +30d
    # snapshots exist so this row decomposes at both horizons and leaves the
    # censoring bookkeeping below unchanged.
    ("p_sale_leak", "SaleLeak", 1_000_000, 1_050_000, DAY0, DAY0 + 40 * 86400, 40),
```

and its series to `_MV_SERIES`:

```python
    "p_sale_leak": [
        (DAY0 - 86400, 1_000_000),
        (DAY0 + 14 * 86400, 1_010_000),
        (DAY0 + 30 * 86400, 1_015_000),
        (DAY0 + 39 * 86400, 1_020_000),
        (DAY0 + 40.5 * 86400, 5_000_000),
    ],
```

In `test_run_diagnosis_over_a_small_fixture`, update the row count — the fixture grew by one and nothing else about that test changes:

```python
assert len(result.rows) == 6
```

`result.censored == {14: 2, 30: 3}` stays as it is: `p_sale_leak` has snapshots at both horizons, so it censors at neither.

Then append the new tests, adding `totals_at_hold` to the file's import block:

```python
def test_the_hold_view_uses_the_at_or_before_lookup(tmp_path):
    """The sale is a DECISION instant, so it follows mv_buy's rule. A snapshot
    taken after we sold must not reach the term that measures our exit."""
    learner_db, corpus_db = _dbs(tmp_path)
    result = run_diagnosis(learner_db, corpus_db, horizons=HORIZONS)
    row = {r.trip.player_id: r for r in result.rows}["p_sale_leak"]
    assert row.at_hold == Decomposition(
        selection=20_000,  # 1_020_000 - 1_000_000, NOT 5_000_000 - 1_000_000
        exit_timing=30_000,  # 1_050_000 - 1_020_000
        entry_premium=0,  # bought at market value
    )


def test_the_hold_identity_closes_to_realised_pnl(tmp_path):
    learner_db, corpus_db = _dbs(tmp_path)
    result = run_diagnosis(learner_db, corpus_db, horizons=HORIZONS)
    contributing = [r for r in result.scored() if r.at_hold is not None]
    assert totals_at_hold(result).total == sum(r.trip.realised for r in contributing)


def test_the_hold_view_cannot_be_censored_once_the_buy_resolved(tmp_path):
    """A STRUCTURAL invariant, not an observation. `market_value_at` returns the
    most recent snapshot at or before its argument, and buy_date < sell_date, so
    whatever resolved `mv_buy` is still a candidate at the sale: a row with a
    buy market value can never lack a sale one. `hold_censored` is therefore a
    tripwire that must read 0 on every dataset -- it exists so that a change to
    that lookup's semantics surfaces here instead of silently zeroing an exit
    term. Do not replace this with a test that manufactures a censored row; it
    cannot be built without breaking the lookup itself."""
    learner_db, corpus_db = _dbs(tmp_path)
    result = run_diagnosis(learner_db, corpus_db, horizons=HORIZONS)
    assert all(r.at_hold is not None for r in result.rows if r.mv_buy is not None)
    assert result.hold_censored == 0


def test_a_row_with_no_market_value_at_buy_has_no_hold_view_either(tmp_path):
    learner_db, corpus_db = _dbs(tmp_path)
    result = run_diagnosis(learner_db, corpus_db, horizons=HORIZONS)
    no_mv = {r.trip.player_id: r for r in result.rows}["p_no_mv"]
    assert no_mv.mv_buy is None
    assert no_mv.at_hold is None
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/test_diagnostics/test_run_diagnosis.py -q`
Expected: FAIL — `AttributeError: 'TripRow' object has no attribute 'at_hold'`

- [ ] **Step 3: Implement**

In `TripRow`, after `by_horizon`:

```python
at_hold: Decomposition | None
```

In `DiagnosisResult`, after `censored`. It carries a default because many
existing tests construct `DiagnosisResult` directly and this is an aggregate
counter, not a per-row fact. `TripRow.at_hold` deliberately gets NO default:
it must be passed explicitly at both construction sites in `run_diagnosis`, so
an omission is a type error rather than a silent `None`.

```python
hold_censored: int = 0
```

In `run_diagnosis`, inside the per-trip loop after `mv_buy` is known to be non-`None` (the `mv_buy is None` branch already `continue`s and must pass `at_hold=None`):

```python
        # The identity again at the SALE instant. `market_value_at` (at-or-
        # before), never `mv_nearest`: the sale date is a decision instant,
        # and `mv_nearest` is bidirectional -- it can resolve to a snapshot
        # taken after we sold and leak post-sale price action into the exit
        # term. Same reasoning as `mv_buy` above; see the REH-78 design doc.
        mv_sell = corpus.market_value_at(trip.player_id, trip.sell_date)
        at_hold = None if mv_sell is None else decompose(trip, mv_buy=mv_buy, mv_h=mv_sell)
        if mv_sell is None and not is_floor:
            hold_censored += 1
```

Initialise `hold_censored = 0` beside `censored`, pass `at_hold=at_hold` in the second `TripRow(...)`, `at_hold=None` in the first, and `hold_censored=hold_censored` into `DiagnosisResult`.

Add beside `totals_by_horizon`:

```python
def totals_at_hold(result: DiagnosisResult) -> Decomposition:
    """Population totals of the identity evaluated at each trip's sale instant.

    A supplementary view, NOT the registered instrument: the sale date is
    chosen by the bot, usually at a local high, so its selection term is
    conditioned on the outcome. See the REH-78 design doc.
    """
    return _sum([r.at_hold for r in result.scored() if r.at_hold is not None])
```

`_sum` takes `list[Decomposition]`; the comprehension already excludes `None`.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_diagnostics/ -q`
Expected: PASS — the whole diagnostics suite, since `TripRow` is constructed in several test helpers that now need `at_hold`. Fix any helper that fails by passing `at_hold=None`.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/diagnostics/flip_diagnosis.py tests/test_diagnostics/
git commit -m "feat(diagnostics): evaluate the identity at each trip's realised hold (REH-78)"
```

______________________________________________________________________

### Task 3: Report the registered verdict

**Files:**

- Modify: `rehoboam/diagnostics/flip_report.py` — import block (line 15), `format_report` (line 64)
- Test: `tests/test_diagnostics/test_flip_report.py`

**Interfaces:**

- Consumes: `dominant_loss_mechanisms`, `agreement_label`, `totals_at_hold` (Tasks 1-2)
- Produces: no new public functions; two module-level display helpers `_verdict_text(terms)` and the constant `SUPERSEDED_NOTE`

**Placement:** the new blocks go immediately AFTER the existing `Headline at H=30d: dominant mechanism = ...` block and BEFORE the `LABEL_SEMANTICS` / per-branch section. The horizon sweep table above is not touched.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_diagnostics/test_flip_report.py`, importing `SUPERSEDED_NOTE` alongside the existing `POSITIVE_WINNER_NOTE`:

```python
# REH-75's published population totals (results doc section 3) and its hold
# view (section 4). Both close to the same realised P&L, -55,256,064, because
# the decomposition is an identity -- which is why one synthetic trip can
# carry them.
REH75_HEADLINE = {
    14: Decomposition(
        selection=-64_936_734, exit_timing=+126_081_998, entry_premium=116_401_328
    ),
    21: Decomposition(
        selection=-115_271_263, exit_timing=+176_416_527, entry_premium=116_401_328
    ),
    30: Decomposition(
        selection=-116_527_447, exit_timing=+177_672_711, entry_premium=116_401_328
    ),
    45: Decomposition(
        selection=-141_559_888, exit_timing=+202_705_152, entry_premium=116_401_328
    ),
    60: Decomposition(
        selection=-164_802_412, exit_timing=+225_947_676, entry_premium=116_401_328
    ),
}
HOLD_TOTALS = Decomposition(
    selection=+43_371_202, exit_timing=+17_774_062, entry_premium=116_401_328
)


def _published_result(by_horizon=None, at_hold=None, realised=-55_256_064):
    """One synthetic row carrying the published totals, so the report's verdict
    lines are pinned to numbers that predate this code. `format_report` checks
    that every horizon Total equals the realised P&L of the rows behind it, so
    buy/sell prices are chosen to satisfy that identity."""
    trip = RoundTrip(
        trip_id=1,
        player_id="p1",
        player_name="Tester",
        buy_price=100_000_000,
        sell_price=100_000_000 + realised,
        buy_date=DAY0,
        sell_date=DAY0 + 40 * 86400,
        hold_days=40,
    )
    row = TripRow(
        trip=trip,
        mv_buy=100_000_000,
        branch="rising",
        by_horizon=REH75_HEADLINE if by_horizon is None else by_horizon,
        peak_during_hold=None,
        is_floor_trip=False,
        at_hold=HOLD_TOTALS if at_hold is None else at_hold,
    )
    horizons = tuple(row.by_horizon)
    return DiagnosisResult(
        rows=[row],
        horizons=horizons,
        censored=dict.fromkeys(horizons, 0),
        hold_censored=0,
    )


def test_the_report_prints_the_registered_verdict_and_marks_the_old_one_superseded():
    text = format_report(_published_result())
    assert (
        "Registered verdict at H=30d (REH-78): selection + entry premium (co-dominant)"
        in text
    )
    assert SUPERSEDED_NOTE in text
    # The old rule's line survives verbatim beside it -- that is what makes the
    # re-run a controlled comparison rather than a claim about deleted code.
    assert "dominant mechanism = exit_timing" in text


def test_the_report_prints_a_verdict_for_every_horizon():
    text = format_report(_published_result())
    assert "Dominance by horizon (REH-78 rule)" in text
    for horizon, expected in (
        (14, "entry premium"),
        (21, "entry premium + selection (co-dominant)"),
        (30, "selection + entry premium (co-dominant)"),
        (45, "selection + entry premium (co-dominant)"),
        (60, "selection"),
    ):
        assert f"{horizon}d" in text
        assert expected in text


def test_the_report_prints_the_hold_view_with_its_agreement_label():
    text = format_report(_published_result())
    assert "Supplementary — the identity at each trip's realised hold" in text
    assert "NOT the registered instrument" in text
    # Registered verdict is {selection, entry premium}; the hold view has one
    # eligible term, entry premium. They share a term without being equal.
    assert "Agreement with the registered verdict: overlapping" in text


def test_a_population_that_lost_nothing_is_rendered_as_no_loss_to_explain():
    """The rule's one silence. It must not surface as an empty list."""
    all_gains = dict.fromkeys(
        (14, 21, 30, 45, 60),
        Decomposition(selection=100, exit_timing=50, entry_premium=0),
    )
    text = format_report(
        _published_result(
            by_horizon=all_gains,
            at_hold=Decomposition(selection=100, exit_timing=50, entry_premium=0),
            realised=150,
        )
    )
    assert "Registered verdict at H=30d (REH-78): no loss to explain" in text


def test_the_horizon_sweep_table_header_is_unchanged():
    """REH-78 design section 5 predicts the sweep cannot move, and that
    prediction is tested by diffing this table against REH-75's appendix --
    which only works while the columns stay exactly as they were. A verdict
    column added here would break the diff for a formatting reason and make the
    prediction untestable."""
    text = format_report(_published_result())
    assert (
        "Horizon           Selection              Exit     Entry premium"
        "             Total      n  Censored"
    ) in text
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/test_diagnostics/test_flip_report.py -q`
Expected: FAIL — `ImportError: cannot import name 'SUPERSEDED_NOTE'`

- [ ] **Step 3: Implement**

Extend the import block from `flip_diagnosis` with `agreement_label`,
`dominant_loss_mechanisms` and `totals_at_hold`, keeping `dominant_mechanism`
— both rules are called. Then add to the module constants:

```python
# Printed on the REH-75 headline block. That block is kept verbatim so the
# re-run stays line-comparable against the results document's appendix; this
# note is what stops a reader taking its verdict as current.
SUPERSEDED_NOTE = (
    "  (superseded: this rule could not name selection — see the registered "
    "verdict below and REH-78)"
)

_DISPLAY = {
    "selection": "selection",
    "exit_timing": "exit timing",
    "entry_premium": "entry premium",
}


def _verdict_text(terms: tuple[str, ...]) -> str:
    if not terms:
        return "no loss to explain"
    named = " + ".join(_DISPLAY[t] for t in terms)
    return f"{named} (co-dominant)" if len(terms) > 1 else named
```

After the existing headline block, append `SUPERSEDED_NOTE`, then:

```python
    registered = dominant_loss_mechanisms(headline)
    hold = totals_at_hold(result)
    hold_verdict = dominant_loss_mechanisms(hold)
    lines += [
        "",
        f"Registered verdict at H={HEADLINE_HORIZON}d (REH-78): {_verdict_text(registered)}",
        "  Only negative contributions are eligible; a term that reduced the "
        "loss cannot be its cause.",
        "",
        "Dominance by horizon (REH-78 rule)",
        _RULE,
    ]
    for h in result.horizons:
        lines.append(f"{f'{h}d':<9}{_verdict_text(dominant_loss_mechanisms(horizon_totals[h]))}")
    lines += [
        "",
        "Supplementary — the identity at each trip's realised hold "
        "(NOT the registered instrument: the sale date is bot-chosen, so "
        "selection is conditioned on the outcome)",
        _RULE,
        f"  Selection:      {_eur(hold.selection)}",
        f"  Exit timing:    {_eur(hold.exit_timing)}",
        f"  Entry premium:  {_eur(hold.entry_premium)}  (paid over market value, unnegated)",
        f"  Total:          {_eur(hold.total)}",
        f"  Verdict: {_verdict_text(hold_verdict)}",
        f"  Agreement with the registered verdict: "
        f"{agreement_label(registered, hold_verdict)}",
        f"  Censored (no market value at or before the sale): {result.hold_censored}",
        "",
    ]
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, 755+ passed.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/diagnostics/flip_report.py tests/test_diagnostics/test_flip_report.py
git commit -m "feat(diagnostics): report the registered verdict and the hold view (REH-78)"
```

______________________________________________________________________

### Task 4: The gated re-run

**Files:**

- Create: `docs/superpowers/specs/2026-08-20-reh-78-rerun-results.md`
- Modify: `docs/superpowers/specs/2026-08-19-reh-75-flip-diagnosis-results.md` (header only)

No TDD here — this task runs an instrument and writes down what it said.

- [ ] **Step 1: Verify the input digests match REH-75's**

```bash
shasum -a 256 logs/bid_learning.db logs/training_corpus.db
```

Expected, from the REH-75 results doc:

```
76e55eba3c68aa147809c09467336166951935662d800954209a6bc1472f18ce  logs/bid_learning.db
0af472a7ac5a9193348def8bfa8cb53cf83f3650fe2373b1971a4b9314b62999  logs/training_corpus.db
```

If either differs, STOP and report it. The section-5 prediction compares two runs and is only a test of REH-77 if the inputs are the same bytes. A differing digest does not block the re-run, but it does mean the results document must say the prediction was untestable rather than quietly comparing across different data.

- [ ] **Step 2: Run the quality gates**

```bash
uv run pytest -q
uv run ruff check rehoboam/ tests/
uv run mypy rehoboam/diagnostics/ --ignore-missing-imports
uv run bandit -r rehoboam/diagnostics/ -c pyproject.toml
```

Expected: tests pass; ruff clean; **0 mypy errors in `rehoboam/diagnostics/`**; **0 bandit findings in `rehoboam/diagnostics/`**. Pre-existing errors elsewhere in the repo are recorded, not fixed.

- [ ] **Step 3: Run the determinism gate**

```bash
uv run rehoboam diagnose-flips > /tmp/reh78-run1.txt
uv run rehoboam diagnose-flips > /tmp/reh78-run2.txt
diff /tmp/reh78-run1.txt /tmp/reh78-run2.txt && echo DETERMINISTIC
```

Expected: `diff` exits 0.

- [ ] **Step 4: Test the section-5 prediction**

Extract the horizon sweep from the new run and diff it against REH-75's appendix:

```bash
sed -n '/^Horizon sweep/,/^Ground truth/p' /tmp/reh78-run1.txt > /tmp/reh78-sweep-new.txt
sed -n '/^Horizon sweep/,/^Ground truth/p' docs/superpowers/specs/2026-08-19-reh-75-flip-diagnosis-results.md > /tmp/reh78-sweep-old.txt
diff /tmp/reh78-sweep-old.txt /tmp/reh78-sweep-new.txt && echo "PREDICTION HELD"
```

Expected: identical. If it differs, the results document leads with that as an unexplained second change and does NOT present the new sweep as a headline.

- [ ] **Step 5: Write the results document**

`docs/superpowers/specs/2026-08-20-reh-78-rerun-results.md`, following REH-75's results structure. Required sections:

1. Input digests, and whether they matched REH-75's
1. Quality gates, with exact counts
1. Determinism gate result
1. **The prediction** — held or failed, with the diff output
1. **The registered verdict** at H=30 and at every horizon, beside the superseded REH-75 verdict
1. **The hold view** and its agreement label
1. **What moved:** branch labels (section 8), the flip-eligible set (the 108 count and its subtotals), mirror divergence — the before/after of each
1. Caveats carried verbatim from the design doc's "Caveats to state in the results, not paper over"

- [ ] **Step 6: Annotate REH-75's results document**

Add immediately below its `Status:` line — do not edit anything else in that file:

```markdown
**Superseded in part (2026-08-20, REH-78).** Section 4's dominance rule was
degenerate: it could not return `selection`, and it named a gain as the cause
of a loss. The rule and its verdict are re-registered in
`2026-08-20-reh-78-dominance-rule-design.md`, and re-run in
`2026-08-20-reh-78-rerun-results.md`. Everything else in this document
stands; section 4's verdict remains a true record of what that rule returned.
```

- [ ] **Step 7: Commit and open the PR**

```bash
git add docs/superpowers/specs/
git commit -m "docs(diagnostics): the re-run under the re-registered rule (REH-78)"
git push -u origin marcobraun2013/reh-78-re-register-a-non-degenerate-dominance-rule-before-the-next
```

Then open a PR summarising: the rule, whether the prediction held, and the verdict delta.

# EP Targeting and Quality Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the bot ranking a second-division defender above a Bayern
midfielder, and give it a defensible notion of a target worth waiting for.

**Architecture:** Four changes, ordered so each is independently testable.
First close a replay/live parity leak by extracting the prev-status traversal
both currently implement separately. Then bound that traversal by recency, so a
three-month-old bench appearance stops driving availability. Then stop applying
a fitted quality coefficient to players whose fitted history is second-division.
Finally add an absolute target bar and a computed hold state on top of the
now-trustworthy EP.

**Tech Stack:** Python 3.12, pytest, pydantic-settings, SQLite.

**Spec:** `docs/superpowers/specs/2026-08-22-ep-targeting-and-quality-bar-design.md`

## Global Constraints

- Real Kickbase points everywhere. Never reintroduce a 0-100 index constant.
  See `scoring/v2/thresholds.py`.
- Every tunable threshold is a `Settings` field so it can change from `.env`
  without a deploy. No inline magic numbers.
- Never add a second implementation of a scoring path. `scoring/v2/thresholds.py`
  states the rule; REH-84 records what it cost last time.
- Do not override `P(status)` at serving time. `rate.py`'s module docstring
  explains that quality is pooled across statuses and a direct override
  reintroduces a ~24% starter bias. Changing the *input* to
  `availability.predict` is fine; replacing its *output* is not.
- Run `uv run pytest -q -m "not slow"` before every commit. 839 tests pass at
  the start of this plan.
- `uv run ruff check rehoboam/ tests/` must pass. Do not run `black` manually on
  existing files — pre-commit pins black 25.1.0 at `--line-length=100`; a local
  black may differ and cause collateral reformatting.

______________________________________________________________________

### Task 1: Extract the prev-status traversal so replay and live share one implementation

The live scorer derives "most recent played status" in
`adapter.last_played_status`. The replay derives the same thing with its own
inline loop at `replay/driver.py:145-149`. That is the exact second
implementation REH-84's comment in that file warns against — it is why a
scoring change once left the replay printing an identical 26,960.

This task is a pure refactor. Behaviour must not change.

**Files:**

- Modify: `rehoboam/scoring/v2/adapter.py` (add `prev_status_from_history`, make
  `last_played_status` delegate to it)
- Modify: `rehoboam/replay/driver.py:141-166` (delegate to the same function)
- Test: `tests/test_scoring_v2/test_adapter.py`

**Interfaces:**

- Consumes: `PLAYED_STATUSES` from `rehoboam.scoring.v2.features`

- Produces: `prev_status_from_history(history: Sequence[tuple[str | None, int | None]]) -> int | None`
  where each tuple is `(iso_match_date, status)` and the sequence is ordered
  oldest-first. Tasks 2 extends this signature.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scoring_v2/test_adapter.py`:

```python
from rehoboam.scoring.v2.adapter import prev_status_from_history


class TestPrevStatusFromHistory:
    def test_returns_the_latest_played_status(self):
        history = [
            ("2026-05-01T13:30:00Z", 5),
            ("2026-05-09T16:30:00Z", 4),
        ]
        assert prev_status_from_history(history) == 4

    def test_skips_unplayed_fixtures(self):
        history = [
            ("2026-05-01T13:30:00Z", 5),
            ("2026-08-29T13:30:00Z", 0),
            ("2026-09-05T13:30:00Z", None),
        ]
        assert prev_status_from_history(history) == 5

    def test_no_played_history_returns_none(self):
        assert prev_status_from_history([("2026-08-29T13:30:00Z", 0)]) is None

    def test_empty_history_returns_none(self):
        assert prev_status_from_history([]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring_v2/test_adapter.py::TestPrevStatusFromHistory -v`
Expected: FAIL with `ImportError: cannot import name 'prev_status_from_history'`

- [ ] **Step 3: Write minimal implementation**

In `rehoboam/scoring/v2/adapter.py`, add above `last_played_status`:

```python
def prev_status_from_history(
    history: Sequence[tuple[str | None, int | None]],
) -> int | None:
    """Most recent *played* status from ``(match_date, status)`` pairs.

    Input is ordered oldest-first. Unplayed fixtures (status 0 or absent)
    describe a match that has not happened, not a state the player was in, so
    they are skipped.

    This is the single implementation of that rule. The live scorer and the
    season replay both call it. They previously derived it separately, which is
    the drift `scoring/v2/thresholds.py` forbids and REH-84 records the cost of.
    """
    latest: int | None = None
    for _match_date, status in history:
        if status in PLAYED_STATUSES:
            latest = int(status)
    return latest
```

Add `from collections.abc import Sequence` to the imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scoring_v2/test_adapter.py::TestPrevStatusFromHistory -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Make `last_played_status` delegate**

Replace the body of `last_played_status` in `rehoboam/scoring/v2/adapter.py`.
The existing version sorts by `(season_title, day)` because the API does not
guarantee order; preserve that, then delegate:

```python
def last_played_status(performance: dict | None) -> int | None:
    """The player's status in his most recent *played* match.

    Returns None when there is no played history, which the availability model
    handles by falling back to its marginal prior.
    """
    if not performance:
        return None

    ordered: list[tuple[tuple[str, int], str | None, int | None]] = []
    for season in performance.get("it") or []:
        title = season.get("ti") or ""
        for match in season.get("ph") or []:
            day = match.get("day")
            if day is None:
                continue
            ordered.append(((title, int(day)), match.get("md"), match.get("st")))
    ordered.sort(key=lambda row: row[0])
    return prev_status_from_history([(md, st) for _key, md, st in ordered])
```

- [ ] **Step 6: Run the existing adapter tests to prove no behaviour change**

Run: `uv run pytest tests/test_scoring_v2/ -q`
Expected: PASS, same count as before this task.

- [ ] **Step 7: Make the replay delegate to the same function**

In `rehoboam/replay/driver.py`, replace the inline loop (currently lines
145-149):

```python
        prev_status = None
        for match in reversed(history):
            if match.get("status") in PLAYED_STATUSES:
                prev_status = int(match["status"])
                break
```

with:

```python
        # REH-84's rule applies to this traversal too, not just to `compose_ep`:
        # deriving "most recent played status" here separately is a second
        # implementation that would drift from the live scorer.
        prev_status = prev_status_from_history(
            [(m.get("match_date"), m.get("status")) for m in history]
        )
```

Update the import at the top of the file to include `prev_status_from_history`
alongside `compose_ep`. `PLAYED_STATUSES` may become unused in this file — if
ruff reports it, remove that import.

- [ ] **Step 8: Verify the replay is unchanged**

Run: `uv run rehoboam replay-season 2>&1 | grep -iE "simulated|actual total|FINISHING"`
Expected: `Simulated total: 26,960` / `Actual total: 26,172` / `FINISHING POSITION: 9 of 14`

This is a pure refactor. **If the number moves, stop** — the two
implementations were not equivalent, and that difference is a finding worth
reporting before going further.

- [ ] **Step 9: Full suite and lint**

Run: `uv run pytest -q -m "not slow" && uv run ruff check rehoboam/ tests/`
Expected: 843 passed, ruff clean.

- [ ] **Step 10: Commit**

```bash
git add rehoboam/scoring/v2/adapter.py rehoboam/replay/driver.py tests/test_scoring_v2/test_adapter.py
git commit -m "refactor(scoring): one implementation of prev-played-status for live and replay"
```

______________________________________________________________________

### Task 2: Bound the prev-status traversal by recency

Pavlović's most recent played match is 2025/26 matchday 34, played 2026-05-16 —
an end-of-season unused-sub appearance in a dead rubber. Three months later that
single observation still yields `P(start) = 17%` against a fitted rate of 131
points if started, which is what puts him under the buy floor.

**Files:**

- Modify: `rehoboam/scoring/v2/adapter.py` (`prev_status_from_history`,
  `last_played_status`, `score_player_v2`)
- Modify: `rehoboam/config.py` (new `Settings` field)
- Modify: `rehoboam/trader.py:459,496` (pass the setting)
- Modify: `rehoboam/replay/driver.py` (pass the same bound)
- Test: `tests/test_scoring_v2/test_adapter.py`

**Interfaces:**

- Consumes: `prev_status_from_history` from Task 1

- Produces: `prev_status_from_history(history, *, now: datetime | None = None, max_age_days: float | None = None) -> int | None`;
  `last_played_status(performance, *, now=None, max_age_days=None)`;
  `score_player_v2(data, *, max_status_age_days: float | None = None)`;
  `Settings.max_status_age_days: float`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scoring_v2/test_adapter.py`:

```python
from datetime import datetime, timedelta, timezone


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestPrevStatusRecency:
    def test_stale_status_is_discarded(self):
        """The live Pavlovic case: an unused-sub appearance from 3 months ago."""
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        history = [("2026-05-16T13:30:00Z", 4)]
        assert prev_status_from_history(history, now=now, max_age_days=60.0) is None

    def test_recent_status_is_kept(self):
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        history = [(_iso(now - timedelta(days=7)), 4)]
        assert prev_status_from_history(history, now=now, max_age_days=60.0) == 4

    def test_no_bound_keeps_current_behaviour(self):
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        history = [("2026-05-16T13:30:00Z", 4)]
        assert prev_status_from_history(history, now=now) == 4

    def test_unparseable_date_is_treated_as_stale(self):
        """Fail closed: an unknown date cannot be shown to be recent."""
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        assert (
            prev_status_from_history([("not-a-date", 5)], now=now, max_age_days=60.0)
            is None
        )

    def test_missing_date_is_treated_as_stale(self):
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        assert prev_status_from_history([(None, 5)], now=now, max_age_days=60.0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring_v2/test_adapter.py::TestPrevStatusRecency -v`
Expected: FAIL with `TypeError: prev_status_from_history() got an unexpected keyword argument 'now'`

- [ ] **Step 3: Implement the bound**

Replace `prev_status_from_history` in `rehoboam/scoring/v2/adapter.py`:

```python
def prev_status_from_history(
    history: Sequence[tuple[str | None, int | None]],
    *,
    now: datetime | None = None,
    max_age_days: float | None = None,
) -> int | None:
    """Most recent *played* status from ``(match_date, status)`` pairs.

    Input is ordered oldest-first. Unplayed fixtures (status 0 or absent)
    describe a match that has not happened, not a state the player was in, so
    they are skipped.

    When ``max_age_days`` is set, a status older than that is discarded and
    None is returned instead. The availability model treats None as "no
    evidence" and falls back to its marginal prior, which is a weaker claim
    than a stale transition rather than a stronger one.

    Why this matters: without a bound, the last matchday of the previous season
    drives availability for the whole of the next one. End-of-season status is
    a bad prior -- squads rotate through dead rubbers -- and it persists for the
    entire off-season.

    A date that is missing or unparseable is treated as stale. This guard
    exists for the case we cannot see, so it fails closed.

    This is the single implementation of that rule. The live scorer and the
    season replay both call it.
    """
    latest: int | None = None
    for match_date, status in history:
        if status not in PLAYED_STATUSES:
            continue
        if max_age_days is not None:
            parsed = _parse_iso(match_date)
            reference = now or datetime.now(tz=timezone.utc)
            if (
                parsed is None
                or (reference - parsed).total_seconds() > max_age_days * 86400
            ):
                continue
        latest = int(status)
    return latest


def _parse_iso(value: str | None) -> datetime | None:
    """Parse a Kickbase ISO match date, or None if it cannot be read."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
```

Add `from datetime import datetime, timezone` to the imports.

Note the loop `continue`s on a stale row rather than breaking, so a recent
match earlier in the list is still found if a later one is unreadable.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scoring_v2/test_adapter.py::TestPrevStatusRecency -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Thread the bound through `last_played_status` and `score_player_v2`**

In `rehoboam/scoring/v2/adapter.py`, change the signatures:

```python
def last_played_status(
    performance: dict | None,
    *,
    now: datetime | None = None,
    max_age_days: float | None = None,
) -> int | None:
```

and pass them through on the final line:

```python
return prev_status_from_history(
    [(md, st) for _key, md, st in ordered], now=now, max_age_days=max_age_days
)
```

Then in `score_player_v2`:

```python
def score_player_v2(
    data: PlayerData, *, max_status_age_days: float | None = None
) -> PlayerScore:
```

and its call:

```python
prev_status = last_played_status(data.performance, max_age_days=max_status_age_days)
```

The scorer keeps taking this as a parameter rather than reading `Settings`
itself: constructing `Settings` requires credentials, and `score_player_v2` is
a pure function used in tests that have none.

- [ ] **Step 6: Add the Settings field**

In `rehoboam/config.py`, next to the other scoring thresholds:

```python
max_status_age_days: float = Field(
    default=60.0,
    description=(
        "Discard a player's last-played availability status when it is older "
        "than this many days, falling back to the availability model's "
        "marginal prior. Must exceed the longest in-season gap (the winter "
        "break, roughly 30 days) and fall below the off-season gap (roughly "
        "90). Without it, the final matchday of the previous season drives "
        "availability for the whole of the next: on 2026-08-22 Pavlovic was "
        "scored P(start)=17% off an unused-sub appearance played 2026-05-16."
    ),
)
```

- [ ] **Step 7: Pass it at the live call sites**

In `rehoboam/trader.py`, at both `score_player_v2(data)` call sites (currently
lines 459 and 496), pass the setting:

```python
market_scores.append(
    score_player_v2(data, max_status_age_days=self.settings.max_status_age_days)
)
```

```python
squad_scores.append(
    score_player_v2(data, max_status_age_days=self.settings.max_status_age_days)
)
```

- [ ] **Step 8: Pass it in the replay**

In `rehoboam/replay/driver.py`, inside `score`, apply the same bound so the
harness measures the same rule. The replay scores at a simulated instant, so
pass that instant as `now` rather than wall-clock time:

```python
prev_status = prev_status_from_history(
    [(m.get("match_date"), m.get("status")) for m in history],
    now=datetime.fromtimestamp(at, tz=timezone.utc),
    max_age_days=float(Settings.model_fields["max_status_age_days"].default),
)
```

`Settings` and `_default` are already imported in this module for the bid
tiers; reuse the same pattern. Add `from datetime import datetime, timezone`
if not present.

- [ ] **Step 9: Run the replay — this one is expected to move**

Run: `uv run rehoboam replay-season 2>&1 | grep -iE "simulated|actual total|FINISHING|Total buys"`
Baseline: 26,960 simulated / 26,172 actual / +788 / 9th of 14.

Unlike Task 1, a change here is expected and is the point. Record the new
number. If it drops materially, that is a real signal — report it rather than
tuning `max_status_age_days` until the number improves.

- [ ] **Step 10: Full suite, lint, live smoke**

```bash
uv run pytest -q -m "not slow" && uv run ruff check rehoboam/ tests/
uv run rehoboam -v status 2>&1 | grep -iE "pavlovi|buy-skip Pavl|  BUY "
```

Expected: tests pass; Pavlović's EP is materially above 29.1 and he is no
longer skipped for `EP < min 35.0`.

- [ ] **Step 11: Commit**

```bash
git add rehoboam/scoring/v2/adapter.py rehoboam/config.py rehoboam/trader.py rehoboam/replay/driver.py tests/test_scoring_v2/test_adapter.py
git commit -m "fix(scoring): discard availability status older than the off-season gap"
```

______________________________________________________________________

### Task 3: Do not apply a fitted quality coefficient to non-top-flight history

`rate.quality` is a baked lookup of 389 `player_id → multiplier` values. Seven
of the 22 Kickbase-sold listings on 2026-08-22 have no `ap`/`tp` field at all on
`get_player_details` — no Bundesliga history — yet carry fitted multipliers
derived from 2. Bundesliga matches, and a data-quality grade of **A**.

`rate.predict` already falls back to `position_prior` when a player_id is absent
from `quality`. So the fix is to withhold the key, not to refit or discount.

**Replay note:** this task is **not** measurable by `replay-season`. The corpus
records no competition marker, so the harness cannot tell top-flight history
from second-division history. Do not expect the replay number to move, and do
not treat an unchanged number as evidence the change is safe. The unit tests and
the live dry-run are the evidence here.

**Files:**

- Modify: `rehoboam/scoring/v2/adapter.py` (`score_player_v2`, new helper)
- Test: `tests/test_scoring_v2/test_adapter.py`

**Interfaces:**

- Consumes: `PlayerData.player_details` (already carried by `DataCollector`)

- Produces: `has_top_flight_history(player_details: dict | None) -> bool`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scoring_v2/test_adapter.py`:

```python
from rehoboam.scoring.v2.adapter import has_top_flight_history


class TestTopFlightHistory:
    def test_player_with_average_points_has_top_flight_history(self):
        assert has_top_flight_history({"ap": 119, "tp": 2844}) is True

    def test_missing_ap_means_no_top_flight_history(self):
        """The live Elversberg case: full 2. Bundesliga record, no `ap` field."""
        assert has_top_flight_history({"fn": "Maximilian", "ln": "Rohr"}) is False

    def test_zero_ap_means_no_top_flight_history(self):
        assert has_top_flight_history({"ap": 0, "tp": 0}) is False

    def test_missing_details_is_not_a_claim_of_history(self):
        assert has_top_flight_history(None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring_v2/test_adapter.py::TestTopFlightHistory -v`
Expected: FAIL with `ImportError: cannot import name 'has_top_flight_history'`

- [ ] **Step 3: Implement the helper**

In `rehoboam/scoring/v2/adapter.py`:

```python
def has_top_flight_history(player_details: dict | None) -> bool:
    """Has this player ever recorded Bundesliga scoring?

    Kickbase omits ``ap``/``tp`` entirely for players with no top-flight
    appearances. On 2026-08-22 that was true of seven of 22 buyable listings —
    six from newly promoted clubs (Elversberg, Schalke, Paderborn) and one
    Mainz backup keeper. The keeper is why the signal is "no top-flight
    history" rather than "promoted club": it is broader, and it needs no
    annually-maintained list of who came up.
    """
    if not player_details:
        return False
    return bool(player_details.get("ap") or player_details.get("tp"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scoring_v2/test_adapter.py::TestTopFlightHistory -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Write the failing scorer test**

Add to `tests/test_scoring_v2/test_adapter.py`. Reuse the module's existing
`_player` and `_perf` helpers:

```python
class TestNonTopFlightUsesPositionPrior:
    def test_fitted_quality_is_withheld_without_top_flight_history(self):
        """A 2. Bundesliga record must not buy a confident Bundesliga rate."""
        from rehoboam.scoring.models import PlayerData

        player = _player("3284")  # Rohr, present in the fitted quality table
        matches = [
            {"day": d, "st": 5, "p": 100, "md": "2026-05-16T13:30:00Z"}
            for d in range(1, 35)
        ]
        with_history = PlayerData(
            player=player,
            performance=_perf(matches),
            player_details={"ap": 74, "tp": 1251},
            team_strength=None,
            opponent_strength=None,
            is_dgw=False,
        )
        without_history = PlayerData(
            player=player,
            performance=_perf(matches),
            player_details={"fn": "Maximilian", "ln": "Rohr"},
            team_strength=None,
            opponent_strength=None,
            is_dgw=False,
        )

        scored_with = score_player_v2(with_history)
        scored_without = score_player_v2(without_history)

        assert scored_without.expected_points < scored_with.expected_points
        assert scored_without.data_quality.grade != "A"
        assert any("position prior" in n for n in scored_without.notes)
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring_v2/test_adapter.py::TestNonTopFlightUsesPositionPrior -v`
Expected: FAIL — both scores are currently identical, so the first assert fails.

- [ ] **Step 7: Withhold the quality key in `score_player_v2`**

In `rehoboam/scoring/v2/adapter.py`, inside `score_player_v2`, compute a
quality key once and use it everywhere the player id currently feeds the rate
model. Replace the scoring block:

```python
    prev_status = last_played_status(data.performance, max_age_days=max_status_age_days)

    # Withhold the fitted quality coefficient from players whose fitted record
    # is not top-flight: `rate.predict` then falls back to the position prior,
    # which is exactly the cold-start path an unfitted player already takes.
    # This is NOT a discount multiplier — REH-80's blanket cold-start discount
    # was reverted for costing 782 points. It declines to apply a coefficient
    # fitted on inapplicable data.
    quality_key = player.id if has_top_flight_history(data.player_details) else None

    ep = compose_ep(quality_key, prev_status, position, availability, rate)

    dgw_multiplier = DGW_MULTIPLIER if data.is_dgw else 1.0
    ep *= dgw_multiplier

    probs = availability.predict(prev_status)
    notes = [
        f"v2: availability P(start)={probs[5]:.0%} "
        f"(prev status {prev_status if prev_status is not None else 'unknown'}), "
        f"rate={rate.predict(quality_key, 5, position):.0f} pts if started"
    ]
    if quality_key not in rate.quality:
        notes.append("No fitted quality — using position prior (cold start)")
```

and change the grade line in the `DataQuality` construction from
`grade="A" if player.id in rate.quality else "C"` to
`grade="A" if quality_key in rate.quality else "C"`.

Widen `compose_ep`'s first parameter annotation to `player_id: str | None` and
`rate.predict`'s to `player_id: str | None`. Both already behave correctly with
None — `dict.get(None)` returns None and triggers the position-prior fallback —
this only makes the type honest.

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run pytest tests/test_scoring_v2/test_adapter.py -v`
Expected: PASS, all classes.

- [ ] **Step 9: Full suite, lint, live smoke**

```bash
uv run pytest -q -m "not slow" && uv run ruff check rehoboam/ tests/
uv run rehoboam -v status 2>&1 | grep -E "  BUY |buy-skip Rohr|buy-skip Gyamerah"
```

Expected: Rohr's EP has fallen from 89.1 (roughly −16%, to about 80 before the
Task 2 effect) and he no longer outranks Pavlović.

- [ ] **Step 10: Run the replay and record it as uninformative**

Run: `uv run rehoboam replay-season 2>&1 | grep -iE "simulated|FINISHING"`

Record the number in the commit message **explicitly labelled as not
measuring this change**, for the reason in the task header. Do not present an
unchanged number as a pass.

- [ ] **Step 11: Commit**

```bash
git add rehoboam/scoring/v2/adapter.py rehoboam/scoring/v2/rate.py tests/test_scoring_v2/test_adapter.py
git commit -m "fix(scoring): withhold fitted quality from players with no top-flight history"
```

______________________________________________________________________

### Task 4: Absolute target bar

The bot ranks only by marginal gain against the current squad, so in a weak week
it spends slots on the best of a poor market. A player is now a **target** only
if his absolute expected points clear a league-elite bar; marginal gain still
decides price and displacement.

**Files:**

- Modify: `rehoboam/config.py` (new `Settings` field)
- Modify: `rehoboam/scoring/decision.py` (`DecisionEngine.__init__`, `recommend_buys`)
- Test: `tests/test_scoring/test_target_bar.py` (create)

**Interfaces:**

- Consumes: `PlayerScore.expected_points`

- Produces: `DecisionEngine(min_ep_to_buy, min_ep_upgrade, target_ep_bar: float = 0.0)`;
  `Settings.target_ep_bar: float`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scoring/test_target_bar.py`:

```python
"""The absolute target bar (2026-08-22 design).

Marginal gain answers "is he worth today's price and who does he displace".
It cannot answer "is this player worth a squad slot at all", because a large
marginal gain against a weak squad still describes a mediocre player. The bar
is that second, absolute question.
"""

from rehoboam.scoring.decision import DecisionEngine
from tests.test_scoring.test_trade_pairs import _make_player, _make_score, _squad


def _engine(bar):
    return DecisionEngine(min_ep_to_buy=35.0, min_ep_upgrade=40.0, target_ep_bar=bar)


def _recommend(engine, ep, *, is_emergency=False):
    squad_players, squad_scores = _squad()
    cand = _make_player("cand", "Midfielder", price=5_000_000)
    return engine.recommend_buys(
        market_scores=[_make_score("cand", ep, "Midfielder", price=5_000_000)],
        squad_scores=squad_scores,
        roster_context={},
        budget=80_000_000,
        market_players={"cand": cand},
        squad_players=squad_players,
        is_emergency=is_emergency,
    )


class TestTargetBar:
    def test_player_below_the_bar_is_not_recommended(self):
        """Large marginal gain against a weak squad is still a mediocre player."""
        assert _recommend(_engine(100.0), 70.0) == []

    def test_player_above_the_bar_is_recommended(self):
        assert len(_recommend(_engine(100.0), 120.0)) == 1

    def test_zero_bar_preserves_existing_behaviour(self):
        assert len(_recommend(_engine(0.0), 70.0)) == 1

    def test_the_bar_yields_in_an_emergency(self):
        """-100 for an empty slot dwarfs the cost of a mediocre signing."""
        assert len(_recommend(_engine(100.0), 70.0, is_emergency=True)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring/test_target_bar.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'target_ep_bar'`

- [ ] **Step 3: Add the constructor parameter**

In `rehoboam/scoring/decision.py`, change `DecisionEngine.__init__`:

```python
def __init__(
    self,
    min_ep_to_buy: float = 35.0,
    min_ep_upgrade: float = 40.0,
    target_ep_bar: float = 0.0,
) -> None:
    self.min_ep_to_buy = min_ep_to_buy
    self.min_ep_upgrade = min_ep_upgrade
    self.target_ep_bar = target_ep_bar
```

- [ ] **Step 4: Apply the bar in `recommend_buys`**

In `recommend_buys`, the existing minimum-EP skip block reads:

```python
            min_ep = 10.0 if is_emergency else self.min_ep_to_buy
            if ps.expected_points < min_ep:
```

Immediately after that block's `continue`, add:

```python
            # The bar is a "worth a squad slot at all" test, so it yields in an
            # emergency: an unfieldable squad needs bodies, and -100 for an
            # empty slot dwarfs the cost of a mediocre signing.
            if not is_emergency and ps.expected_points < self.target_ep_bar:
                logger.debug(
                    "buy-skip %s: EP %.1f below target bar %.1f — holding the slot",
                    player.last_name,
                    ps.expected_points,
                    self.target_ep_bar,
                )
                continue
```

The loop binds the market player as `player` (from
`market_players.get(ps.player_id)`), matching the neighbouring skip blocks.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_scoring/test_target_bar.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Add the Settings field and wire it**

In `rehoboam/config.py`:

```python
target_ep_bar: float = Field(
    default=0.0,
    description=(
        "Absolute expected points a market player must reach to count as a "
        "target worth a squad slot, independent of marginal gain against the "
        "current squad. 0.0 disables the bar and preserves pre-2026-08-22 "
        "behaviour. Derive this from the measured marginal-gain and EP "
        "distributions via `rehoboam derive-thresholds` once the market has "
        "repopulated — it reported n=0 on 2026-07-31 and n=9 on 2026-08-22, "
        "neither of which is enough to set it from."
    ),
)
```

In `rehoboam/trader.py`, where `DecisionEngine` is constructed, pass
`target_ep_bar=self.settings.target_ep_bar`.

**Ship the default at 0.0.** Setting a real bar is a separate, measured
decision — see the plan's closing note.

- [ ] **Step 7: Full suite, lint, replay**

```bash
uv run pytest -q -m "not slow" && uv run ruff check rehoboam/ tests/
uv run rehoboam replay-season 2>&1 | grep -iE "simulated|FINISHING"
```

Expected: tests pass; the replay is unchanged, because the shipped default of
0.0 disables the bar.

- [ ] **Step 8: Commit**

```bash
git add rehoboam/config.py rehoboam/scoring/decision.py rehoboam/trader.py tests/test_scoring/test_target_bar.py
git commit -m "feat(scoring): absolute target bar, disabled by default pending measurement"
```

______________________________________________________________________

### Task 5: Computed availability state and holding

`competitor_player_ids` already rebuilds the set of every player in every
opponent's squad each session (`trader.py:280`), and is currently used for
nothing but an `uncontested` metadata flag. Make "no target available" a state
the bot computes, logs, and acts on.

**Files:**

- Modify: `rehoboam/auto_trader.py` (`run_unified_trade_phase`)
- Test: `tests/test_target_hold_state.py` (create)

**Interfaces:**

- Consumes: `ctx.ep_result["competitor_player_ids"]`, `Settings.target_ep_bar`

- Produces: `_target_availability(buy_recs, competitor_ids, bar) -> dict` with
  keys `listed`, `owned_by_opponents`, `bar`

- [ ] **Step 1: Write the failing test**

Create `tests/test_target_hold_state.py`:

```python
"""Target availability is a computed, logged state — not an accident.

The bot holds slots when nothing worth having is listed. That is only
defensible if it can say how many targets exist and where they are.
"""

from types import SimpleNamespace

from rehoboam.auto_trader import _target_availability


def _rec(pid, ep):
    return SimpleNamespace(
        player=SimpleNamespace(id=pid, last_name=pid),
        score=SimpleNamespace(expected_points=ep),
    )


class TestTargetAvailability:
    def test_counts_listed_targets_above_the_bar(self):
        state = _target_availability(
            [_rec("a", 120.0), _rec("b", 50.0)], competitor_ids=set(), bar=100.0
        )
        assert state["listed"] == 1

    def test_targets_held_by_opponents_are_counted_separately(self):
        state = _target_availability(
            [_rec("a", 120.0)], competitor_ids={"a"}, bar=100.0
        )
        assert state["listed"] == 0
        assert state["owned_by_opponents"] == 1

    def test_no_bar_means_every_recommendation_is_a_target(self):
        state = _target_availability(
            [_rec("a", 120.0), _rec("b", 50.0)], competitor_ids=set(), bar=0.0
        )
        assert state["listed"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_target_hold_state.py -v`
Expected: FAIL with `ImportError: cannot import name '_target_availability'`

- [ ] **Step 3: Implement the helper**

In `rehoboam/auto_trader.py`, alongside the other module-level pure helpers
(`_available_squad_slots`, `_emergency_slots_short`,
`_starter_swap_has_recovery_time`):

```python
def _target_availability(buy_recs: list, competitor_ids: set, bar: float) -> dict:
    """How many targets exist, and where they are.

    A target is a player whose ABSOLUTE expected points clear the bar —
    "is he worth a squad slot at all" — as distinct from marginal gain, which
    answers "is he worth today's price and who does he displace".

    Split by where they sit, because the two states call for different
    behaviour: a target that is listed can be bid on now, while one sitting in
    an opponent's squad is a reason to keep a slot free rather than to act.
    """
    listed = 0
    owned = 0
    for rec in buy_recs:
        if rec.score.expected_points < bar:
            continue
        if rec.player.id in competitor_ids:
            owned += 1
        else:
            listed += 1
    return {"listed": listed, "owned_by_opponents": owned, "bar": bar}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_target_hold_state.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Log the state each session**

In `rehoboam/auto_trader.py`, inside `run_unified_trade_phase`, after
`buy_recs` and `trade_pairs` are read from `ctx.ep_result` and before the
candidate list is assembled, add:

```python
        target_state = _target_availability(
            buy_recs,
            ctx.ep_result.get("competitor_player_ids") or set(),
            self.settings.target_ep_bar,
        )
        logger.info(
            "target-availability listed=%d owned_by_opponents=%d bar=%.1f",
            target_state["listed"],
            target_state["owned_by_opponents"],
            target_state["bar"],
        )
```

Logging the state before acting on it is deliberate: with `target_ep_bar`
shipped at 0.0 this records what the bar *would* do for several sessions before
anything is gated on it, which is how the bar gets set from evidence rather
than from a guess.

- [ ] **Step 6: Full suite, lint, live smoke**

```bash
uv run pytest -q -m "not slow" && uv run ruff check rehoboam/ tests/
uv run rehoboam -v status 2>&1 | grep "target-availability"
```

Expected: one `target-availability` line per session with real counts.

- [ ] **Step 7: Commit**

```bash
git add rehoboam/auto_trader.py tests/test_target_hold_state.py
git commit -m "feat(trade): compute and log target availability from competitor ownership"
```

______________________________________________________________________

## Deliberately deferred

**Turning the bar on.** Every task ships `target_ep_bar` at 0.0, so behaviour is
unchanged until a value is chosen. Choosing it needs the distribution, and
`derive-thresholds` reported n=0 on 2026-07-31 and n=9 on 2026-08-22 — neither
is enough. Run it again once the market has repopulated, read the percentiles,
set the bar from `.env`, and watch the `target-availability` log line. Setting
it now would repeat the mistake that produced the current pre-season thresholds.

**The hold behaviour itself.** With the bar at 0.0 there is nothing to hold for.
Once the bar is live and the log shows the state behaving sensibly, gating buys
on `target_state["listed"] > 0` is a small follow-up. The spec's two exceptions
must be honoured when it lands: the emergency fieldability fill
(`_emergency_slots_short`, REH-82) overrides the hold, and position minimums
still bind. An unfieldable squad is never an acceptable state to hold in.

**A rate-model refit excluding second-division matches.** Task 3 achieves the
same outcome for affected players at serving time. A refit is larger work and
would also let the replay measure it — see REH-88 and REH-90.

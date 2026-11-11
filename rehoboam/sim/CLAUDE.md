# rehoboam/sim — the v3 verdict

Backtest and paper trading for `rehoboam/desk` (spec v3 §8, ADR-0005). Read-only against the
store. Every number a decision rests on goes in the spec's run log (§12).

- `history.py` loads the history. `value_day` is the 22:00 Berlin rule: keep it identical to
  migration 024. Value paths are `mv_series` with gaps filled from `player_status_daily`
  (`mv_series` is refreshed weekly).
- `auction.py`: would a bid have won? Against the league's real buyer, or at the value at
  expiry when nobody bought it.
- `validate.py` (`rehoboam desk-validate-sim`): the simulator reproduces every squad, every
  value and our wallet. Run it before trusting any counterfactual.
- `drift_report.py` (`rehoboam desk-drift-report`): M1, out-of-sample drift.
- `engine.py` + `backtest.py` (`rehoboam desk-backtest`): the planner replayed twice a day
  against real auctions and real points, beside every rival.
- Paper trading is this backtest on a window that didn't exist when the configuration was
  committed. The commit's timestamp is the pre-registration.
- The rolling freeze (ADR-0007): each frozen configuration is a tag `v3-frozen-N`. Score it
  from a worktree of the tag, never from main, so the code is the code that was frozen:
  `git worktree add ../v3-frozen-N v3-frozen-N && cd ../v3-frozen-N && uv run rehoboam desk-holdout --frozen-at <tag time> …`. The frozen configurations and their flags are listed
  in the spec's run log.

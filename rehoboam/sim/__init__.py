"""The v3 verdict: backtest and paper trading (spec v3 §8, ADR-0005).

The same `desk` planner runs here against history (backtest) or against the
newest data with a persistent paper book (paper). Read-only against the store,
except for the run and paper tables it owns.
"""

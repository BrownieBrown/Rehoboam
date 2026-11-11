"""The v3 decision layer (spec 2026-11-11-rehoboam-v3-design.md, ADR-0001..0006).

Pure: frozen dataclasses and functions, no I/O. The same code runs behind the
backtest, paper and live adapters, so what the verdict measures is what trades.
Nothing here may import the legacy decision modules (spec §6, I-A1).
"""

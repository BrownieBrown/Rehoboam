"""The request budget the ingestion (and its league-wide refresh) spend against.

Split out of ``ingest.py`` so ``league_refresh.py`` can catch and re-raise
``BudgetExhausted`` without importing ``ingest`` back (``ingest.py`` imports
``league_refresh`` to call ``run_league_refresh``). Re-exported from
``ingest.py`` so existing ``from rehoboam.enrichment.ingest import
BudgetExhausted`` imports keep working.
"""

from __future__ import annotations


class BudgetExhausted(Exception):
    """Raised by ``IngestBudget.spend`` between requests; never mid-write."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

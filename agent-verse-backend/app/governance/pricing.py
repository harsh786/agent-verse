"""Cost display helpers.

Pricing itself has one source of truth:
:func:`app.intelligence.cost_tracker.calculate_cost` (``model_pricing`` table
overlay, then the reference table, then the configurable fallback). The old
per-1k fragment table and ``estimate_cost`` were removed because they priced the
same call differently from the ledger.
"""

from __future__ import annotations


def format_cost(usd: float) -> str:
    """Format cost for display: $0.0012 or $1.23."""
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.2f}"

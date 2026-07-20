"""Deterministic public research payload for credential-free demo mode."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def synthetic_demo_research_report() -> dict[str, Any]:
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "dataset": "omega-synthetic-v1",
        "symbols": ["AAPL", "MSFT", "NVDA", "SPY"],
        "portfolio_assessment": {
            "weights": {"AAPL": 0.30, "MSFT": 0.25, "NVDA": 0.20, "SPY": 0.25},
            "largest_position_weight": 0.30,
            "annualized_volatility": 0.1842,
            "historical_var_95_daily": 0.0187,
            "flags": [],
        },
        "optimization": {
            "method": "deterministic minimum-variance example",
            "weights": {"AAPL": 0.22, "MSFT": 0.24, "NVDA": 0.14, "SPY": 0.40},
            "status": "demo",
        },
        "option_scenario": {
            "contract": "synthetic 105 call",
            "spot_shocks": [-0.10, -0.05, 0.0, 0.05, 0.10],
            "prices_at_30_days": [0.1847, 0.6685, 2.3724, 5.7674, 10.4463],
        },
        "disclaimer": "Synthetic, reproducible data only. Not investment advice.",
    }

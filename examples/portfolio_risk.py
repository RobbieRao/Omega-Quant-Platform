"""Credential-free concentration and historical-risk example."""

from __future__ import annotations

import json

import numpy as np


def main() -> None:
    rng = np.random.default_rng(20260720)
    weights = np.array([0.40, 0.30, 0.20, 0.10])
    means = np.array([0.0005, 0.0004, 0.0006, 0.0002])
    covariance = np.array([
        [0.00040, 0.00018, 0.00016, 0.00005],
        [0.00018, 0.00030, 0.00014, 0.00004],
        [0.00016, 0.00014, 0.00055, 0.00003],
        [0.00005, 0.00004, 0.00003, 0.00008],
    ])
    returns = rng.multivariate_normal(means, covariance, size=252)
    portfolio = returns @ weights
    var_95 = -float(np.quantile(portfolio, 0.05))
    tail = portfolio[portfolio <= np.quantile(portfolio, 0.05)]
    print(json.dumps({
        "seed": 20260720,
        "weights": weights.tolist(),
        "annualized_volatility": float(portfolio.std(ddof=1) * np.sqrt(252)),
        "historical_var_95_daily": var_95,
        "historical_cvar_95_daily": -float(tail.mean()),
        "largest_weight": float(weights.max()),
    }, indent=2))


if __name__ == "__main__":
    main()

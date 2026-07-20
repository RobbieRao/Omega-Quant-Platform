"""Deterministic Black-Scholes option scenario matrix without market data."""

from __future__ import annotations

import json
import math


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def call_price(spot: float, strike: float, years: float, rate: float, volatility: float) -> float:
    if years <= 0:
        return max(spot - strike, 0.0)
    sigma_t = volatility * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + volatility**2 / 2) * years) / sigma_t
    d2 = d1 - sigma_t
    return spot * normal_cdf(d1) - strike * math.exp(-rate * years) * normal_cdf(d2)


def main() -> None:
    base_spot, strike, volatility, rate = 100.0, 105.0, 0.30, 0.04
    rows = []
    for days in (30, 14, 1):
        rows.append({
            "days_to_expiry": days,
            "prices": {
                f"spot_{shock:+.0%}": round(call_price(base_spot * (1 + shock), strike, days / 365, rate, volatility), 4)
                for shock in (-0.10, -0.05, 0.0, 0.05, 0.10)
            },
        })
    print(json.dumps({"contract": "synthetic 105 call", "volatility": volatility, "matrix": rows}, indent=2))


if __name__ == "__main__":
    main()

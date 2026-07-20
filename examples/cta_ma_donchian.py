"""Reproducible MA/Donchian signal example using Omega's generated bars."""

from __future__ import annotations

import json

from omega_quant.data import generate_seed_bars


def main() -> None:
    bars = generate_seed_bars("OMEGA-DEMO", periods=120).copy()
    bars["ma_fast"] = bars["close"].rolling(10).mean()
    bars["ma_slow"] = bars["close"].rolling(30).mean()
    bars["donchian_high"] = bars["high"].rolling(20).max().shift(1)
    bars["donchian_low"] = bars["low"].rolling(20).min().shift(1)
    bars["long_signal"] = (bars["ma_fast"] > bars["ma_slow"]) & (bars["close"] > bars["donchian_high"])
    bars["exit_signal"] = bars["close"] < bars["donchian_low"]
    latest = bars.iloc[-1]
    print(json.dumps({
        "dataset": "omega-seed-bars-v1",
        "rows": len(bars),
        "latest_close": float(latest["close"]),
        "long_signals": int(bars["long_signal"].sum()),
        "exit_signals": int(bars["exit_signal"].sum()),
    }, indent=2))


if __name__ == "__main__":
    main()

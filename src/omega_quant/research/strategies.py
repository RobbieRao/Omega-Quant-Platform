from __future__ import annotations

from typing import Iterable


def vectorbt_available() -> bool:
    try:
        import vectorbt  # noqa: F401
    except Exception:
        return False
    return True


def run_sma_crossover_grid(
    close,
    fast_windows: Iterable[int] = range(5, 31, 5),
    slow_windows: Iterable[int] = range(30, 121, 15),
    init_cash: float = 1000.0,
):
    """Run a vectorbt SMA crossover grid when vectorbt is installed.

    This is intentionally a small adapter. Strategy research should stay in
    notebooks or scripts that call this function, while vectorbt remains the
    engine doing the real work.
    """
    import vectorbt as vbt

    windows = sorted(set(fast_windows).union(slow_windows))
    ma = vbt.MA.run(close, window=windows)
    results = []
    for fast in fast_windows:
        for slow in slow_windows:
            if fast >= slow:
                continue
            entries = ma.ma_crossed_above(ma, fast, slow)
            exits = ma.ma_crossed_below(ma, fast, slow)
            portfolio = vbt.Portfolio.from_signals(
                close,
                entries,
                exits,
                init_cash=init_cash,
                fees=0.001,
                freq="1D",
            )
            results.append(
                {
                    "fast": fast,
                    "slow": slow,
                    "total_return": portfolio.total_return(),
                    "max_drawdown": portfolio.max_drawdown(),
                    "sharpe": portfolio.sharpe_ratio(),
                }
            )
    return results

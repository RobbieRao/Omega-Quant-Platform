from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ..compat import install_runtime_shims
from .risk import as_float, option_days_to_expiry, snapshot_valuation_date
from ..utils import finite_float


install_runtime_shims()


@dataclass(frozen=True)
class PriceHistoryResult:
    close: pd.DataFrame
    source: str
    error: str | None
    attempts: list[dict[str, str | None]]


def scalar(value: Any) -> Any:
    if hasattr(value, "iloc"):
        try:
            return value.iloc[0]
        except Exception:
            return value
    if isinstance(value, (list, tuple)) and len(value) == 1:
        return scalar(value[0])
    if hasattr(value, "shape") and getattr(value, "size", None) == 1:
        try:
            return value.item()
        except Exception:
            pass
    if hasattr(value, "flat"):
        try:
            return next(iter(value.flat))
        except Exception:
            pass
    return value


def portfolio_symbols(assessment: dict[str, Any]) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for row in assessment.get("positions") or []:
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
    return symbols


def current_weights_by_symbol(assessment: dict[str, Any]) -> dict[str, float]:
    net_liquidation = as_float(assessment.get("net_liquidation"), default=0.0)
    weights: dict[str, float] = {}
    if not net_liquidation:
        return weights
    for row in assessment.get("positions") or []:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        weights[symbol] = weights.get(symbol, 0.0) + as_float(row.get("market_value")) / net_liquidation
    return weights


def fetch_price_history(
    symbols: list[str],
    period: str = "6mo",
    settings: Any = None,
) -> pd.DataFrame:
    return fetch_price_history_result(symbols, period=period, settings=settings).close


def fetch_price_history_result(
    symbols: list[str],
    period: str = "6mo",
    settings: Any = None,
) -> PriceHistoryResult:
    if not symbols:
        return PriceHistoryResult(pd.DataFrame(), "none", None, [])

    days = {"1mo": 35, "3mo": 95, "6mo": 185, "1y": 370, "2y": 740}.get(period, 185)
    start = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    attempts: list[dict[str, str | None]] = []

    # Massive (primary)
    if settings is not None and getattr(settings, "massive_api_key", None):
        try:
            from .massive_client import fetch_close_prices as _massive_close

            close = _massive_close(symbols, settings, start=start)
            if not close.empty:
                attempts.append({"source": "massive", "status": "ok", "error": None})
                return PriceHistoryResult(close, "massive", None, attempts)
            attempts.append({"source": "massive", "status": "empty", "error": "no rows returned"})
        except Exception as exc:
            attempts.append({"source": "massive", "status": "error", "error": str(exc)})

    # yfinance (fallback)
    try:
        import yfinance as yf

        raw = yf.download(
            tickers=" ".join(symbols),
            period=period,
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=False,
        )
        close = extract_close_prices(raw, symbols)
        if not close.empty:
            attempts.append({"source": "yfinance", "status": "ok", "error": None})
            return PriceHistoryResult(close, "yfinance", None, attempts)
        attempts.append({"source": "yfinance", "status": "empty", "error": "no rows returned"})
    except Exception as exc:
        attempts.append({"source": "yfinance", "status": "error", "error": str(exc)})

    error = "; ".join(
        f"{attempt['source']}: {attempt.get('error') or attempt.get('status')}"
        for attempt in attempts
        if attempt.get("error") or attempt.get("status") != "ok"
    )
    return PriceHistoryResult(pd.DataFrame(), "none", error or "no history rows returned", attempts)


def extract_close_prices(raw: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()

    close = pd.DataFrame(index=raw.index)
    if isinstance(raw.columns, pd.MultiIndex):
        for symbol in symbols:
            series = None
            for key in ((symbol, "Close"), ("Close", symbol)):
                if key in raw.columns:
                    series = raw[key]
                    break
            if series is not None:
                close[symbol] = series
    elif "Close" in raw.columns and len(symbols) == 1:
        close[symbols[0]] = raw["Close"]

    close = close.apply(pd.to_numeric, errors="coerce")
    close = close.dropna(axis=1, how="all").dropna(axis=0, how="all")
    return close


def latest_price_map(close: pd.DataFrame) -> dict[str, float]:
    if close.empty:
        return {}
    latest = close.ffill().iloc[-1]
    return {
        str(symbol): float(value)
        for symbol, value in latest.items()
        if finite_float(value) is not None
    }


def normalized_prices(close: pd.DataFrame) -> pd.DataFrame:
    prices = close.ffill().dropna(axis=1, how="all").dropna(axis=0, how="any")
    return prices.loc[:, prices.nunique(dropna=True) > 1]


def run_vectorbt_sma(close: pd.DataFrame) -> dict[str, Any]:
    try:
        import vectorbt as vbt
    except Exception as exc:
        return {"status": "error", "message": f"vectorbt 导入失败：{exc}", "rows": []}

    rows: list[dict[str, Any]] = []
    for symbol in close.columns:
        price = close[symbol].dropna()
        if len(price) < 40:
            rows.append(
                {
                    "symbol": symbol,
                    "status": "数据不足",
                    "total_return": None,
                    "max_drawdown": None,
                    "sharpe": None,
                    "trades": 0,
                }
            )
            continue

        fast = price.rolling(10).mean()
        slow = price.rolling(30).mean()
        entries = ((fast > slow) & (fast.shift(1) <= slow.shift(1))).fillna(False)
        exits = ((fast < slow) & (fast.shift(1) >= slow.shift(1))).fillna(False)
        portfolio = vbt.Portfolio.from_signals(
            price,
            entries,
            exits,
            init_cash=10_000,
            fees=0.001,
            freq="1D",
        )
        rows.append(
            {
                "symbol": symbol,
                "status": "已运行",
                "total_return": finite_float(scalar(portfolio.total_return())),
                "max_drawdown": finite_float(scalar(portfolio.max_drawdown())),
                "sharpe": finite_float(scalar(portfolio.sharpe_ratio())),
                "trades": int(finite_float(scalar(portfolio.trades.count())) or 0),
            }
        )

    return {
        "status": "ok",
        "message": "vectorbt：6 个月日线，SMA 10/30 交叉信号回测。",
        "rows": rows,
    }


def run_pypfopt_min_vol(close: pd.DataFrame, current_weights: dict[str, float]) -> dict[str, Any]:
    prices = normalized_prices(close)
    if prices.shape[1] < 2 or len(prices) < 30:
        return {"status": "skipped", "message": "PyPortfolioOpt 需要至少两个标的和足够历史价格。", "rows": []}

    try:
        from pypfopt import EfficientFrontier, expected_returns, risk_models
    except Exception as exc:
        return {"status": "error", "message": f"PyPortfolioOpt 导入失败：{exc}", "rows": []}

    try:
        mu = expected_returns.mean_historical_return(prices)
        cov = risk_models.sample_cov(prices)
        efficient_frontier = EfficientFrontier(mu, cov, weight_bounds=(0, 1))
        efficient_frontier.min_volatility()
        weights = efficient_frontier.clean_weights()
        expected_return, volatility, sharpe = efficient_frontier.portfolio_performance()
    except Exception as exc:
        return {
            "status": "error",
            "message": f"PyPortfolioOpt 优化失败：{exc}",
            "performance": None,
            "rows": [],
        }

    rows = [
        {
            "symbol": symbol,
            "target_weight": finite_float(weight) or 0.0,
            "current_weight": current_weights.get(symbol, 0.0),
            "delta_weight": (finite_float(weight) or 0.0) - current_weights.get(symbol, 0.0),
        }
        for symbol, weight in weights.items()
    ]
    return {
        "status": "ok",
        "message": "PyPortfolioOpt：最小波动组合权重，用来和当前仓位做 sanity check。",
        "performance": {
            "expected_return": finite_float(expected_return),
            "volatility": finite_float(volatility),
            "sharpe": finite_float(sharpe),
        },
        "rows": rows,
    }


def run_riskfolio_min_risk(close: pd.DataFrame, current_weights: dict[str, float]) -> dict[str, Any]:
    prices = normalized_prices(close)
    if prices.shape[1] < 2 or len(prices) < 30:
        return {"status": "skipped", "message": "Riskfolio-Lib 需要至少两个标的和足够历史价格。", "rows": []}

    os.environ.setdefault(
        "MPLCONFIGDIR",
        str(Path(tempfile.gettempdir()) / "omega-quant-mpl"),
    )
    try:
        import riskfolio as rp
    except Exception as exc:
        return {"status": "error", "message": f"Riskfolio-Lib 导入失败：{exc}", "rows": []}

    try:
        returns = prices.pct_change(fill_method=None).dropna()
        portfolio = rp.Portfolio(returns=returns)
        portfolio.assets_stats(method_mu="hist", method_cov="hist")
        weights = portfolio.optimization(
            model="Classic",
            rm="MV",
            obj="MinRisk",
            rf=0,
            l=0,
            hist=True,
        )
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Riskfolio-Lib 优化失败：{exc}",
            "rows": [],
        }

    if weights is None or weights.empty:
        return {
            "status": "error",
            "message": "Riskfolio-Lib 没有返回可用权重。",
            "rows": [],
        }

    weight_series = weights.iloc[:, 0]
    rows = [
        {
            "symbol": str(symbol),
            "target_weight": finite_float(weight) or 0.0,
            "current_weight": current_weights.get(str(symbol), 0.0),
            "delta_weight": (finite_float(weight) or 0.0) - current_weights.get(str(symbol), 0.0),
        }
        for symbol, weight in weight_series.items()
    ]
    return {
        "status": "ok",
        "message": "Riskfolio-Lib：Classic / MV / MinRisk，用作第二套风险优化交叉验证。",
        "rows": rows,
    }


def run_fast_vollib_greeks(
    snapshot: dict[str, Any],
    latest_prices: dict[str, float],
) -> dict[str, Any]:
    option_positions = ((snapshot.get("positions") or {}).get("OPT") or [])
    if not option_positions:
        return {"status": "skipped", "message": "当前快照里没有期权仓位。", "rows": []}

    try:
        from fast_vollib.api import get_all_greeks
        from fast_vollib.implied_volatility import fast_implied_volatility
    except Exception as exc:
        return {"status": "error", "message": f"fast-vollib 导入失败：{exc}", "rows": []}

    valuation_date = snapshot_valuation_date(snapshot)
    rows: list[dict[str, Any]] = []
    ok_count = 0
    for position in option_positions:
        contract = position.get("contract") or {}
        symbol = str(contract.get("symbol") or "").strip().upper()
        display = contract.get("display") or symbol
        dte = option_days_to_expiry(position, valuation_date)
        strike = finite_float(contract.get("strike"))
        option_price = finite_float(position.get("market_price"))
        underlying_price = latest_prices.get(symbol)
        flag = "c" if str(contract.get("put_call") or "").upper().startswith("C") else "p"
        base_row = {
            "display": display,
            "symbol": symbol,
            "strike": strike,
            "option_price": option_price,
            "underlying_price": underlying_price,
            "dte": dte,
            "iv": None,
            "delta": None,
            "theta": None,
            "vega": None,
            "status": "待计算",
        }

        if not all(value is not None for value in (strike, option_price, underlying_price, dte)):
            base_row["status"] = "缺少行情或合约字段"
            rows.append(base_row)
            continue

        years = max(float(dte or 0), 1.0) / 365.0
        try:
            iv = finite_float(
                scalar(
                    fast_implied_volatility(
                        option_price,
                        underlying_price,
                        strike,
                        years,
                        0.04,
                        flag,
                        return_as="numpy",
                        on_error="ignore",
                    )
                )
            )
            if iv is None or iv <= 0:
                base_row["status"] = "IV 无法反解"
                rows.append(base_row)
                continue

            greeks = get_all_greeks(
                flag,
                underlying_price,
                strike,
                years,
                0.04,
                iv,
                return_as="dict",
            )
            base_row.update(
                {
                    "iv": iv,
                    "delta": finite_float(scalar(greeks.get("delta"))),
                    "theta": finite_float(scalar(greeks.get("theta"))),
                    "vega": finite_float(scalar(greeks.get("vega"))),
                    "status": "已运行",
                }
            )
            ok_count += 1
        except Exception as exc:
            base_row["status"] = f"计算失败：{exc}"
        rows.append(base_row)

    status = "ok" if ok_count == len(option_positions) else "partial" if ok_count else "error"
    return {
        "status": status,
        "message": "fast-vollib：用最新底层价格反解 IV，再计算 Delta / Theta / Vega。",
        "rows": rows,
    }


def build_research_report(snapshot: dict[str, Any], assessment: dict[str, Any], *, settings: Any = None) -> dict[str, Any]:
    symbols = portfolio_symbols(assessment)
    current_weights = current_weights_by_symbol(assessment)
    generated_at = datetime.now(timezone.utc).isoformat()

    try:
        history = fetch_price_history_result(symbols, settings=settings)
        close = history.close
        history_error = history.error
    except Exception as exc:
        history = PriceHistoryResult(pd.DataFrame(), "none", str(exc), [])
        close = pd.DataFrame()
        history_error = str(exc)

    latest_prices = latest_price_map(close)
    report = {
        "generatedAt": generated_at,
        "symbols": symbols,
        "history": {
            "source": history.source,
            "period": "6mo",
            "rows": int(len(close)),
            "columns": list(close.columns),
            "error": history_error,
            "attempts": history.attempts,
        },
        "vectorbt": run_vectorbt_sma(close) if history_error is None else {
            "status": "error",
            "message": f"vectorbt 需要历史价格；价格获取失败：{history_error}",
            "rows": [],
        },
        "pypfopt": run_pypfopt_min_vol(close, current_weights) if history_error is None else {
            "status": "error",
            "message": f"PyPortfolioOpt 需要历史价格；价格获取失败：{history_error}",
            "rows": [],
        },
        "riskfolio": run_riskfolio_min_risk(close, current_weights) if history_error is None else {
            "status": "error",
            "message": f"Riskfolio-Lib 需要历史价格；价格获取失败：{history_error}",
            "rows": [],
        },
        "fast_vollib": run_fast_vollib_greeks(snapshot, latest_prices),
    }
    return report

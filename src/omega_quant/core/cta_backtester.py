"""Minimal vn.py-style CTA backtester core for Omega."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import product
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ..vendor.vnpy_backtesting_pnl import PortfolioDailyResult, VnpyStyleTrade
from .data_manager import DataManagerEngine
from .cta_utility import BarGenerator
from .data_recorder import replay_bars, replay_ticks
from .engine import BaseEngine, MainEngine
from .event import EventEngine
from .objects import Direction, OrderData, OrderType, Status, TradeData
from .strategy_template import CtaStrategyTemplate, get_cta_strategy_class, list_cta_strategy_metadata


# --- Tiger Brokers 真实手续费费率（USD，2026 年公开费率，可在 Tiger 客户端覆盖） ---
# 股票每订单最低 1.99 = 佣金 0.99 + 平台费 1.00
# 期权每订单最低 0.99 = 佣金 0 + 平台费 0.99
TIGER_FEE_PRESETS: dict[str, dict[str, float]] = {
    "tiger_stock": {
        "commission_per_unit": 0.0049,
        "commission_min": 0.99,
        "platform_fee_per_unit": 0.005,
        "platform_fee_min": 1.0,
    },
    "tiger_option": {
        "commission_per_unit": 0.0,
        "commission_min": 0.0,
        "platform_fee_per_unit": 0.30,
        "platform_fee_min": 0.99,
    },
}


@dataclass(frozen=True)
class CtaBacktestRequest:
    vt_symbol: str
    interval: str = "1m"
    data_source: str = "bar"
    strategy_class: str = "MovingAverageCrossStrategy"
    strategy_setting: dict[str, Any] = field(default_factory=dict)
    start: datetime | None = None
    end: datetime | None = None
    fast_window: int = 5
    slow_window: int = 20
    capital: float = 100_000.0
    rate: float = 0.0005
    slippage: float = 0.01
    size: float = 1.0
    # 费率模型: "percent" = 老式 rate*notional; "tiger_stock" / "tiger_option" = Tiger 真实分层 + 最低; "custom" = 用下面四个字段
    fee_model: str = "percent"
    commission_per_unit: float = 0.0049  # 美元/股 或 美元/合约
    commission_min_per_order: float = 0.99
    platform_fee_per_unit: float = 0.005
    platform_fee_min_per_order: float = 1.0
    # 仓位规则: "strategy" 尊重策略 volume; "full_capital" 用满现金; "fixed_pct" 用 capital 的 X%; "fixed_dollars" 固定美元
    position_sizing_mode: str = "strategy"
    position_sizing_value: float = 1.0  # interpretation: pct (0-1) 或 美元


def compute_trade_fee(
    *,
    fee_model: str,
    quantity: float,
    fill_price: float,
    size_multiplier: float,
    commission_per_unit: float = 0.0049,
    commission_min: float = 0.99,
    platform_fee_per_unit: float = 0.005,
    platform_fee_min: float = 1.0,
    rate_pct_of_notional: float = 0.0005,
) -> dict[str, float]:
    """Return per-trade {commission, platform_fee, total_fee} according to model.

    - "percent": commission = notional * rate_pct, platform_fee = 0 (legacy)
    - "tiger_stock" / "tiger_option" / "custom": commission_per_unit * qty (min commission_min) +
      platform_fee_per_unit * qty (min platform_fee_min). Note Tiger sums them per order.

    Units: 'unit' = share for stocks (size_multiplier=1) or contract for options (size_multiplier=100).
    The per-unit fees do NOT scale by size_multiplier — Tiger charges per contract, not per share-equivalent.
    """

    model = (fee_model or "percent").lower()
    notional = quantity * fill_price * size_multiplier
    if model == "percent":
        commission = notional * float(rate_pct_of_notional or 0.0)
        platform_fee = 0.0
    else:
        if model in TIGER_FEE_PRESETS and (commission_per_unit, platform_fee_per_unit) == (0.0049, 0.005):
            preset = TIGER_FEE_PRESETS[model]
            cu, cmin = preset["commission_per_unit"], preset["commission_min"]
            pu, pmin = preset["platform_fee_per_unit"], preset["platform_fee_min"]
        else:
            cu, cmin = float(commission_per_unit), float(commission_min)
            pu, pmin = float(platform_fee_per_unit), float(platform_fee_min)
        commission = max(cu * quantity, cmin)
        platform_fee = max(pu * quantity, pmin)
    return {
        "commission": round(commission, 6),
        "platform_fee": round(platform_fee, 6),
        "total_fee": round(commission + platform_fee, 6),
        "notional": round(notional, 4),
    }


def resolve_position_quantity(
    *,
    mode: str,
    sizing_value: float,
    strategy_volume: float,
    available_cash: float,
    fill_price: float,
    size_multiplier: float,
) -> float:
    """Map (strategy.volume, sizing rule) → actual quantity for a backtest fill.

    Returns floor-rounded integer quantity ≥ 0. size_multiplier is the contract size
    (1 for stock, 100 for option); ``cash / (price * size_multiplier)`` is the max
    affordable units.
    """

    if fill_price <= 0 or size_multiplier <= 0:
        return float(strategy_volume)
    mode = (mode or "strategy").lower()
    if mode == "strategy":
        return float(max(strategy_volume, 0.0))
    if mode == "full_capital":
        units = int(max(available_cash, 0.0) // (fill_price * size_multiplier))
        return float(max(units, 0))
    if mode == "fixed_pct":
        pct = max(0.0, min(float(sizing_value or 0.0), 1.0))
        target_dollars = max(available_cash, 0.0) * pct
        units = int(target_dollars // (fill_price * size_multiplier))
        return float(max(units, 0))
    if mode == "fixed_dollars":
        target = max(float(sizing_value or 0.0), 0.0)
        units = int(target // (fill_price * size_multiplier))
        return float(max(units, 0))
    return float(max(strategy_volume, 0.0))


class CtaBacktesterEngine(BaseEngine):
    """Database-backed moving-average CTA backtester.

    This is intentionally small but follows vn.py's CtaBacktester flow:
    load historical bars from database, run strategy, calculate trades/costs,
    then return statistics suitable for Web UI rendering.
    """

    engine_name = "cta_backtester"

    def __init__(
        self,
        main_engine: MainEngine,
        event_engine: EventEngine,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        super().__init__(main_engine, event_engine, self.engine_name)
        self.session_factory = session_factory
        self.datetime: datetime | None = None
        self.limit_order_count = 0
        self.trade_count = 0
        self.active_limit_orders: dict[str, OrderData] = {}
        self.limit_orders: dict[str, OrderData] = {}
        self.trades: dict[str, TradeData] = {}
        self.logs: list[str] = []
        self.current_bar_index: int = -1
        self._order_bar_index: dict[str, int] = {}
        self._order_stop_trigger: dict[str, float] = {}

    def bind_session_factory(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def run_backtest(self, request: CtaBacktestRequest) -> dict[str, Any]:
        strategy_class = get_cta_strategy_class(request.strategy_class)
        setting = self._build_strategy_setting(request, strategy_class)
        return self.run_strategy_class_backtest(
            request,
            strategy_class=strategy_class,
            setting=setting,
        )

    def list_strategies(self) -> list[dict[str, Any]]:
        return [metadata.__dict__ for metadata in list_cta_strategy_metadata()]

    def run_strategy_class_backtest(
        self,
        request: CtaBacktestRequest,
        *,
        strategy_class: type[CtaStrategyTemplate],
        setting: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        required = _required_bars(setting or {})
        bars, data_info = self._load_backtest_bars(request)
        if len(bars) < required:
            return {
                "status": "insufficient_data",
                "message": data_info["insufficient_message"],
                "data_source": request.data_source,
                "source": data_info["source"],
                "tick_count": data_info["tick_count"],
                "bar_count": len(bars),
                "required_bars": required,
            }

        self._reset_backtest_state()
        cash = float(request.capital)
        position = 0.0
        entry_price = 0.0
        equity_curve: list[dict[str, Any]] = []
        trade_rows: list[dict[str, Any]] = []
        daily_closes: dict[date, float] = {}
        strategy = strategy_class(self, strategy_class.__name__, request.vt_symbol, setting or {})
        strategy.on_init()
        strategy.on_start()
        for bar_index, bar in enumerate(bars):
            self.datetime = bar.datetime
            self.current_bar_index = bar_index
            close = float(bar.close_price)
            daily_closes[bar.datetime.date()] = close
            # 撮合: 仅匹配上一根 bar 之前提交的 pending 订单 (杜绝同 bar 成交)
            self._cross_orders(strategy, bar, request)
            strategy.on_bar(bar)
            # ❌ 此处不再二次撮合 - 新提交的订单等待下一根 bar
            for trade in list(self.trades.values())[len(trade_rows) :]:
                strategy_qty = trade.volume
                fill = trade.price
                if trade.direction == Direction.LONG:
                    quantity = resolve_position_quantity(
                        mode=request.position_sizing_mode,
                        sizing_value=request.position_sizing_value,
                        strategy_volume=strategy_qty,
                        available_cash=cash,
                        fill_price=fill,
                        size_multiplier=request.size,
                    )
                else:
                    # closing/short: cap at current long position so we don't go short by accident
                    quantity = strategy_qty if request.position_sizing_mode == "strategy" else min(
                        position, resolve_position_quantity(
                            mode=request.position_sizing_mode,
                            sizing_value=request.position_sizing_value,
                            strategy_volume=strategy_qty,
                            available_cash=cash,
                            fill_price=fill,
                            size_multiplier=request.size,
                        ) or position
                    )
                if quantity <= 0:
                    continue
                fee = compute_trade_fee(
                    fee_model=request.fee_model,
                    quantity=quantity,
                    fill_price=fill,
                    size_multiplier=request.size,
                    commission_per_unit=request.commission_per_unit,
                    commission_min=request.commission_min_per_order,
                    platform_fee_per_unit=request.platform_fee_per_unit,
                    platform_fee_min=request.platform_fee_min_per_order,
                    rate_pct_of_notional=request.rate,
                )
                commission = fee["total_fee"]
                slippage_cost = quantity * request.size * request.slippage
                if trade.direction == Direction.LONG:
                    cash -= quantity * fill * request.size + commission + slippage_cost
                    position += quantity
                    entry_price = fill
                    pnl = None
                else:
                    pnl = (fill - entry_price) * quantity * request.size - commission - slippage_cost
                    cash += quantity * fill * request.size - commission - slippage_cost
                    position -= quantity
                    if position <= 0:
                        entry_price = 0.0
                trade_rows.append(
                    {
                        "datetime": trade.datetime.isoformat(),
                        "vt_symbol": trade.vt_symbol,
                        "direction": trade.direction.value,
                        "price": fill,
                        "volume": quantity,
                        "commission": fee["commission"],
                        "platform_fee": fee["platform_fee"],
                        "total_fee": fee["total_fee"],
                        "notional": fee["notional"],
                        "slippage": request.slippage,
                        "slippage_cost": slippage_cost,
                        "pnl": pnl,
                        "trade_date": trade.datetime.date().isoformat(),
                    }
                )
            equity_curve.append({"datetime": bar.datetime.isoformat(), "equity": cash + position * close * request.size})

        daily_rows, vnpy_statistics = _calculate_vnpy_daily_results(
            request=request,
            vt_symbol=request.vt_symbol.upper(),
            daily_closes=daily_closes,
            trade_rows=trade_rows,
        )
        final_equity = float(vnpy_statistics.get("end_balance") or equity_curve[-1]["equity"])
        closed_trade_pnls = [float(trade["pnl"]) for trade in trade_rows if trade.get("pnl") is not None]
        wins = [value for value in closed_trade_pnls if value > 0]
        losses = [value for value in closed_trade_pnls if value < 0]
        max_dd_pct_raw = vnpy_statistics.get("max_ddpercent")
        if max_dd_pct_raw is not None:
            max_drawdown = float(max_dd_pct_raw) / 100.0
        else:
            max_drawdown = _max_drawdown([item["equity"] for item in equity_curve], request.capital)
        max_drawdown_value = float(vnpy_statistics.get("max_drawdown") or _max_drawdown_dollars([item["equity"] for item in equity_curve], request.capital))
        total_commission = float(vnpy_statistics.get("total_commission") or 0.0)
        total_turnover = float(vnpy_statistics.get("total_turnover") or 0.0)
        total_slippage = sum(float(trade.get("slippage_cost") or 0.0) for trade in trade_rows)

        # Buy-and-hold 基准: 期初买入, 期末卖出, 同样的 fee + slip 模型, 一次 round trip
        buy_and_hold = _compute_buy_and_hold(bars, request)

        strategy.on_stop()
        return {
            "status": "completed",
            "engine": "omega_cta_backtester",
            "source": f"{data_info['source']}; pnl=vn.py PortfolioDailyResult daily mark-to-market",
            "data_source": request.data_source,
            "strategy_class": strategy_class.__name__,
            "vt_symbol": request.vt_symbol.upper(),
            "interval": request.interval,
            "tick_count": data_info["tick_count"],
            "bar_count": len(bars),
            "trade_count": len(trade_rows),
            "closed_trade_count": len(closed_trade_pnls),
            "capital": request.capital,
            "final_equity": final_equity,
            "total_return": final_equity / request.capital - 1.0,
            "max_drawdown": max_drawdown,
            "max_drawdown_value": max_drawdown_value,
            "sharpe_ratio": float(vnpy_statistics.get("sharpe_ratio") or 0.0),
            "annual_return": float(vnpy_statistics.get("annual_return_percent") or 0.0) / 100.0,
            "return_drawdown_ratio": float(vnpy_statistics.get("return_drawdown_ratio") or 0.0),
            "win_rate": (len(wins) / len(closed_trade_pnls)) if closed_trade_pnls else 0.0,
            "average_win": (sum(wins) / len(wins)) if wins else 0.0,
            "average_loss": (sum(losses) / len(losses)) if losses else 0.0,
            "gross_pnl": sum(closed_trade_pnls),
            "net_pnl": final_equity - request.capital,
            "total_commission": total_commission,
            "total_turnover": total_turnover,
            "total_slippage": total_slippage,
            "buy_and_hold": buy_and_hold,
            "alpha_vs_buy_and_hold": (final_equity / request.capital - 1.0) - buy_and_hold["total_return"],
            "daily_results": daily_rows,
            "vnpy_statistics": vnpy_statistics,
            "pnl_source": "vn.py-style PortfolioDailyResult: daily holding_pnl + trading_pnl - commission",
            "data_range": {
                "start": bars[0].datetime.isoformat() if bars else None,
                "end": bars[-1].datetime.isoformat() if bars else None,
                "rows": len(bars),
                "source": data_info["source"],
            },
            "trades": trade_rows,
            "equity_curve": equity_curve,
            "logs": self.logs,
            # Only expose parameters the strategy class actually declares
            # (avoids leaking CtaBacktestRequest fields like rate/size into the
            # strategy-facing parameter map). Execution config exposed separately.
            "parameters": {k: v for k, v in (setting or {}).items() if k in strategy_class.parameters},
            "execution_config": {
                "rate": request.rate,
                "slippage": request.slippage,
                "size": request.size,
                "capital": request.capital,
            },
        }

    def _load_backtest_bars(self, request: CtaBacktestRequest) -> tuple[list[Any], dict[str, Any]]:
        manager = DataManagerEngine(self.main_engine, self.event_engine, self.session_factory)
        data_source = request.data_source.strip().lower()
        if data_source in {"bar", "bars", "kline", "k_line"}:
            rows = manager.load_bars(request.vt_symbol, interval=request.interval, start=request.start, end=request.end)
            return replay_bars(rows), {
                "source": "recorded_bars: vn.py CtaBacktester workflow adapted for Omega Web backend",
                "tick_count": None,
                "insufficient_message": "历史K线数量不足，回测终止。",
            }
        if data_source in {"tick", "ticks"}:
            if request.interval != "1m":
                raise ValueError("tick 回测当前仅支持 interval=1m")
            rows = manager.load_ticks(request.vt_symbol, start=request.start, end=request.end, limit=1_000_000)
            ticks = replay_ticks(rows)
            bars: list[Any] = []
            generator = BarGenerator(bars.append)
            for tick in ticks:
                generator.update_tick(tick)
            generator.generate()
            return bars, {
                "source": "recorded_ticks: vn.py BarGenerator tick-to-1m-bar replay",
                "tick_count": len(ticks),
                "insufficient_message": "历史Tick数量不足或无法聚合出足够K线，回测终止。",
            }
        raise ValueError("data_source 仅支持 bar 或 tick")

    def optimize(self, base_request: CtaBacktestRequest, *, fast_values: list[int], slow_values: list[int], target: str = "total_return") -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for fast, slow in product(fast_values, slow_values):
            if fast >= slow:
                continue
            request = CtaBacktestRequest(**{**base_request.__dict__, "fast_window": fast, "slow_window": slow})
            result = self.run_backtest(request)
            if result.get("status") == "completed":
                results.append({"fast_window": fast, "slow_window": slow, target: result.get(target), "result": result})
        return sorted(results, key=lambda item: float(item.get(target) or 0.0), reverse=True)

    def walk_forward(
        self,
        base_request: CtaBacktestRequest,
        *,
        is_fraction: float = 0.6,
        n_folds: int = 4,
    ) -> dict[str, Any]:
        """Walk-forward validation.

        Splits the available bar range into ``n_folds`` rolling windows. In each
        fold, the first ``is_fraction`` of the window is the in-sample (IS) used
        to *report* the strategy with current settings; the remainder is the
        out-of-sample (OOS) on which we measure stability. This is a sanity
        check — pair with ``optimize_generic`` if you want IS-tuned parameters
        validated on OOS.
        """

        from datetime import timedelta

        if not 0.1 < is_fraction < 0.95:
            raise ValueError("is_fraction 必须在 (0.1, 0.95) 范围内")
        if n_folds < 2:
            raise ValueError("n_folds 必须 >= 2")

        bars, _info = self._load_backtest_bars(base_request)
        if not bars:
            raise ValueError("没有可用的历史 K 线数据")
        timestamps = [b.datetime for b in bars]
        start_ts, end_ts = timestamps[0], timestamps[-1]
        total_span = end_ts - start_ts
        if total_span.total_seconds() <= 0:
            raise ValueError("回测区间过短")

        fold_span_seconds = total_span.total_seconds() / n_folds
        results: list[dict[str, Any]] = []
        for i in range(n_folds):
            fold_start = start_ts + timedelta(seconds=i * fold_span_seconds)
            fold_end = start_ts + timedelta(seconds=(i + 1) * fold_span_seconds)
            split = fold_start + timedelta(seconds=(fold_end - fold_start).total_seconds() * is_fraction)
            is_req = CtaBacktestRequest(**{**base_request.__dict__, "start": fold_start, "end": split})
            oos_req = CtaBacktestRequest(**{**base_request.__dict__, "start": split, "end": fold_end})
            is_res = self.run_backtest(is_req)
            oos_res = self.run_backtest(oos_req)
            results.append({
                "fold": i + 1,
                "is_start": fold_start.isoformat(),
                "is_end": split.isoformat(),
                "oos_start": split.isoformat(),
                "oos_end": fold_end.isoformat(),
                "is_total_return": is_res.get("total_return"),
                "is_sharpe": is_res.get("sharpe_ratio"),
                "is_max_drawdown": is_res.get("max_drawdown"),
                "is_trade_count": is_res.get("trade_count"),
                "oos_total_return": oos_res.get("total_return"),
                "oos_sharpe": oos_res.get("sharpe_ratio"),
                "oos_max_drawdown": oos_res.get("max_drawdown"),
                "oos_trade_count": oos_res.get("trade_count"),
            })
        return {
            "algorithm": "walk_forward",
            "n_folds": n_folds,
            "is_fraction": is_fraction,
            "results": results,
        }

    def monte_carlo(
        self,
        base_request: CtaBacktestRequest,
        *,
        n_simulations: int = 200,
        method: str = "trade_shuffle",
    ) -> dict[str, Any]:
        """Monte Carlo robustness simulation.

        ``method='trade_shuffle'``: take per-trade PnL series from the real
        backtest and randomly reshuffle the order ``n_simulations`` times. For
        each shuffled order, compute the resulting equity curve and capture
        final equity + max drawdown. Returns distribution stats.
        """

        import random as _random
        import statistics as _stats

        base_result = self.run_backtest(base_request)
        if base_result.get("status") != "completed":
            raise ValueError("Monte Carlo 需要一次成功的 base 回测先跑出 trades")
        trade_pnls: list[float] = []
        for trade in (base_result.get("trades") or []):
            pnl = trade.get("pnl")
            if isinstance(pnl, (int, float)) and pnl != 0:
                trade_pnls.append(float(pnl))
        if not trade_pnls:
            # fall back to per-trade marked PnL diff via daily_results
            for d in (base_result.get("daily_results") or []):
                if isinstance(d.get("trading_pnl"), (int, float)) and d["trading_pnl"] != 0:
                    trade_pnls.append(float(d["trading_pnl"]))
        if not trade_pnls:
            raise ValueError("基准回测没有产生任何带 PnL 的交易，无法 Monte Carlo")
        capital = float(base_request.capital)
        finals: list[float] = []
        max_dds: list[float] = []
        for _ in range(int(n_simulations)):
            order = list(trade_pnls)
            if method == "trade_shuffle":
                _random.shuffle(order)
            elif method == "bootstrap":
                order = [_random.choice(trade_pnls) for _ in range(len(trade_pnls))]
            else:
                raise ValueError(f"未知 method: {method}")
            equity = capital
            peak = capital
            max_dd_ratio = 0.0
            for p in order:
                equity += p
                if equity > peak:
                    peak = equity
                if peak > 0:
                    max_dd_ratio = min(max_dd_ratio, (equity - peak) / peak)
            finals.append(equity)
            max_dds.append(max_dd_ratio)

        finals.sort()
        max_dds.sort()
        def _pct(arr: list[float], p: float) -> float:
            idx = max(0, min(len(arr) - 1, int(p * (len(arr) - 1))))
            return arr[idx]

        return {
            "algorithm": "monte_carlo",
            "method": method,
            "n_simulations": int(n_simulations),
            "n_trades": len(trade_pnls),
            "base_final_equity": base_result.get("final_equity"),
            "base_max_drawdown": base_result.get("max_drawdown"),
            "final_equity_stats": {
                "mean": _stats.fmean(finals),
                "median": _stats.median(finals),
                "stdev": _stats.pstdev(finals),
                "p05": _pct(finals, 0.05),
                "p25": _pct(finals, 0.25),
                "p75": _pct(finals, 0.75),
                "p95": _pct(finals, 0.95),
                "min": finals[0],
                "max": finals[-1],
            },
            "max_drawdown_stats": {
                "mean": _stats.fmean(max_dds),
                "median": _stats.median(max_dds),
                "p05": _pct(max_dds, 0.05),
                "p95": _pct(max_dds, 0.95),
                "worst": max_dds[0],
                "best": max_dds[-1],
            },
            "histogram": {
                "final_equity": finals,
                "max_drawdown": max_dds,
            },
        }

    def optimize_generic(
        self,
        base_request: CtaBacktestRequest,
        *,
        parameter_values: dict[str, list[Any]],
        target: str = "total_return",
    ) -> dict[str, Any]:
        """Generic brute-force optimization over arbitrary parameter sets.

        ``parameter_values`` is e.g. ``{"fast_window": [3,5,8], "slow_window": [20,30]}``.
        Each combination is run through ``run_backtest`` after merging into
        ``strategy_setting``. Returns the top sorted results plus the raw
        per-combination score table.
        """

        if not parameter_values:
            raise ValueError("parameter_values 不能为空")
        # max_drawdown is returned as a NEGATIVE ratio (-0.05 = 5% drawdown), so larger
        # (closer to 0) is better. Standard "maximize" semantics applies to all targets.
        worse_is_better = False
        keys = list(parameter_values.keys())
        value_lists = [parameter_values[k] for k in keys]
        combos = list(product(*value_lists))
        results: list[dict[str, Any]] = []
        for combo in combos:
            overrides = dict(zip(keys, combo, strict=False))
            merged = dict(base_request.strategy_setting or {})
            merged.update(overrides)
            request = CtaBacktestRequest(**{**base_request.__dict__, "strategy_setting": merged})
            result = self.run_backtest(request)
            if result.get("status") != "completed":
                continue
            metric = result.get(target)
            metric_val = float(metric) if metric is not None else None
            row: dict[str, Any] = {target: metric_val, "parameters": overrides, "result": result}
            results.append(row)
        results.sort(
            key=lambda row: (row[target] if row[target] is not None else (float("inf") if worse_is_better else float("-inf"))),
            reverse=not worse_is_better,
        )
        return {
            "algorithm": "brute_force",
            "target": target,
            "tested_combinations": len(combos),
            "completed_runs": len(results),
            "parameter_names": keys,
            "results": results[:50],
        }

    def optimize_ga_generic(
        self,
        base_request: CtaBacktestRequest,
        *,
        parameter_ranges: dict[str, dict[str, Any]] | None = None,
        parameter_values: dict[str, list[Any]] | None = None,
        target: str = "total_return",
        population_size: int = 20,
        generations: int = 10,
        crossover_prob: float = 0.7,
        mutation_prob: float = 0.3,
    ) -> dict[str, Any]:
        """Genetic algorithm over arbitrary parameters using deap.

        - ``parameter_ranges``: continuous/stepped numeric search space {low, high, step, type}
        - ``parameter_values``: discrete candidate sets [v1, v2, v3]; GA picks via random.choice
        - Single-element values act as fixed and don't really vary, but still appear in
          ``parameter_names`` so 2-axis heatmaps render correctly.
        """

        import random as _random
        from deap import base as _base, creator as _creator, tools as _tools  # type: ignore

        parameter_ranges = parameter_ranges or {}
        parameter_values = parameter_values or {}
        if not parameter_ranges and not parameter_values:
            raise ValueError("parameter_ranges / parameter_values 至少一个非空")

        # Build unified spec list keyed by parameter name. "kind" distinguishes range vs values.
        specs: list[dict[str, Any]] = []
        names: list[str] = []
        for name, raw_spec in parameter_ranges.items():
            spec = dict(raw_spec)
            spec.setdefault("type", "int")
            spec["low"] = float(spec["low"])
            spec["high"] = float(spec["high"])
            spec["step"] = float(spec.get("step", 1))
            if spec["low"] >= spec["high"] or spec["step"] <= 0:
                raise ValueError(f"参数 {name} 范围非法: {spec}")
            spec["kind"] = "range"
            specs.append(spec)
            names.append(name)
        for name, raw_values in parameter_values.items():
            if name in parameter_ranges:
                continue  # range takes precedence if both supplied
            values_list = list(raw_values or [])
            if not values_list:
                continue
            spec = {"kind": "values", "values": values_list, "type": "int" if all(isinstance(v, int) for v in values_list) else "float"}
            specs.append(spec)
            names.append(name)
        if not specs:
            raise ValueError("没有可变参数, 无法运行遗传算法")

        # max_drawdown is returned as a NEGATIVE ratio (-0.05 = 5% drawdown), so larger
        # (closer to 0) is better. Standard "maximize" semantics applies to all targets.
        worse_is_better = False
        weight = -1.0 if worse_is_better else 1.0
        suffix = f"{int(weight*10)}_{len(names)}"
        fitness_attr = f"FitOmega_{suffix}"
        ind_attr = f"IndOmega_{suffix}"
        if not hasattr(_creator, fitness_attr):
            _creator.create(fitness_attr, _base.Fitness, weights=(weight,))
            _creator.create(ind_attr, list, fitness=getattr(_creator, fitness_attr))
        individual_cls = getattr(_creator, ind_attr)

        def _cast(value: Any, spec: dict[str, Any]) -> Any:
            if spec.get("kind") == "values":
                values_list = spec["values"]
                # Snap to nearest value in the discrete set
                try:
                    target = float(value)
                    return min(values_list, key=lambda v: abs(float(v) - target))
                except (TypeError, ValueError):
                    return values_list[0]
            if spec["type"] == "int":
                return int(round(float(value)))
            return round(float(value) / spec["step"]) * spec["step"]

        def _sample(spec: dict[str, Any]) -> Any:
            if spec.get("kind") == "values":
                return _random.choice(spec["values"])
            if spec["type"] == "int":
                return _random.randint(int(spec["low"]), int(spec["high"]))
            steps = int((spec["high"] - spec["low"]) / spec["step"])
            return spec["low"] + _random.randint(0, max(1, steps)) * spec["step"]

        cache: dict[tuple, tuple[float | None, dict[str, Any]]] = {}

        def evaluate(ind: list[Any]) -> tuple[float]:
            overrides: dict[str, Any] = {}
            for i, spec in enumerate(specs):
                if spec.get("kind") == "values":
                    overrides[names[i]] = _cast(ind[i], spec)
                else:
                    clamped = max(spec["low"], min(spec["high"], float(ind[i])))
                    overrides[names[i]] = _cast(clamped, spec)
            key = tuple(overrides[n] for n in names)
            if key in cache:
                metric = cache[key][0]
                return (metric if metric is not None else (-1e18 if not worse_is_better else 1e18),)
            merged = dict(base_request.strategy_setting or {})
            merged.update(overrides)
            request = CtaBacktestRequest(**{**base_request.__dict__, "strategy_setting": merged})
            result = self.run_backtest(request)
            if result.get("status") != "completed":
                metric = None
                cache[key] = (metric, result)
                return (-1e18 if not worse_is_better else 1e18,)
            metric_raw = result.get(target)
            metric = float(metric_raw) if metric_raw is not None else None
            cache[key] = (metric, result)
            return (metric if metric is not None else (-1e18 if not worse_is_better else 1e18),)

        toolbox = _base.Toolbox()
        toolbox.register("individual", lambda: individual_cls([_sample(s) for s in specs]))
        toolbox.register("population", _tools.initRepeat, list, toolbox.individual)
        toolbox.register("evaluate", evaluate)
        toolbox.register("mate", _tools.cxTwoPoint)
        toolbox.register("select", _tools.selTournament, tournsize=3)

        def mutate(ind: list[Any]) -> tuple[list[Any]]:
            idx = _random.randrange(len(specs))
            ind[idx] = _sample(specs[idx])
            return (ind,)

        toolbox.register("mutate", mutate)

        population = toolbox.population(n=int(population_size))
        for ind in population:
            ind.fitness.values = toolbox.evaluate(ind)

        history: list[dict[str, Any]] = []
        for gen in range(int(generations)):
            offspring = list(map(toolbox.clone, toolbox.select(population, len(population))))
            for c1, c2 in zip(offspring[::2], offspring[1::2]):
                if _random.random() < crossover_prob:
                    toolbox.mate(c1, c2)
                    del c1.fitness.values
                    del c2.fitness.values
            for mut in offspring:
                if _random.random() < mutation_prob:
                    toolbox.mutate(mut)
                    del mut.fitness.values
            for ind in offspring:
                if not ind.fitness.valid:
                    ind.fitness.values = toolbox.evaluate(ind)
            population[:] = offspring
            best = max(population, key=lambda ind: ind.fitness.values[0])
            def _materialize(idx: int) -> Any:
                spec_i = specs[idx]
                if spec_i.get("kind") == "values":
                    return _cast(best[idx], spec_i)
                clamped_v = max(spec_i["low"], min(spec_i["high"], float(best[idx])))
                return _cast(clamped_v, spec_i)

            history.append({
                "generation": gen + 1,
                "best_parameters": {names[i]: _materialize(i) for i in range(len(names))},
                "best_score": float(best.fitness.values[0]),
            })

        sorted_cache = sorted(
            [(k, v) for k, v in cache.items() if v[0] is not None],
            key=lambda item: item[1][0],
            reverse=not worse_is_better,
        )
        top: list[dict[str, Any]] = []
        for key, (metric, result) in sorted_cache[:20]:
            top.append({
                "parameters": dict(zip(names, key, strict=False)),
                target: metric,
                "result": result,
            })
        return {
            "algorithm": "genetic_algorithm",
            "target": target,
            "population_size": int(population_size),
            "generations": int(generations),
            "parameter_names": names,
            "tested_combinations": len(cache),
            "history": history,
            "results": top,
        }

    @staticmethod
    def _build_strategy_setting(
        request: CtaBacktestRequest,
        strategy_class: type[CtaStrategyTemplate],
    ) -> dict[str, Any]:
        """Build the actual setting dict applied to the strategy.

        Source order (later overrides earlier):
          1. Legacy ``request.fast_window`` / ``request.slow_window`` if the
             strategy declares those parameter names (kept for backward-compat
             with old tests + the deprecated ``/optimize`` endpoint).
          2. ``request.strategy_setting`` (user-provided per-parameter overrides).

        Class defaults are NOT pre-populated here — the strategy ``__init__``
        already picks them up from class attributes, only declared overrides
        need to be passed through ``setting``.
        """

        setting: dict[str, Any] = {}
        if "fast_window" in strategy_class.parameters:
            setting["fast_window"] = request.fast_window
        if "slow_window" in strategy_class.parameters:
            setting["slow_window"] = request.slow_window
        if request.strategy_setting:
            setting.update(request.strategy_setting)
        return setting

    def send_order(
        self,
        strategy: CtaStrategyTemplate,
        vt_symbol: str,
        direction: Direction,
        price: float,
        volume: float,
        order_type: OrderType = OrderType.LIMIT,
        stop_price: float | None = None,
    ) -> list[str]:
        """提交订单到回测撮合队列。

        - LIMIT: ``price`` = 限价
        - MARKET: ``price`` 仅做参考；成交价 = 下一根 bar 的 open + slip
        - STOP (stop-market): ``price`` = 触发价；触发后以市价成交
        - STOP_LIMIT: ``price`` = 限价；``stop_price`` = 触发价
        """

        self.limit_order_count += 1
        symbol, exchange_text = vt_symbol.upper().split(".", 1)
        from .objects import Exchange

        order = OrderData(
            symbol=symbol,
            exchange=Exchange(exchange_text),
            orderid=str(self.limit_order_count),
            direction=direction,
            type=order_type,
            volume=volume,
            price=float(price),
            status=Status.SUBMITTING,
            datetime=self.datetime,
            gateway_name="BACKTESTING",
            reference=strategy.strategy_name,
        )
        self.active_limit_orders[order.vt_orderid] = order
        self.limit_orders[order.vt_orderid] = order
        # 标记订单提交时所在 bar，禁止同 bar 成交
        self._order_bar_index[order.vt_orderid] = self.current_bar_index
        if order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
            trigger = stop_price if stop_price is not None else float(price)
            self._order_stop_trigger[order.vt_orderid] = float(trigger)
        return [order.vt_orderid]

    def cancel_order(self, strategy: CtaStrategyTemplate, vt_orderid: str) -> None:
        order = self.active_limit_orders.pop(vt_orderid, None)
        if not order:
            return
        cancelled = OrderData(**{**order.__dict__, "status": Status.CANCELLED})
        self.limit_orders[vt_orderid] = cancelled
        self._order_bar_index.pop(vt_orderid, None)
        self._order_stop_trigger.pop(vt_orderid, None)
        strategy.update_order(cancelled)

    def write_log(self, msg: str, strategy: CtaStrategyTemplate | None = None) -> None:
        prefix = f"{self.datetime.isoformat()} " if self.datetime else ""
        name = f"[{strategy.strategy_name}] " if strategy else ""
        self.logs.append(f"{prefix}{name}{msg}")

    def _reset_backtest_state(self) -> None:
        self.datetime = None
        self.limit_order_count = 0
        self.trade_count = 0
        self.active_limit_orders.clear()
        self.limit_orders.clear()
        self.trades.clear()
        self.logs.clear()
        self.current_bar_index = -1
        # 每个订单提交时所在的 bar index，用于禁止同 bar 成交
        self._order_bar_index: dict[str, int] = {}
        # STOP 与 STOP_LIMIT 的触发价；LIMIT/MARKET 不需要
        self._order_stop_trigger: dict[str, float] = {}

    def _cross_orders(self, strategy: CtaStrategyTemplate, bar, request: CtaBacktestRequest) -> None:
        """Match pending orders against this bar; skip same-bar orders; branch by type."""

        slip = float(request.slippage)
        open_p = float(bar.open_price)
        high_p = float(bar.high_price)
        low_p = float(bar.low_price)

        for vt_orderid, order in list(self.active_limit_orders.items()):
            submitted_idx = self._order_bar_index.get(vt_orderid, -1)
            if submitted_idx >= self.current_bar_index:
                if order.status == Status.SUBMITTING:
                    updated = OrderData(**{**order.__dict__, "status": Status.NOTTRADED})
                    self.active_limit_orders[vt_orderid] = updated
                    self.limit_orders[vt_orderid] = updated
                    strategy.update_order(updated)
                continue

            if order.status == Status.SUBMITTING:
                updated = OrderData(**{**order.__dict__, "status": Status.NOTTRADED})
                self.active_limit_orders[vt_orderid] = updated
                self.limit_orders[vt_orderid] = updated
                strategy.update_order(updated)
                order = updated

            order_type = order.type
            order_price = float(order.price or 0.0)
            is_long = order.direction == Direction.LONG
            fill_price: float | None = None

            if order_type == OrderType.MARKET:
                fill_price = open_p + slip if is_long else open_p - slip

            elif order_type == OrderType.LIMIT:
                if is_long:
                    if low_p <= order_price:
                        fill_price = (open_p if open_p < order_price else order_price) + slip
                else:
                    if high_p >= order_price:
                        fill_price = (open_p if open_p > order_price else order_price) - slip

            elif order_type == OrderType.STOP:
                trigger = self._order_stop_trigger.get(vt_orderid, order_price)
                if is_long:
                    if high_p >= trigger:
                        triggered_price = open_p if open_p >= trigger else trigger
                        fill_price = triggered_price + slip
                else:
                    if low_p <= trigger:
                        triggered_price = open_p if open_p <= trigger else trigger
                        fill_price = triggered_price - slip

            elif order_type == OrderType.STOP_LIMIT:
                trigger = self._order_stop_trigger.get(vt_orderid, order_price)
                limit = order_price
                if is_long:
                    if high_p >= trigger and low_p <= limit:
                        triggered_price = open_p if open_p >= trigger else trigger
                        fill_price = (min(triggered_price, limit) if open_p >= trigger else limit) + slip
                else:
                    if low_p <= trigger and high_p >= limit:
                        triggered_price = open_p if open_p <= trigger else trigger
                        fill_price = (max(triggered_price, limit) if open_p <= trigger else limit) - slip

            else:
                if is_long and low_p <= order_price:
                    fill_price = order_price + slip
                elif not is_long and high_p >= order_price:
                    fill_price = order_price - slip

            if fill_price is None:
                continue

            fill_price = max(fill_price, 0.0)
            self.trade_count += 1
            filled = OrderData(**{**order.__dict__, "status": Status.ALLTRADED, "traded": order.volume})
            self.active_limit_orders.pop(vt_orderid, None)
            self.limit_orders[vt_orderid] = filled
            self._order_bar_index.pop(vt_orderid, None)
            self._order_stop_trigger.pop(vt_orderid, None)
            strategy.update_order(filled)
            trade = TradeData(
                symbol=order.symbol,
                exchange=order.exchange,
                tradeid=str(self.trade_count),
                orderid=order.orderid,
                direction=order.direction,
                volume=order.volume,
                price=fill_price,
                datetime=bar.datetime,
                gateway_name="BACKTESTING",
            )
            strategy.update_trade(trade)
            self.trades[trade.vt_tradeid] = trade


def _max_drawdown(equity: list[float], initial: float) -> float:
    """Return max drawdown as a ratio (negative, e.g. -0.05 for -5%)."""
    peak = initial
    drawdown_ratio = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            drawdown_ratio = min(drawdown_ratio, (value - peak) / peak)
    return drawdown_ratio


def _max_drawdown_dollars(equity: list[float], initial: float) -> float:
    """Return max drawdown as a raw dollar amount (negative)."""
    peak = initial
    drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        drawdown = min(drawdown, value - peak)
    return drawdown


def _compute_buy_and_hold(bars: list, request: "CtaBacktestRequest") -> dict[str, Any]:
    """期初满仓买入, 期末平仓, 用相同的 fee + slip 模型 — 直接对比策略实际增量价值."""

    if not bars or len(bars) < 2:
        return {
            "total_return": 0.0,
            "final_equity": float(request.capital),
            "entry_price": 0.0,
            "exit_price": 0.0,
            "shares": 0.0,
            "fees": 0.0,
            "max_drawdown": 0.0,
        }

    capital = float(request.capital)
    slip = float(request.slippage)
    size_mul = float(request.size)
    first = bars[0]
    last = bars[-1]
    entry_price = float(first.open_price) + slip
    exit_price = max(float(last.close_price) - slip, 0.0)

    shares = math.floor(capital / (entry_price * size_mul)) if entry_price > 0 else 0.0
    if shares <= 0:
        return {
            "total_return": 0.0,
            "final_equity": capital,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "shares": 0.0,
            "fees": 0.0,
            "max_drawdown": 0.0,
        }

    entry_fee = compute_trade_fee(
        fee_model=request.fee_model, quantity=shares, fill_price=entry_price,
        size_multiplier=size_mul,
        commission_per_unit=request.commission_per_unit,
        commission_min=request.commission_min_per_order,
        platform_fee_per_unit=request.platform_fee_per_unit,
        platform_fee_min=request.platform_fee_min_per_order,
        rate_pct_of_notional=request.rate,
    )["total_fee"]
    exit_fee = compute_trade_fee(
        fee_model=request.fee_model, quantity=shares, fill_price=exit_price,
        size_multiplier=size_mul,
        commission_per_unit=request.commission_per_unit,
        commission_min=request.commission_min_per_order,
        platform_fee_per_unit=request.platform_fee_per_unit,
        platform_fee_min=request.platform_fee_min_per_order,
        rate_pct_of_notional=request.rate,
    )["total_fee"]
    cash_left = capital - shares * entry_price * size_mul - entry_fee
    final_equity = cash_left + shares * exit_price * size_mul - exit_fee
    fees = entry_fee + exit_fee

    # Max drawdown for buy-and-hold = trough vs peak across bar closes
    equity_curve = [cash_left + shares * float(b.close_price) * size_mul for b in bars]
    peak = capital
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = min(max_dd, (eq - peak) / peak)

    return {
        "total_return": (final_equity / capital - 1.0) if capital > 0 else 0.0,
        "final_equity": final_equity,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "shares": shares,
        "fees": fees,
        "max_drawdown": max_dd,
    }


def _calculate_vnpy_daily_results(
    *,
    request: CtaBacktestRequest,
    vt_symbol: str,
    daily_closes: dict[date, float],
    trade_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Calculate vn.py-style daily mark-to-market results.

    This mirrors vn.py's backtesting accounting at the part Omega needs:
    every trading date has close price, start/end position, holding PnL,
    trading PnL, turnover, commission, net PnL, balance and drawdown.
    """

    trades_by_date: dict[date, list[VnpyStyleTrade]] = defaultdict(list)
    for row in trade_rows:
        raw_date = str(row.get("trade_date") or "")
        if not raw_date:
            continue
        trade_date = date.fromisoformat(raw_date)
        direction = "long" if row.get("direction") == Direction.LONG.value else "short"
        trades_by_date[trade_date].append(
            VnpyStyleTrade(
                vt_symbol=vt_symbol,
                direction=direction,
                volume=float(row.get("volume") or 0.0),
                price=float(row.get("price") or 0.0),
            )
        )

    balance = float(request.capital)
    highlevel = balance
    start_poses: dict[str, float] = {vt_symbol: 0.0}
    pre_closes: dict[str, float] = {}
    daily_rows: list[dict[str, Any]] = []

    for result_date in sorted(daily_closes):
        previous_balance = balance
        close_price = float(daily_closes[result_date])
        daily_result = PortfolioDailyResult(result_date, {vt_symbol: close_price})
        for trade in trades_by_date.get(result_date, []):
            daily_result.add_trade(trade)
        daily_result.calculate_pnl(
            pre_closes=pre_closes,
            start_poses=start_poses,
            sizes={vt_symbol: float(request.size)},
            long_rates={vt_symbol: float(request.rate)},
            short_rates={vt_symbol: float(request.rate)},
        )

        balance += daily_result.net_pnl
        highlevel = max(highlevel, balance)
        drawdown = balance - highlevel
        ddpercent = (drawdown / highlevel * 100) if highlevel else 0.0
        daily_return = (daily_result.net_pnl / previous_balance * 100) if previous_balance else 0.0
        daily_rows.append(
            {
                "date": result_date.isoformat(),
                "close_price": close_price,
                "trade_count": daily_result.trade_count,
                "turnover": daily_result.turnover,
                "commission": daily_result.commission,
                "trading_pnl": daily_result.trading_pnl,
                "holding_pnl": daily_result.holding_pnl,
                "total_pnl": daily_result.total_pnl,
                "net_pnl": daily_result.net_pnl,
                "start_pos": daily_result.start_poses.get(vt_symbol, 0.0),
                "end_pos": daily_result.end_poses.get(vt_symbol, 0.0),
                "balance": balance,
                "return_percent": daily_return,
                "highlevel": highlevel,
                "drawdown": drawdown,
                "ddpercent": ddpercent,
            }
        )
        start_poses = dict(daily_result.end_poses)
        pre_closes = dict(daily_result.close_prices)

    stats = _calculate_vnpy_statistics(request.capital, daily_rows)
    return daily_rows, stats


def _calculate_vnpy_statistics(capital: float, daily_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not daily_rows:
        return {
            "capital": capital,
            "end_balance": capital,
            "total_days": 0,
            "profit_days": 0,
            "loss_days": 0,
            "total_net_pnl": 0.0,
            "total_commission": 0.0,
            "total_turnover": 0.0,
            "total_trade_count": 0,
            "total_return_percent": 0.0,
            "max_drawdown": 0.0,
            "max_ddpercent": 0.0,
            "sharpe_ratio": 0.0,
            "return_drawdown_ratio": 0.0,
        }

    returns = [float(row["return_percent"]) for row in daily_rows]
    total_days = len(daily_rows)
    end_balance = float(daily_rows[-1]["balance"])
    total_return = (end_balance / capital - 1) * 100 if capital else 0.0
    daily_return = sum(returns) / total_days if total_days else 0.0
    if len(returns) >= 2:
        mean = daily_return
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        return_std = math.sqrt(variance)
    else:
        return_std = 0.0
    sharpe = (daily_return / return_std * math.sqrt(240)) if return_std else 0.0
    max_drawdown = min(float(row["drawdown"]) for row in daily_rows)
    max_ddpercent = min(float(row["ddpercent"]) for row in daily_rows)
    max_drawdown_duration = _calculate_drawdown_duration(daily_rows)
    return_drawdown_ratio = (total_return / abs(max_ddpercent)) if max_ddpercent else 0.0

    return {
        "start_date": daily_rows[0]["date"],
        "end_date": daily_rows[-1]["date"],
        "capital": capital,
        "end_balance": end_balance,
        "total_days": total_days,
        "profit_days": sum(1 for row in daily_rows if float(row["net_pnl"]) > 0),
        "loss_days": sum(1 for row in daily_rows if float(row["net_pnl"]) < 0),
        "total_net_pnl": sum(float(row["net_pnl"]) for row in daily_rows),
        "daily_net_pnl": sum(float(row["net_pnl"]) for row in daily_rows) / total_days,
        "total_commission": sum(float(row["commission"]) for row in daily_rows),
        "daily_commission": sum(float(row["commission"]) for row in daily_rows) / total_days,
        "total_turnover": sum(float(row["turnover"]) for row in daily_rows),
        "daily_turnover": sum(float(row["turnover"]) for row in daily_rows) / total_days,
        "total_trade_count": sum(int(row["trade_count"]) for row in daily_rows),
        "daily_trade_count": sum(int(row["trade_count"]) for row in daily_rows) / total_days,
        "total_return_percent": total_return,
        "annual_return_percent": total_return / total_days * 240 if total_days else 0.0,
        "daily_return_percent": daily_return,
        "return_std_percent": return_std,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_drawdown,
        "max_ddpercent": max_ddpercent,
        "max_drawdown_duration": max_drawdown_duration,
        "return_drawdown_ratio": return_drawdown_ratio,
    }


def _calculate_drawdown_duration(daily_rows: list[dict[str, Any]]) -> int:
    duration = 0
    max_duration = 0
    for row in daily_rows:
        if float(row["drawdown"]) < 0:
            duration += 1
            max_duration = max(max_duration, duration)
        else:
            duration = 0
    return max_duration


def _required_bars(setting: dict[str, Any]) -> int:
    windows: list[int] = []
    for key, value in setting.items():
        if key.endswith("_window"):
            try:
                windows.append(int(value))
            except (TypeError, ValueError):
                continue
    return max(windows or [1]) + 1

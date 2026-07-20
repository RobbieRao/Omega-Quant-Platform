"""Headless OptionMaster engine for US stock options.

This absorbs vn.py OptionMaster's useful backend concepts for Omega:
option portfolios, option chains, T-shaped quote rows, implied-volatility
curves, and position Greeks aggregation. Desktop Qt widgets and China-specific
option conventions are deliberately excluded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable

from .option_pricing import (
    OptionContractSnapshot,
    calculate_fast_vollib_greeks,
    parse_option_contract,
)

from .engine import BaseEngine, MainEngine
from .event import EVENT_POSITION, EVENT_TICK, Event, EventEngine
from .objects import Direction, Exchange, LogData, PositionData, TickData


EVENT_OPTION = "eOption."


@dataclass(frozen=True)
class UsOptionContract:
    symbol: str
    underlying: str
    expiry: str
    strike: float
    put_call: str
    exchange: Exchange = Exchange.SMART
    multiplier: float = 100.0
    currency: str = "USD"

    @property
    def canonical_symbol(self) -> str:
        expiry_short = self.expiry[2:] if len(self.expiry) == 8 else self.expiry
        strike = int(round(self.strike * 1000))
        return f"O:{self.underlying.upper()}{expiry_short}{self.put_call.upper()}{strike:08d}"

    @property
    def chain_key(self) -> str:
        return f"{self.underlying.upper()}-{self.expiry}"


@dataclass
class OptionQuoteState:
    contract: UsOptionContract
    last_price: float | None = None
    bid_price: float | None = None
    ask_price: float | None = None
    volume: float = 0.0
    mark_price: float | None = None
    mark_source: str | None = None
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    greeks_status: str | None = None
    greeks_engine: str | None = None
    updated_at: datetime | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "symbol": self.contract.canonical_symbol,
            "underlying": self.contract.underlying,
            "expiry": self.contract.expiry,
            "strike": self.contract.strike,
            "put_call": self.contract.put_call,
            "last_price": self.last_price,
            "bid_price": self.bid_price,
            "ask_price": self.ask_price,
            "mark_price": self.mark_price,
            "mark_source": self.mark_source,
            "iv": self.iv,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "greeks_status": self.greeks_status,
            "greeks_engine": self.greeks_engine,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class OptionPositionState:
    contract: UsOptionContract
    net_volume: float = 0.0
    market_value: float = 0.0
    average_price: float | None = None
    updated_at: datetime | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "symbol": self.contract.canonical_symbol,
            "underlying": self.contract.underlying,
            "expiry": self.contract.expiry,
            "strike": self.contract.strike,
            "put_call": self.contract.put_call,
            "net_volume": self.net_volume,
            "market_value": self.market_value,
            "average_price": self.average_price,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class OptionPortfolioState:
    name: str
    risk_free_rate: float = 0.04
    dividend_yield: float = 0.0
    underlying_prices: dict[str, float] = field(default_factory=dict)
    contracts: dict[str, UsOptionContract] = field(default_factory=dict)
    quotes: dict[str, OptionQuoteState] = field(default_factory=dict)
    positions: dict[str, OptionPositionState] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class OptionMasterEngine(BaseEngine):
    """Option pricing/risk engine for US equities/options."""

    engine_name = "option_master"

    def __init__(
        self,
        main_engine: MainEngine,
        event_engine: EventEngine,
        valuation_date_factory: Callable[[], date] = date.today,
    ) -> None:
        super().__init__(main_engine, event_engine, self.engine_name)
        self.valuation_date_factory = valuation_date_factory
        self.portfolios: dict[str, OptionPortfolioState] = {}
        self.symbol_to_portfolio: dict[str, str] = {}
        self.event_engine.register(EVENT_TICK, self.process_tick_event)
        self.event_engine.register(EVENT_POSITION, self.process_position_event)

    def close(self) -> None:
        self.event_engine.unregister(EVENT_TICK, self.process_tick_event)
        self.event_engine.unregister(EVENT_POSITION, self.process_position_event)

    def configure_portfolio(
        self,
        name: str,
        *,
        risk_free_rate: float = 0.04,
        dividend_yield: float = 0.0,
    ) -> OptionPortfolioState:
        key = name.strip().upper()
        if not key:
            raise ValueError("portfolio name is required")
        state = self.portfolios.get(key)
        if state is None:
            state = OptionPortfolioState(name=key)
            self.portfolios[key] = state
        state.risk_free_rate = risk_free_rate
        state.dividend_yield = dividend_yield
        state.updated_at = datetime.now(timezone.utc)
        self.write_log(f"Option portfolio configured: {key}")
        return state

    def add_option_contract(self, portfolio_name: str, symbol: str) -> UsOptionContract:
        portfolio = self._portfolio(portfolio_name)
        contract = parse_us_option_contract(symbol)
        portfolio.contracts[contract.canonical_symbol] = contract
        portfolio.quotes.setdefault(contract.canonical_symbol, OptionQuoteState(contract=contract))
        self.symbol_to_portfolio[contract.canonical_symbol] = portfolio.name
        self.symbol_to_portfolio[contract.canonical_symbol.removeprefix("O:")] = portfolio.name
        self.symbol_to_portfolio[contract.underlying.upper()] = portfolio.name
        self._emit_option(portfolio)
        return contract

    def update_underlying_price(self, portfolio_name: str, underlying: str, price: float) -> None:
        if price <= 0:
            raise ValueError("underlying price must be positive")
        portfolio = self._portfolio(portfolio_name)
        portfolio.underlying_prices[underlying.upper()] = price
        portfolio.updated_at = datetime.now(timezone.utc)
        for quote in portfolio.quotes.values():
            if quote.contract.underlying.upper() == underlying.upper() and quote.mark_price:
                self._recalculate_greeks(portfolio, quote)
        self._emit_option(portfolio)

    def update_option_quote(
        self,
        portfolio_name: str,
        symbol: str,
        *,
        last_price: float | None = None,
        bid_price: float | None = None,
        ask_price: float | None = None,
        volume: float | None = None,
    ) -> OptionQuoteState:
        portfolio = self._portfolio(portfolio_name)
        contract = portfolio.contracts.get(parse_us_option_contract(symbol).canonical_symbol)
        if contract is None:
            contract = self.add_option_contract(portfolio.name, symbol)
        quote = portfolio.quotes.setdefault(contract.canonical_symbol, OptionQuoteState(contract=contract))
        quote.last_price = _positive_or_none(last_price) or quote.last_price
        quote.bid_price = _positive_or_none(bid_price) or quote.bid_price
        quote.ask_price = _positive_or_none(ask_price) or quote.ask_price
        quote.volume = volume if volume is not None else quote.volume
        quote.updated_at = datetime.now(timezone.utc)
        quote.mark_price, quote.mark_source = _select_mark(quote)
        self._recalculate_greeks(portfolio, quote)
        portfolio.updated_at = quote.updated_at
        self._emit_option(portfolio)
        return quote

    def update_option_position(
        self,
        portfolio_name: str,
        symbol: str,
        *,
        net_volume: float,
        market_value: float = 0.0,
        average_price: float | None = None,
    ) -> OptionPositionState:
        portfolio = self._portfolio(portfolio_name)
        contract = portfolio.contracts.get(parse_us_option_contract(symbol).canonical_symbol)
        if contract is None:
            contract = self.add_option_contract(portfolio.name, symbol)
        position = OptionPositionState(
            contract=contract,
            net_volume=net_volume,
            market_value=market_value,
            average_price=average_price,
            updated_at=datetime.now(timezone.utc),
        )
        portfolio.positions[contract.canonical_symbol] = position
        portfolio.updated_at = position.updated_at
        self._emit_option(portfolio)
        return position

    def get_chain(self, portfolio_name: str, underlying: str, expiry: str) -> dict[str, Any]:
        portfolio = self._portfolio(portfolio_name)
        rows: dict[float, dict[str, Any]] = {}
        for quote in portfolio.quotes.values():
            contract = quote.contract
            if contract.underlying.upper() != underlying.upper() or contract.expiry != expiry:
                continue
            row = rows.setdefault(contract.strike, {"strike": contract.strike, "call": None, "put": None})
            side = "call" if contract.put_call == "C" else "put"
            row[side] = quote.snapshot()
        return {
            "portfolio": portfolio.name,
            "underlying": underlying.upper(),
            "expiry": expiry,
            "underlying_price": portfolio.underlying_prices.get(underlying.upper()),
            "rows": [rows[strike] for strike in sorted(rows)],
        }

    def get_volatility_curve(self, portfolio_name: str, underlying: str, expiry: str) -> dict[str, Any]:
        chain = self.get_chain(portfolio_name, underlying, expiry)
        points = []
        for row in chain["rows"]:
            call_iv = (row.get("call") or {}).get("iv")
            put_iv = (row.get("put") or {}).get("iv")
            iv_values = [value for value in (call_iv, put_iv) if isinstance(value, (int, float))]
            points.append(
                {
                    "strike": row["strike"],
                    "call_iv": call_iv,
                    "put_iv": put_iv,
                    "mid_iv": sum(iv_values) / len(iv_values) if iv_values else None,
                }
            )
        return {
            "portfolio": chain["portfolio"],
            "underlying": chain["underlying"],
            "expiry": chain["expiry"],
            "points": points,
        }

    def get_volatility_surface(self, portfolio_name: str, underlying: str) -> dict[str, Any]:
        """Aggregate all available (expiry, strike) → mid_iv into a 2D grid."""

        portfolio = self._portfolio(portfolio_name)
        expiries: set[str] = set()
        for symbol, quote in portfolio.quotes.items():
            if quote.underlying.upper() != underlying.upper():
                continue
            if quote.expiry:
                expiries.add(quote.expiry)
        rows: list[dict[str, Any]] = []
        for exp in sorted(expiries):
            curve = self.get_volatility_curve(portfolio_name, underlying, exp)
            for pt in curve.get("points", []):
                if pt.get("mid_iv") is not None:
                    rows.append({"expiry": exp, "strike": pt["strike"], "iv": pt["mid_iv"]})
        return {
            "portfolio": portfolio.name,
            "underlying": underlying.upper(),
            "expiries": sorted(expiries),
            "points": rows,
        }

    def get_greeks_exposure(self, portfolio_name: str) -> dict[str, Any]:
        portfolio = self._portfolio(portfolio_name)
        totals = {
            "portfolio": portfolio.name,
            "contracts": 0,
            "net_contracts": 0.0,
            "delta_shares": 0.0,
            "gamma_shares": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "weighted_iv": None,
            "by_underlying": {},
        }
        iv_numerator = 0.0
        iv_denominator = 0.0
        for symbol, position in portfolio.positions.items():
            quote = portfolio.quotes.get(symbol)
            if not quote:
                continue
            quantity = position.net_volume
            if quantity == 0:
                continue
            exposure = _contract_exposure(position, quote)
            if not exposure["has_greeks"]:
                continue
            totals["contracts"] += 1
            totals["net_contracts"] = round(float(totals["net_contracts"]) + quantity, 6)
            for key in ("delta_shares", "gamma_shares", "theta", "vega"):
                totals[key] = round(float(totals[key]) + float(exposure[key]), 6)
            weight = abs(position.market_value) or abs(quantity * (quote.mark_price or 0.0) * position.contract.multiplier)
            if quote.iv is not None and weight > 0:
                iv_numerator += quote.iv * weight
                iv_denominator += weight
            bucket = totals["by_underlying"].setdefault(
                position.contract.underlying,
                {"delta_shares": 0.0, "gamma_shares": 0.0, "theta": 0.0, "vega": 0.0, "net_contracts": 0.0},
            )
            bucket["net_contracts"] = round(float(bucket["net_contracts"]) + quantity, 6)
            for key in ("delta_shares", "gamma_shares", "theta", "vega"):
                bucket[key] = round(float(bucket[key]) + float(exposure[key]), 6)
        if iv_denominator > 0:
            totals["weighted_iv"] = round(iv_numerator / iv_denominator, 6)
        return totals

    def get_scenario_matrix(
        self,
        portfolio_name: str,
        underlying: str,
        *,
        price_moves: tuple[float, ...] = (-0.1, -0.05, 0.0, 0.05, 0.1),
        iv_shifts: tuple[float, ...] = (-0.05, 0.0, 0.05),
        days_forward: float = 0.0,
    ) -> dict[str, Any]:
        """Approximate option portfolio PnL under spot/IV/time scenarios.

        This follows OptionMaster's practical workflow: use current position
        Greeks as a fast risk surface, not a full repricing engine. ``iv_shifts``
        are absolute volatility changes, so ``0.05`` means +5 volatility points.
        """

        portfolio = self._portfolio(portfolio_name)
        normalized_underlying = underlying.upper()
        base_underlying_price = portfolio.underlying_prices.get(normalized_underlying)
        rows: list[dict[str, Any]] = []
        base_market_value = 0.0
        contracts_used = 0
        for position in portfolio.positions.values():
            if position.contract.underlying.upper() != normalized_underlying or position.net_volume == 0:
                continue
            quote = portfolio.quotes.get(position.contract.canonical_symbol)
            if not quote:
                continue
            mark = quote.mark_price or position.average_price
            if mark:
                base_market_value += position.net_volume * mark * position.contract.multiplier
            if not all(value is not None for value in (quote.delta, quote.gamma, quote.theta, quote.vega)):
                continue
            contracts_used += 1
            for price_move in price_moves:
                if not base_underlying_price:
                    d_s = 0.0
                    scenario_underlying_price = None
                else:
                    d_s = base_underlying_price * price_move
                    scenario_underlying_price = base_underlying_price + d_s
                for iv_shift in iv_shifts:
                    multiplier = position.contract.multiplier
                    quantity = position.net_volume
                    delta_pnl = float(quote.delta or 0.0) * quantity * multiplier * d_s
                    gamma_pnl = 0.5 * float(quote.gamma or 0.0) * quantity * multiplier * d_s * d_s
                    vega_pnl = float(quote.vega or 0.0) * quantity * multiplier * (iv_shift * 100.0)
                    theta_pnl = float(quote.theta or 0.0) * quantity * multiplier * days_forward
                    rows.append(
                        {
                            "symbol": position.contract.canonical_symbol,
                            "price_move": price_move,
                            "iv_shift": iv_shift,
                            "days_forward": days_forward,
                            "underlying_price": scenario_underlying_price,
                            "delta_pnl": round(delta_pnl, 6),
                            "gamma_pnl": round(gamma_pnl, 6),
                            "vega_pnl": round(vega_pnl, 6),
                            "theta_pnl": round(theta_pnl, 6),
                            "total_pnl": round(delta_pnl + gamma_pnl + vega_pnl + theta_pnl, 6),
                        }
                    )

        grouped: dict[tuple[float, float], dict[str, Any]] = {}
        for row in rows:
            key = (float(row["price_move"]), float(row["iv_shift"]))
            bucket = grouped.setdefault(
                key,
                {
                    "price_move": row["price_move"],
                    "iv_shift": row["iv_shift"],
                    "days_forward": days_forward,
                    "underlying_price": row["underlying_price"],
                    "delta_pnl": 0.0,
                    "gamma_pnl": 0.0,
                    "vega_pnl": 0.0,
                    "theta_pnl": 0.0,
                    "total_pnl": 0.0,
                },
            )
            for field_name in ("delta_pnl", "gamma_pnl", "vega_pnl", "theta_pnl", "total_pnl"):
                bucket[field_name] = round(float(bucket[field_name]) + float(row[field_name]), 6)

        return {
            "portfolio": portfolio.name,
            "underlying": normalized_underlying,
            "base_underlying_price": base_underlying_price,
            "base_market_value": round(base_market_value, 6),
            "contracts_used": contracts_used,
            "price_moves": list(price_moves),
            "iv_shifts": list(iv_shifts),
            "days_forward": days_forward,
            "rows": sorted(grouped.values(), key=lambda item: (item["price_move"], item["iv_shift"])),
        }

    def process_tick_event(self, event: Event) -> None:
        tick = event.data
        if not isinstance(tick, TickData):
            return
        option_contract = maybe_parse_us_option_contract(tick.symbol)
        if option_contract:
            portfolio_name = self.symbol_to_portfolio.get(option_contract.canonical_symbol)
            if portfolio_name:
                self.update_option_quote(
                    portfolio_name,
                    option_contract.canonical_symbol,
                    last_price=tick.last_price,
                    bid_price=tick.bid_price_1,
                    ask_price=tick.ask_price_1,
                    volume=tick.volume,
                )
            return
        portfolio_name = self.symbol_to_portfolio.get(tick.symbol.upper())
        if portfolio_name:
            self.update_underlying_price(portfolio_name, tick.symbol, tick.last_price)

    def process_position_event(self, event: Event) -> None:
        position = event.data
        if not isinstance(position, PositionData):
            return
        contract = maybe_parse_us_option_contract(position.symbol)
        if not contract:
            return
        portfolio_name = self.symbol_to_portfolio.get(contract.canonical_symbol) or self.symbol_to_portfolio.get(contract.underlying)
        if not portfolio_name:
            return
        signed_volume = position.volume if position.direction == Direction.LONG else -position.volume
        self.update_option_position(
            portfolio_name,
            contract.canonical_symbol,
            net_volume=signed_volume,
            market_value=position.volume * position.price * contract.multiplier,
            average_price=position.price,
        )

    def _recalculate_greeks(self, portfolio: OptionPortfolioState, quote: OptionQuoteState) -> None:
        if not quote.mark_price:
            return
        underlying_price = portfolio.underlying_prices.get(quote.contract.underlying.upper())
        if not underlying_price:
            return
        snapshot = OptionContractSnapshot(
            symbol=quote.contract.underlying,
            expiry=quote.contract.expiry,
            strike=quote.contract.strike,
            put_call=quote.contract.put_call,
            option_price=quote.mark_price,
            underlying_price=underlying_price,
            risk_free_rate=portfolio.risk_free_rate,
            dividend_yield=portfolio.dividend_yield,
            valuation_date=self.valuation_date_factory(),
        )
        greeks = calculate_fast_vollib_greeks(snapshot, mark={"mark_price": quote.mark_price, "mark_source": quote.mark_source})
        quote.greeks_status = greeks.get("status")
        quote.greeks_engine = greeks.get("engine")
        if greeks.get("status") == "ok":
            quote.iv = _float_or_none(greeks.get("iv"))
            quote.delta = _float_or_none(greeks.get("delta"))
            quote.gamma = _float_or_none(greeks.get("gamma"))
            quote.theta = _float_or_none(greeks.get("theta"))
            quote.vega = _float_or_none(greeks.get("vega"))

    def _portfolio(self, name: str) -> OptionPortfolioState:
        key = name.strip().upper()
        state = self.portfolios.get(key)
        if state is None:
            state = self.configure_portfolio(key)
        return state

    def _emit_option(self, portfolio: OptionPortfolioState) -> None:
        payload = {
            "portfolio": portfolio.name,
            "updated_at": portfolio.updated_at.isoformat(),
            "underlyings": dict(portfolio.underlying_prices),
            "contracts": len(portfolio.contracts),
            "positions": len(portfolio.positions),
        }
        self.event_engine.put(Event(EVENT_OPTION, payload))
        self.event_engine.put(Event(EVENT_OPTION + portfolio.name, payload))

    def write_log(self, msg: str) -> None:
        self.event_engine.put(Event("eLog", LogData(msg=msg, gateway_name=self.engine_name)))


def parse_us_option_contract(symbol: str) -> UsOptionContract:
    parsed = parse_option_contract(symbol)
    if not parsed:
        raise ValueError(f"unsupported US OCC option symbol: {symbol}")
    return UsOptionContract(
        symbol=str(symbol).strip().upper(),
        underlying=str(parsed["underlying"]).upper(),
        expiry=str(parsed["expiry"]),
        strike=float(parsed["strike"]),
        put_call=str(parsed["put_call"]).upper(),
    )


def maybe_parse_us_option_contract(symbol: str) -> UsOptionContract | None:
    try:
        return parse_us_option_contract(symbol)
    except ValueError:
        return None


def _select_mark(quote: OptionQuoteState) -> tuple[float | None, str | None]:
    bid = quote.bid_price
    ask = quote.ask_price
    if bid is not None and ask is not None and bid > 0 and ask >= bid:
        mid = (bid + ask) / 2
        if quote.last_price is not None and bid <= quote.last_price <= ask:
            return quote.last_price, "last_trade"
        return mid, "midpoint"
    if quote.last_price is not None and quote.last_price > 0:
        return quote.last_price, "last_trade"
    return None, None


def _contract_exposure(position: OptionPositionState, quote: OptionQuoteState) -> dict[str, Any]:
    multiplier = position.contract.multiplier
    result = {"has_greeks": False, "delta_shares": 0.0, "gamma_shares": 0.0, "theta": 0.0, "vega": 0.0}
    for source, target in (("delta", "delta_shares"), ("gamma", "gamma_shares"), ("theta", "theta"), ("vega", "vega")):
        value = getattr(quote, source)
        if value is None:
            continue
        result[target] = float(value) * position.net_volume * multiplier
        result["has_greeks"] = True
    return result


def _positive_or_none(value: float | None) -> float | None:
    value = _float_or_none(value)
    return value if value is not None and value > 0 else None


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None

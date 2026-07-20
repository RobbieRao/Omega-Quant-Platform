from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
import re
import threading
import time
from typing import Any

from ..utils import finite_float, now_ms


@dataclass(frozen=True)
class OptionContractSnapshot:
    symbol: str
    expiry: str
    strike: float
    put_call: str
    option_price: float
    underlying_price: float
    risk_free_rate: float = 0.04
    dividend_yield: float = 0.0
    valuation_date: date | None = None

    @property
    def years_to_expiry(self) -> float:
        valuation_date = self.valuation_date or date.today()
        expiry_date = datetime.strptime(self.expiry, "%Y%m%d").date()
        days = max((expiry_date - valuation_date).days, 0)
        return days / 365.0


def fast_vollib_available() -> bool:
    try:
        import fast_vollib  # noqa: F401
    except Exception:
        return False
    return True


_OPTION_SYMBOL_RE = re.compile(r"^(?:O:)?([A-Z.]+)\s*(\d{6}|\d{8})([CP])(\d{8})")
_LIVE_GREEKS_CACHE: dict[str, dict[str, Any]] = {}
_LIVE_GREEKS_LOCK = threading.Lock()


def parse_option_contract(value: str | None) -> dict[str, Any] | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    match = _OPTION_SYMBOL_RE.match(text)
    if not match:
        return None
    underlying, expiry, put_call, raw_strike = match.groups()
    if len(expiry) == 6:
        expiry = f"20{expiry}"
    return {
        "underlying": underlying,
        "expiry": expiry,
        "put_call": put_call,
        "flag": put_call.lower(),
        "strike": int(raw_strike) / 1000.0,
    }


def option_contract_snapshot_from_position(
    position: dict[str, Any],
    *,
    option_price: float,
    underlying_price: float,
    risk_free_rate: float = 0.04,
    valuation_date: date | None = None,
) -> OptionContractSnapshot | None:
    contract = position.get("contract") or {}
    parsed = parse_option_contract(contract.get("display") or contract.get("identifier"))
    if parsed is None:
        parsed = parse_option_contract(position.get("symbol"))
    expiry = str(contract.get("expiry") or (parsed or {}).get("expiry") or "")
    put_call = str(contract.get("put_call") or (parsed or {}).get("put_call") or "").upper()
    if put_call in {"CALL", "C"}:
        put_call = "C"
    elif put_call in {"PUT", "P"}:
        put_call = "P"
    strike = finite_float(contract.get("strike"))
    if strike is None:
        strike = finite_float((parsed or {}).get("strike"))
    symbol = str(contract.get("symbol") or (parsed or {}).get("underlying") or "")
    if not symbol or not expiry or put_call not in {"C", "P"} or strike is None or strike <= 0:
        return None
    return OptionContractSnapshot(
        symbol=symbol,
        expiry=expiry,
        strike=float(strike),
        put_call=put_call,
        option_price=option_price,
        underlying_price=underlying_price,
        risk_free_rate=risk_free_rate,
        valuation_date=valuation_date,
    )


def _age_seconds(item: dict[str, Any] | None, current_ms: int) -> float | None:
    if not item:
        return None
    received_at_ms = finite_float(item.get("received_at_ms"))
    if received_at_ms is None:
        return None
    return max(0.0, (current_ms - received_at_ms) / 1000.0)


def select_option_mark_price(
    *,
    bbo: dict[str, Any] | None,
    last_trade: dict[str, Any] | None,
    current_ms: int | None = None,
    last_trade_max_age_seconds: float = 15.0,
    last_mid_max_deviation_pct: float = 0.10,
    last_mid_max_deviation_abs: float = 0.05,
) -> dict[str, Any] | None:
    current_ms = current_ms or now_ms()
    bid = finite_float((bbo or {}).get("bid_price"))
    ask = finite_float((bbo or {}).get("ask_price"))
    quoted_mid = finite_float((bbo or {}).get("mid_price"))
    if quoted_mid is None and bid is not None and ask is not None and bid > 0 and ask >= bid:
        quoted_mid = (bid + ask) / 2
    spread = (ask - bid) if bid is not None and ask is not None and ask >= bid else None
    spread_pct = spread / quoted_mid if spread is not None and quoted_mid else None

    last_price = finite_float((last_trade or {}).get("latest_price") or (last_trade or {}).get("price"))
    last_age = _age_seconds(last_trade, current_ms)
    last_is_fresh = last_age is not None and last_age <= last_trade_max_age_seconds
    last_inside_book = (
        bid is not None
        and ask is not None
        and last_price is not None
        and bid <= last_price <= ask
    )
    last_near_mid = False
    if last_price is not None and quoted_mid is not None:
        tolerance = max(last_mid_max_deviation_abs, abs(quoted_mid) * last_mid_max_deviation_pct)
        last_near_mid = abs(last_price - quoted_mid) <= tolerance

    if last_price is not None and last_price > 0 and (quoted_mid is None or (last_is_fresh and last_inside_book and last_near_mid)):
        return {
            "mark_price": last_price,
            "mark_source": "last_trade",
            "last_trade_price": last_price,
            "last_trade_age_seconds": last_age,
            "mid_price": quoted_mid,
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "spread_pct": spread_pct,
            "mark_reason": "fresh last trade inside current bid/ask",
        }
    if quoted_mid is not None and quoted_mid > 0:
        return {
            "mark_price": quoted_mid,
            "mark_source": "midpoint",
            "last_trade_price": last_price,
            "last_trade_age_seconds": last_age,
            "mid_price": quoted_mid,
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "spread_pct": spread_pct,
            "mark_reason": "current bid/ask midpoint",
        }
    return None


def _scalar(value: Any) -> float | None:
    try:
        if hasattr(value, "iloc"):
            value = value.iloc[0]
        elif hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
            value = value[0]
        result = float(value)
    except Exception:
        return None
    return result if math.isfinite(result) else None


def calculate_fast_vollib_greeks(contract: OptionContractSnapshot, *, mark: dict[str, Any] | None = None) -> dict[str, Any]:
    if contract.option_price <= 0 or contract.underlying_price <= 0 or contract.strike <= 0:
        return {"status": "invalid_input", **(mark or {})}
    t = contract.years_to_expiry
    if t <= 0:
        return {"status": "expired", **(mark or {})}
    flag = contract.put_call.lower()
    try:
        engine = "fast-vollib"
        try:
            from fast_vollib.greeks import delta, gamma, theta, vega
            from fast_vollib.implied_volatility import fast_implied_volatility

            iv = _scalar(
                fast_implied_volatility(
                    contract.option_price,
                    contract.underlying_price,
                    contract.strike,
                    t,
                    contract.risk_free_rate,
                    flag,
                    q=contract.dividend_yield,
                    model="black_scholes_merton" if contract.dividend_yield else "black_scholes",
                    return_as="numpy",
                )
            )

            def calc_delta(sigma: float) -> float | None:
                model = "black_scholes_merton" if contract.dividend_yield else "black_scholes"
                q_value = contract.dividend_yield if contract.dividend_yield else None
                return _scalar(delta(flag, contract.underlying_price, contract.strike, t, contract.risk_free_rate, sigma, q=q_value, model=model, return_as="numpy"))

            def calc_gamma(sigma: float) -> float | None:
                model = "black_scholes_merton" if contract.dividend_yield else "black_scholes"
                q_value = contract.dividend_yield if contract.dividend_yield else None
                return _scalar(gamma(flag, contract.underlying_price, contract.strike, t, contract.risk_free_rate, sigma, q=q_value, model=model, return_as="numpy"))

            def calc_theta(sigma: float) -> float | None:
                model = "black_scholes_merton" if contract.dividend_yield else "black_scholes"
                q_value = contract.dividend_yield if contract.dividend_yield else None
                return _scalar(theta(flag, contract.underlying_price, contract.strike, t, contract.risk_free_rate, sigma, q=q_value, model=model, return_as="numpy"))

            def calc_vega(sigma: float) -> float | None:
                model = "black_scholes_merton" if contract.dividend_yield else "black_scholes"
                q_value = contract.dividend_yield if contract.dividend_yield else None
                return _scalar(vega(flag, contract.underlying_price, contract.strike, t, contract.risk_free_rate, sigma, q=q_value, model=model, return_as="numpy"))
        except Exception:
            # Some fast-vollib wheels expose broken module filenames on macOS.
            # Keep the runtime path usable with a compact Black-Scholes-Merton
            # fallback that matches the same IV/Greek conventions.
            iv = _implied_vol_bsm(
                flag=flag,
                price=contract.option_price,
                spot=contract.underlying_price,
                strike=contract.strike,
                years=t,
                rate=contract.risk_free_rate,
                dividend_yield=contract.dividend_yield,
            )

            def calc_delta(sigma: float) -> float | None:
                return _bsm_delta(flag, contract.underlying_price, contract.strike, t, contract.risk_free_rate, sigma, contract.dividend_yield)

            def calc_gamma(sigma: float) -> float | None:
                return _bsm_gamma(contract.underlying_price, contract.strike, t, contract.risk_free_rate, sigma, contract.dividend_yield)

            def calc_theta(sigma: float) -> float | None:
                return _bsm_theta(flag, contract.underlying_price, contract.strike, t, contract.risk_free_rate, sigma, contract.dividend_yield)

            def calc_vega(sigma: float) -> float | None:
                return _bsm_vega(contract.underlying_price, contract.strike, t, contract.risk_free_rate, sigma, contract.dividend_yield)

        if iv is None or iv <= 0:
            return {"status": "invalid_iv", **(mark or {})}
        result = {
            "status": "ok",
            "iv": iv,
            "delta": calc_delta(iv),
            "gamma": calc_gamma(iv),
            "theta": calc_theta(iv),
            "vega": calc_vega(iv),
            "underlying_price": contract.underlying_price,
            "option_price": contract.option_price,
            "strike": contract.strike,
            "expiry": contract.expiry,
            "put_call": contract.put_call,
            "years_to_expiry": t,
            "engine": engine,
            **(mark or {}),
        }
        return result
    except Exception as exc:
        return {"status": "error", "error": str(exc), **(mark or {})}


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _norm_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2.0 * math.pi)


def _bsm_d1_d2(spot: float, strike: float, years: float, rate: float, sigma: float, dividend_yield: float) -> tuple[float, float]:
    sigma = max(sigma, 1e-9)
    denominator = sigma * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate - dividend_yield + 0.5 * sigma * sigma) * years) / denominator
    return d1, d1 - denominator


def _bsm_price(flag: str, spot: float, strike: float, years: float, rate: float, sigma: float, dividend_yield: float) -> float:
    d1, d2 = _bsm_d1_d2(spot, strike, years, rate, sigma, dividend_yield)
    discounted_spot = spot * math.exp(-dividend_yield * years)
    discounted_strike = strike * math.exp(-rate * years)
    if flag == "c":
        return discounted_spot * _norm_cdf(d1) - discounted_strike * _norm_cdf(d2)
    return discounted_strike * _norm_cdf(-d2) - discounted_spot * _norm_cdf(-d1)


def _implied_vol_bsm(*, flag: str, price: float, spot: float, strike: float, years: float, rate: float, dividend_yield: float) -> float | None:
    low = 1e-6
    high = 5.0
    low_price = _bsm_price(flag, spot, strike, years, rate, low, dividend_yield)
    high_price = _bsm_price(flag, spot, strike, years, rate, high, dividend_yield)
    if price < low_price - 1e-8 or price > high_price + 1e-8:
        return None
    for _ in range(100):
        mid = (low + high) / 2.0
        mid_price = _bsm_price(flag, spot, strike, years, rate, mid, dividend_yield)
        if abs(mid_price - price) < 1e-8:
            return mid
        if mid_price < price:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def _bsm_delta(flag: str, spot: float, strike: float, years: float, rate: float, sigma: float, dividend_yield: float) -> float:
    d1, _ = _bsm_d1_d2(spot, strike, years, rate, sigma, dividend_yield)
    discount = math.exp(-dividend_yield * years)
    if flag == "c":
        return discount * _norm_cdf(d1)
    return -discount * _norm_cdf(-d1)


def _bsm_gamma(spot: float, strike: float, years: float, rate: float, sigma: float, dividend_yield: float) -> float:
    d1, _ = _bsm_d1_d2(spot, strike, years, rate, sigma, dividend_yield)
    return math.exp(-dividend_yield * years) * _norm_pdf(d1) / (spot * sigma * math.sqrt(years))


def _bsm_vega(spot: float, strike: float, years: float, rate: float, sigma: float, dividend_yield: float) -> float:
    d1, _ = _bsm_d1_d2(spot, strike, years, rate, sigma, dividend_yield)
    return spot * math.exp(-dividend_yield * years) * _norm_pdf(d1) * math.sqrt(years) / 100.0


def _bsm_theta(flag: str, spot: float, strike: float, years: float, rate: float, sigma: float, dividend_yield: float) -> float:
    d1, d2 = _bsm_d1_d2(spot, strike, years, rate, sigma, dividend_yield)
    first = -(spot * math.exp(-dividend_yield * years) * _norm_pdf(d1) * sigma) / (2.0 * math.sqrt(years))
    if flag == "c":
        annual = first - rate * strike * math.exp(-rate * years) * _norm_cdf(d2) + dividend_yield * spot * math.exp(-dividend_yield * years) * _norm_cdf(d1)
    else:
        annual = first + rate * strike * math.exp(-rate * years) * _norm_cdf(-d2) - dividend_yield * spot * math.exp(-dividend_yield * years) * _norm_cdf(-d1)
    return annual / 365.0


def cached_fast_vollib_greeks(
    contract_key: str,
    contract: OptionContractSnapshot,
    *,
    mark: dict[str, Any],
    min_interval_seconds: float = 0.5,
) -> dict[str, Any]:
    cache_key = "|".join(
        [
            contract_key,
            f"{contract.option_price:.6f}",
            f"{contract.underlying_price:.6f}",
            f"{contract.years_to_expiry:.8f}",
            contract.put_call,
        ]
    )
    monotonic_now = time.monotonic()
    with _LIVE_GREEKS_LOCK:
        cached = _LIVE_GREEKS_CACHE.get(contract_key)
        if cached and cached.get("cache_key") == cache_key:
            return dict(cached["data"])
        if cached and monotonic_now - float(cached.get("calculated_monotonic", 0.0)) < min_interval_seconds:
            data = dict(cached["data"])
            data["throttled"] = True
            return data
    data = calculate_fast_vollib_greeks(contract, mark=mark)
    data["calculated_at_ms"] = now_ms()
    with _LIVE_GREEKS_LOCK:
        _LIVE_GREEKS_CACHE[contract_key] = {
            "cache_key": cache_key,
            "calculated_monotonic": monotonic_now,
            "data": dict(data),
        }
    return data


def option_time_profile(contract: OptionContractSnapshot) -> dict[str, float | str | bool]:
    profile: dict[str, float | str | bool] = {
        "symbol": contract.symbol,
        "put_call": contract.put_call,
        "strike": contract.strike,
        "option_price": contract.option_price,
        "underlying_price": contract.underlying_price,
        "years_to_expiry": contract.years_to_expiry,
        "fast_vollib_available": fast_vollib_available(),
    }
    return profile

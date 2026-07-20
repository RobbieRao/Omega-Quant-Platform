from __future__ import annotations

from datetime import date

from omega_quant.core.option_pricing import (
    OptionContractSnapshot,
    calculate_fast_vollib_greeks,
    option_contract_snapshot_from_position,
    parse_option_contract,
    select_option_mark_price,
)
from omega_quant.config import Settings
from omega_quant.broker.account_stream import now_ms
from omega_quant.research.live_options import live_options_greeks


def test_parse_option_contract_accepts_massive_and_display_symbols() -> None:
    assert parse_option_contract("O:NVDA260618C00245000") == {
        "underlying": "NVDA",
        "expiry": "20260618",
        "put_call": "C",
        "flag": "c",
        "strike": 245.0,
    }
    assert parse_option_contract("MU 260618P00107000/OPT/USD")["strike"] == 107.0


def test_select_option_mark_prefers_fresh_last_trade_inside_tight_book() -> None:
    mark = select_option_mark_price(
        bbo={"bid_price": 1.95, "ask_price": 2.05, "mid_price": 2.0},
        last_trade={"latest_price": 2.01, "received_at_ms": 10_000},
        current_ms=12_000,
    )

    assert mark["mark_price"] == 2.01
    assert mark["mark_source"] == "last_trade"


def test_select_option_mark_uses_midpoint_when_last_trade_is_far_from_book() -> None:
    mark = select_option_mark_price(
        bbo={"bid_price": 1.95, "ask_price": 2.05, "mid_price": 2.0},
        last_trade={"latest_price": 3.0, "received_at_ms": 10_000},
        current_ms=12_000,
    )

    assert mark["mark_price"] == 2.0
    assert mark["mark_source"] == "midpoint"


def test_calculate_fast_vollib_greeks_from_contract_snapshot() -> None:
    greeks = calculate_fast_vollib_greeks(
        OptionContractSnapshot(
            symbol="NVDA",
            expiry="20260618",
            strike=245.0,
            put_call="C",
            option_price=10.0,
            underlying_price=250.0,
            valuation_date=date(2026, 5, 22),
        ),
        mark={"mark_price": 10.0, "mark_source": "midpoint"},
    )

    assert greeks["status"] == "ok"
    assert greeks["engine"] == "fast-vollib"
    assert greeks["mark_source"] == "midpoint"
    assert greeks["iv"] > 0
    assert -1 <= greeks["delta"] <= 1
    assert greeks["gamma"] >= 0


def test_option_contract_snapshot_from_position_prefers_contract_fields() -> None:
    snapshot = option_contract_snapshot_from_position(
        {
            "contract": {
                "symbol": "NVDA",
                "display": "NVDA 260618C00245000/OPT/USD",
                "expiry": "20260618",
                "strike": "245",
                "put_call": "CALL",
            }
        },
        option_price=5.0,
        underlying_price=250.0,
        valuation_date=date(2026, 5, 22),
    )

    assert snapshot is not None
    assert snapshot.symbol == "NVDA"
    assert snapshot.strike == 245.0
    assert snapshot.put_call == "C"


def test_live_options_greeks_use_stream_mark_price() -> None:
    received_at_ms = now_ms()
    snapshot = {
        "queried_at": "2026-05-22T18:00:00+00:00",
        "positions": {
            "OPT": [
                {
                    "contract": {
                        "symbol": "NVDA",
                        "display": "NVDA 260618C00245000/OPT/USD",
                        "identifier": "NVDA 260618C00245000",
                        "expiry": "20260618",
                        "strike": 245.0,
                        "put_call": "CALL",
                    },
                    "quantity": 1,
                }
            ]
        },
    }
    stream = {
        "quotes": {"NVDA": {"symbol": "NVDA", "latest_price": 250.0, "received_at_ms": received_at_ms}},
        "bbo": {
            "O:NVDA260618C00245000": {
                "symbol": "O:NVDA260618C00245000",
                "bid_price": 9.9,
                "ask_price": 10.1,
                "mid_price": 10.0,
                "received_at_ms": received_at_ms,
            }
        },
        "ticks": {
            "O:NVDA260618C00245000": {
                "symbol": "O:NVDA260618C00245000",
                "latest_price": 10.02,
                "received_at_ms": received_at_ms,
            }
        },
    }

    greeks = live_options_greeks(
        snapshot,
        stream,
        Settings(option_greeks_min_interval_seconds=0.0),
    )

    row = greeks["O:NVDA260618C00245000"]
    assert row["status"] == "ok"
    assert row["mark_source"] == "last_trade"
    assert row["iv"] > 0

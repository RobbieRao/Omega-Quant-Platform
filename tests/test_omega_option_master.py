from __future__ import annotations

from datetime import date, datetime, timezone

from omega_quant.core import (
    EVENT_TICK,
    Direction,
    Event,
    Exchange,
    MainEngine,
    OptionMasterEngine,
    PositionData,
    TickData,
    parse_us_option_contract,
)


def _engine() -> tuple[MainEngine, OptionMasterEngine]:
    main_engine = MainEngine(auto_start=False)
    option_engine = main_engine.add_engine(OptionMasterEngine, valuation_date_factory=lambda: date(2026, 5, 22))
    return main_engine, option_engine


def test_parse_us_option_contract_canonicalizes_massive_and_display_symbols() -> None:
    contract = parse_us_option_contract("O:NVDA260618C00245000")
    assert contract.underlying == "NVDA"
    assert contract.expiry == "20260618"
    assert contract.strike == 245.0
    assert contract.put_call == "C"
    assert contract.canonical_symbol == "O:NVDA260618C00245000"

    display = parse_us_option_contract("MU 260618P00107000/OPT/USD")
    assert display.underlying == "MU"
    assert display.put_call == "P"
    assert display.canonical_symbol == "O:MU260618P00107000"


def test_option_master_builds_t_quote_chain_and_vol_curve() -> None:
    main_engine, option_engine = _engine()
    try:
        option_engine.configure_portfolio("nvda", risk_free_rate=0.04)
        option_engine.add_option_contract("nvda", "O:NVDA260618C00245000")
        option_engine.add_option_contract("nvda", "O:NVDA260618P00245000")
        option_engine.update_underlying_price("nvda", "NVDA", 250.0)
        call = option_engine.update_option_quote(
            "nvda",
            "O:NVDA260618C00245000",
            last_price=10.0,
            bid_price=9.9,
            ask_price=10.1,
        )
        put = option_engine.update_option_quote(
            "nvda",
            "O:NVDA260618P00245000",
            last_price=8.0,
            bid_price=7.9,
            ask_price=8.1,
        )

        chain = option_engine.get_chain("nvda", "NVDA", "20260618")
        assert len(chain["rows"]) == 1
        row = chain["rows"][0]
        assert row["strike"] == 245.0
        assert row["call"]["symbol"] == "O:NVDA260618C00245000"
        assert row["put"]["symbol"] == "O:NVDA260618P00245000"
        assert call.greeks_status == "ok"
        assert put.greeks_status == "ok"

        curve = option_engine.get_volatility_curve("nvda", "NVDA", "20260618")
        assert curve["points"][0]["call_iv"] > 0
        assert curve["points"][0]["put_iv"] > 0
        assert curve["points"][0]["mid_iv"] > 0
    finally:
        main_engine.close()


def test_option_master_aggregates_position_greeks_by_underlying() -> None:
    main_engine, option_engine = _engine()
    try:
        option_engine.configure_portfolio("nvda")
        option_engine.update_underlying_price("nvda", "NVDA", 250.0)
        option_engine.update_option_quote(
            "nvda",
            "O:NVDA260618C00245000",
            last_price=10.0,
            bid_price=9.9,
            ask_price=10.1,
        )
        option_engine.update_option_position(
            "nvda",
            "O:NVDA260618C00245000",
            net_volume=2,
            market_value=2_000,
            average_price=10.0,
        )

        exposure = option_engine.get_greeks_exposure("nvda")
        assert exposure["contracts"] == 1
        assert exposure["net_contracts"] == 2
        assert exposure["delta_shares"] != 0
        assert exposure["vega"] != 0
        assert exposure["weighted_iv"] is not None
        assert "NVDA" in exposure["by_underlying"]
    finally:
        main_engine.close()


def test_option_master_scenario_matrix_estimates_spot_iv_pnl() -> None:
    main_engine, option_engine = _engine()
    try:
        option_engine.configure_portfolio("nvda")
        option_engine.update_underlying_price("nvda", "NVDA", 250.0)
        option_engine.update_option_quote(
            "nvda",
            "O:NVDA260618C00245000",
            last_price=10.0,
            bid_price=9.9,
            ask_price=10.1,
        )
        option_engine.update_option_position(
            "nvda",
            "O:NVDA260618C00245000",
            net_volume=2,
            market_value=2_000,
            average_price=10.0,
        )

        matrix = option_engine.get_scenario_matrix(
            "nvda",
            "NVDA",
            price_moves=(-0.05, 0.0, 0.05),
            iv_shifts=(-0.05, 0.0, 0.05),
        )
    finally:
        main_engine.close()

    assert matrix["portfolio"] == "NVDA"
    assert matrix["underlying"] == "NVDA"
    assert matrix["base_underlying_price"] == 250.0
    assert matrix["contracts_used"] == 1
    assert len(matrix["rows"]) == 9
    flat = next(row for row in matrix["rows"] if row["price_move"] == 0 and row["iv_shift"] == 0)
    up = next(row for row in matrix["rows"] if row["price_move"] == 0.05 and row["iv_shift"] == 0)
    iv_up = next(row for row in matrix["rows"] if row["price_move"] == 0 and row["iv_shift"] == 0.05)
    assert flat["total_pnl"] == 0
    assert up["total_pnl"] > 0
    assert iv_up["vega_pnl"] > 0


def test_option_master_consumes_tick_and_position_events() -> None:
    main_engine, option_engine = _engine()
    try:
        option_engine.configure_portfolio("nvda")
        option_engine.add_option_contract("nvda", "O:NVDA260618C00245000")
        option_engine.process_tick_event(
            Event(
                EVENT_TICK,
                TickData(
                    symbol="NVDA",
                    exchange=Exchange.SMART,
                    datetime=datetime.now(timezone.utc),
                    last_price=250.0,
                ),
            )
        )
        option_engine.process_tick_event(
            Event(
                EVENT_TICK,
                TickData(
                    symbol="O:NVDA260618C00245000",
                    exchange=Exchange.SMART,
                    datetime=datetime.now(timezone.utc),
                    last_price=10.0,
                    bid_price_1=9.9,
                    ask_price_1=10.1,
                ),
            )
        )
        option_engine.process_position_event(
            Event(
                "ePosition.",
                PositionData(
                    symbol="O:NVDA260618C00245000",
                    exchange=Exchange.SMART,
                    direction=Direction.LONG,
                    volume=1,
                    price=10.0,
                ),
            )
        )

        chain = option_engine.get_chain("nvda", "NVDA", "20260618")
        assert chain["underlying_price"] == 250.0
        assert chain["rows"][0]["call"]["greeks_status"] == "ok"
        assert option_engine.get_greeks_exposure("nvda")["contracts"] == 1
    finally:
        main_engine.close()


def test_option_master_core_does_not_import_qt_domestic_or_crypto_modules() -> None:
    import sys

    forbidden = [
        "vnpy.trader.ui",
        "vnpy_ctp",
        "vnpy_xtp",
        "vnpy_sopt",
        "vnpy_binance",
        "vnpy_okx",
    ]
    assert not any(name in sys.modules for name in forbidden)

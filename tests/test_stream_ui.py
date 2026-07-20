from __future__ import annotations

from omega_quant.adapters.platform_context import holding_position_keys, stream_quote_group_model


def test_stream_quote_group_model_pairs_underlying_with_options() -> None:
    groups = stream_quote_group_model(
        quotes={
            "MU": {"symbol": "MU", "latest_price": 120.0, "asset_class": "stocks"},
            "O:MU260618C01070000": {
                "symbol": "O:MU260618C01070000",
                "latest_price": 3.2,
                "asset_class": "options",
            },
            "NVDA": {"symbol": "NVDA", "latest_price": 180.0, "asset_class": "stocks"},
            "O:NVDA260618C00245000": {
                "symbol": "O:NVDA260618C00245000",
                "latest_price": 4.1,
                "asset_class": "options",
            },
            "AAOI": {"symbol": "AAOI", "latest_price": 18.0, "asset_class": "stocks"},
            "O:SOXS260612C00010000": {
                "symbol": "O:SOXS260612C00010000",
                "latest_price": 0.25,
                "asset_class": "options",
            },
        },
        bbo={},
        subscriptions={
            "quotes": ["MU", "NVDA", "AAOI"],
            "options": ["O:MU260618C01070000", "O:NVDA260618C00245000", "O:SOXS260612C00010000"],
        },
    )

    by_underlying = {group["underlying"]: group for group in groups}

    assert by_underlying["MU"]["stockSymbol"] == "MU"
    assert [option["symbol"] for option in by_underlying["MU"]["options"]] == ["O:MU260618C01070000"]
    assert by_underlying["NVDA"]["stockSymbol"] == "NVDA"
    assert [option["symbol"] for option in by_underlying["NVDA"]["options"]] == ["O:NVDA260618C00245000"]
    assert by_underlying["AAOI"]["stockSymbol"] == "AAOI"
    assert by_underlying["AAOI"]["options"] == []
    assert by_underlying["SOXS"]["stockSymbol"] is None
    assert [option["symbol"] for option in by_underlying["SOXS"]["options"]] == ["O:SOXS260612C00010000"]


def test_stream_quote_group_model_deduplicates_tiger_and_massive_option_symbols() -> None:
    groups = stream_quote_group_model(
        quotes={
            "MU": {"symbol": "MU", "latest_price": 120.0, "asset_class": "stocks"},
            "O:MU260618C01070000": {
                "symbol": "O:MU260618C01070000",
                "latest_price": 3.2,
                "asset_class": "options",
            },
        },
        bbo={
            "O:MU260618C01070000": {
                "symbol": "O:MU260618C01070000",
                "bid_price": 3.1,
                "ask_price": 3.3,
            },
        },
        subscriptions={
            "quotes": ["MU"],
            "options": ["MU 260618C01070000", "O:MU260618C01070000"],
        },
    )

    by_underlying = {group["underlying"]: group for group in groups}

    assert [option["symbol"] for option in by_underlying["MU"]["options"]] == ["O:MU260618C01070000"]
    assert by_underlying["MU"]["options"][0]["data"]["latest_price"] == 3.2
    assert by_underlying["MU"]["options"][0]["data"]["bid_price"] == 3.1


def test_holding_position_keys_deduplicate_option_aliases_without_underlying_collision() -> None:
    keys = holding_position_keys(["MU", "MU 260618C01070000/OPT/USD", "O:MU260618C01070000"])

    assert keys == {"MU 260618C01070000/OPT/USD", "O:MU260618C01070000"}
    assert holding_position_keys(["MU"]) == {"MU"}

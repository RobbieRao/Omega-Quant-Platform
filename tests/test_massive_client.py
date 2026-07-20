from __future__ import annotations

from omega_quant.adapters.massive_client import fetch_options_greeks
from omega_quant.adapters.rate_limits import massive_rate_limit_snapshot, reset_massive_rate_limits
from omega_quant.config import Settings


class Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_fetch_options_greeks_accepts_bid_price_field_names(monkeypatch) -> None:
    reset_massive_rate_limits()
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key")

    class FakeClient:
        def get_snapshot_option(self, *, underlying_asset, option_contract):
            assert underlying_asset == "MU"
            assert option_contract == "O:MU260618C01070000"
            return Obj(
                greeks=Obj(delta=0.42, gamma=0.03, theta=-0.02, vega=0.11),
                last_quote=Obj(bid_price=2.45, ask_price=2.55),
                implied_volatility=0.66,
                open_interest=1234,
                break_even_price=13.25,
            )

    monkeypatch.setattr("omega_quant.adapters.massive_client.build_rest_client", lambda settings: FakeClient())

    result = fetch_options_greeks(["O:MU260618C01070000"], Settings())

    assert result["O:MU260618C01070000"]["bid"] == 2.45
    assert result["O:MU260618C01070000"]["ask"] == 2.55
    assert result["O:MU260618C01070000"]["iv"] == 0.66
    assert massive_rate_limit_snapshot()["channels"]["rest"]["rpm"] == 1

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from omega_quant.adapters import live_market_readonly
from omega_quant.adapters.platform_context import (
    canonical_option_symbol,
    group_market_quotes,
    platform_capabilities,
    select_option_mark_price,
)
from omega_quant.data import ReadOnlyStreamCacheFeed
from omega_quant.market_data_modes import MarketDataModeController, MarketDataUnavailable
from omega_quant.market_events import MarketEvent, new_market_event, normalize_live_payload
from omega_quant.schemas import MarketEventRead


DIRECT_TIGER_SDK_CALLS = ("client.create_order(", "client.preview_order(", "client.place_order(", "client.cancel_order(")
TRADING_API_NAMES = {
    "execute_action",
    "create_order",
    "preview_order",
    "place_order",
    "submit_order",
    "cancel_order",
    "modify_order",
}


class FakeLiveFeed:
    def __init__(self, event: MarketEvent | None):
        self.event = event
        self.connected = event is not None
        self.subscribed: list[str] = []

    def connect(self, symbols=None):
        if symbols:
            self.subscribe(symbols)
        return self.status()

    def subscribe(self, symbols):
        self.subscribed.extend(str(symbol).upper() for symbol in symbols)
        return self.status()

    def snapshot(self, _symbol: str):
        return self.event

    def status(self):
        return {
            "status": "realtime" if self.connected else "disconnected",
            "connected": self.connected,
            "read_only": True,
            "subscribed_symbols": sorted(set(self.subscribed)),
            "last_event_at": self.event.received_at.isoformat() if self.event else None,
            "message": "fake readonly feed",
        }


def test_market_event_schema_accepts_stable_fields() -> None:
    event = new_market_event(
        source="live_readonly",
        symbol="aapl",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        price=101.25,
        volume=1000,
        bid=None,
        ask=None,
        last=101.25,
        data_quality="unit_test_live_readonly",
    )

    payload = MarketEventRead.model_validate(event.to_payload())

    assert payload.source == "live_readonly"
    assert payload.symbol == "AAPL"
    assert payload.price == 101.25
    assert payload.bid is None
    assert payload.ask is None


def test_live_payloads_normalize_to_market_event() -> None:
    seed_event = new_market_event(source="live_readonly", symbol="AAPL", timestamp=None, price=100, data_quality="unit_test")
    live_event = normalize_live_payload(
        {
            "symbol": "NVDA",
            "bid_price": 899.5,
            "ask_price": 900.5,
            "latest_price": 900.0,
            "volume": 2000,
            "data_quality": "realtime_sip_or_opra",
        }
    )

    assert seed_event.source == "live_readonly"
    assert live_event is not None
    assert live_event.source == "live_readonly"
    assert live_event.bid == 899.5
    assert live_event.ask == 900.5
    assert live_event.last == 900.0


def test_live_readonly_snapshot_rejects_stale_market_event(monkeypatch) -> None:
    stale_event = new_market_event(
        source="live_readonly",
        symbol="AAPL",
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=10),
        received_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        price=100,
        data_quality="unit_test_stale",
    )
    controller = MarketDataModeController(live_adapter=FakeLiveFeed(stale_event), initial_mode="live_readonly")
    monkeypatch.setenv("OMEGA_LIVE_READONLY_MAX_AGE_SECONDS", "60")

    try:
        controller.snapshot_with_bars("AAPL")
    except MarketDataUnavailable as exc:
        assert "超过 60 秒未更新" in str(exc)
    else:
        raise AssertionError("stale live readonly market data should pause paper simulation")


def test_legacy_data_modes_are_rejected_instead_of_silently_mapped() -> None:
    for mode in ("mock", "replay"):
        try:
            MarketDataModeController(initial_mode=mode)
        except ValueError as exc:
            assert "unsupported data mode" in str(exc)
        else:
            raise AssertionError(f"{mode} should be rejected")


def test_live_readonly_adapter_public_api_has_no_trading_functions() -> None:
    module_public = {name for name in dir(live_market_readonly) if not name.startswith("_")}
    class_public = {name for name in dir(live_market_readonly.LiveMarketReadonlyAdapter) if not name.startswith("_")}

    assert module_public.isdisjoint(TRADING_API_NAMES)
    assert class_public.isdisjoint(TRADING_API_NAMES)


def test_live_readonly_channel_defaults_and_updates() -> None:
    adapter = live_market_readonly.LiveMarketReadonlyAdapter()

    assert adapter.status()["subscribe_quotes"] is True
    assert adapter.status()["subscribe_trades"] is False

    status = adapter.set_channels(subscribe_quotes=True, subscribe_trades=True)

    assert status["read_only"] is True
    assert status["subscribe_quotes"] is True
    assert status["subscribe_trades"] is True

    status = adapter.set_channels(subscribe_quotes=False, subscribe_trades=False)

    assert status["status"] == "no_channels"
    assert status["connected"] is False


def test_live_readonly_subscriptions_canonicalize_option_symbols() -> None:
    adapter = live_market_readonly.LiveMarketReadonlyAdapter()

    status = adapter.subscribe([" aapl ", "MU 260618C01070000", "O:MU260618C01070000"])

    assert status["subscribed_symbols"] == ["AAPL", "O:MU260618C01070000"]


def test_live_readonly_connect_restarts_when_subscription_changes(monkeypatch) -> None:
    started = threading.Event()
    stopped = threading.Event()
    instances = []

    class FakeProvider:
        def __init__(self, settings, watchlist, on_event, on_status, stop_event, **kwargs):
            del settings, on_event
            self.watchlist = watchlist
            self.on_status = on_status
            self.stop_event = stop_event
            self.kwargs = kwargs
            instances.append(self)

        def run(self):
            self.on_status({"status": "connected", "connected": True})
            started.set()
            self.stop_event.wait(5)
            stopped.set()

    monkeypatch.setattr(live_market_readonly, "MassiveMarketDataProvider", FakeProvider)
    adapter = live_market_readonly.LiveMarketReadonlyAdapter()

    adapter.connect(["AAPL"])
    assert started.wait(1)

    adapter.subscribe(["MSFT"])
    status = adapter.connect()

    assert stopped.wait(1)
    assert len(instances) == 2
    assert instances[0].watchlist.stocks == ("AAPL",)
    assert instances[1].watchlist.stocks == ("AAPL", "MSFT")
    assert status["subscribed_symbols"] == ["AAPL", "MSFT"]


def test_live_readonly_connect_reuses_socket_when_subscription_unchanged(monkeypatch) -> None:
    started = threading.Event()
    instances = []

    class FakeProvider:
        def __init__(self, settings, watchlist, on_event, on_status, stop_event, **kwargs):
            del settings, watchlist, on_event, kwargs
            self.on_status = on_status
            self.stop_event = stop_event
            instances.append(self)

        def run(self):
            self.on_status({"status": "connected", "connected": True})
            started.set()
            self.stop_event.wait(5)

    monkeypatch.setattr(live_market_readonly, "MassiveMarketDataProvider", FakeProvider)
    adapter = live_market_readonly.LiveMarketReadonlyAdapter()

    adapter.connect(["AAPL"])
    assert started.wait(1)
    adapter.connect(["AAPL"])

    assert len(instances) == 1
    adapter.disconnect()


def test_live_readonly_adapter_notifies_market_event_listeners() -> None:
    adapter = live_market_readonly.LiveMarketReadonlyAdapter()
    seen: list[MarketEvent] = []

    adapter.add_event_listener(seen.append)
    adapter._record_event("stocks", "AAPL", {"symbol": "AAPL", "bid_price": 100.0, "ask_price": 100.2, "latest_price": 100.1})

    assert len(seen) == 1
    assert seen[0].symbol == "AAPL"
    assert adapter.status()["events_received"] == 1
    assert adapter.status()["connected"] is True


def test_omega_business_source_does_not_call_tiger_sdk_directly() -> None:
    # TigerOpen SDK execution calls are allowed only in broker adapters and
    # vn.py-style gateway implementations. Business services, data adapters and
    # frontend-facing modules must not call them directly.
    source = "\n".join(
        path.read_text()
        for path in Path("src/omega_quant").rglob("*.py")
        if "broker" not in path.parts
        and "gateways" not in path.parts
        and "core" not in path.parts
        and "vnpy_core" not in path.parts
    )

    for call in DIRECT_TIGER_SDK_CALLS:
        assert call not in source


def test_omega_option_symbol_canonicalization_dedupes_prefix_variants() -> None:
    assert canonical_option_symbol("O:NVDA260612C00010000") == "O:NVDA260612C00010000"
    assert canonical_option_symbol("NVDA 20260612C00010000") == "O:NVDA260612C00010000"
    assert canonical_option_symbol("NVDA260612C00010000") == "O:NVDA260612C00010000"


def test_omega_quote_grouping_pairs_stock_with_options() -> None:
    groups = group_market_quotes(
        {
            "NVDA": {"symbol": "NVDA", "latest_price": 900},
            "O:NVDA260612C00010000": {"symbol": "O:NVDA260612C00010000", "latest_price": 12.5, "asset_class": "options"},
            "MU": {"symbol": "MU", "latest_price": 120},
        },
        subscribed_stocks=["NVDA", "MU"],
        subscribed_options=["NVDA260612C00010000"],
    )

    nvda = next(group for group in groups if group["underlying"] == "NVDA")
    mu = next(group for group in groups if group["underlying"] == "MU")

    assert nvda["stock_symbol"] == "NVDA"
    assert nvda["options"][0]["symbol"] == "O:NVDA260612C00010000"
    assert mu["stock_symbol"] == "MU"
    assert mu["options"] == []


def test_stream_cache_feed_canonicalizes_option_aliases_and_prefers_trade_last(tmp_path: Path) -> None:
    cache_path = tmp_path / "live_stream.json"
    cache_path.write_text(
        """
        {
          "lastEventAt": "2026-05-24T12:00:00+00:00",
          "quotes": {
            "MU260618C01070000": {
              "symbol": "MU260618C01070000",
              "latest_price": 1.10,
              "bid_price": 1.00,
              "ask_price": 1.20,
              "received_at": "2026-05-24T11:59:58+00:00"
            }
          },
          "bbo": {
            "O:MU260618C01070000": {
              "symbol": "O:MU260618C01070000",
              "bid_price": 1.04,
              "ask_price": 1.16
            }
          },
          "ticks": {
            "O:MU260618C01070000": {
              "symbol": "O:MU260618C01070000",
              "latest_price": 1.14,
              "volume": 22,
              "received_at": "2026-05-24T12:00:00+00:00"
            }
          }
        }
        """,
        encoding="utf-8",
    )

    event = ReadOnlyStreamCacheFeed(cache_path).latest("MU 260618C01070000")

    assert event is not None
    assert event.symbol == "O:MU260618C01070000"
    assert event.price == 1.14
    assert event.last == 1.14
    assert event.bid == 1.04
    assert event.ask == 1.16
    assert event.volume == 22


def test_option_mark_price_prefers_fresh_trade_inside_book_then_midpoint() -> None:
    fresh = select_option_mark_price(
        bbo={"bid_price": 10.0, "ask_price": 10.2, "mid_price": 10.1},
        last_trade={"latest_price": 10.1, "received_at_ms": 1_000},
        current_ms=2_000,
    )
    stale_outside = select_option_mark_price(
        bbo={"bid_price": 10.0, "ask_price": 10.2, "mid_price": 10.1},
        last_trade={"latest_price": 14.0, "received_at_ms": 1_000},
        current_ms=60_000,
    )

    assert fresh and fresh["mark_source"] == "last_trade"
    assert stale_outside and stale_outside["mark_source"] == "midpoint"


def test_platform_capabilities_include_massive_limits() -> None:
    payload = platform_capabilities()

    assert payload["read_only"] is True
    assert payload["rate_limits"]["channels"]["rest"]["limitRpm"] == 500
    assert payload["rate_limits"]["channels"]["websocket_stocks"]["limitRpm"] == 1000
    assert payload["rate_limits"]["channels"]["websocket_options"]["limitRpm"] == 50

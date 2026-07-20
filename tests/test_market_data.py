from __future__ import annotations

import asyncio
import sys
import threading
import types

from omega_quant.adapters.massive_stream import MarketDataWatchlist, MassiveMarketDataProvider
from omega_quant.adapters.rate_limits import massive_rate_limit_snapshot, reset_massive_rate_limits
from omega_quant.config import Settings


def test_settings_default_massive_urls_match_official_service(monkeypatch) -> None:
    monkeypatch.delenv("OMEGA_MASSIVE_HOST", raising=False)
    monkeypatch.delenv("OMEGA_MASSIVE_SECURE", raising=False)

    settings = Settings()

    assert settings.resolved_massive_secure is True
    assert settings.massive_rest_base_url == "https://api.massive.com"
    assert settings.massive_ws_host == "socket.massive.com"


def test_settings_allow_explicit_insecure_massive_host(monkeypatch) -> None:
    monkeypatch.setenv("OMEGA_MASSIVE_HOST", "http://example.test")

    settings = Settings()

    assert settings.resolved_massive_secure is False
    assert settings.massive_rest_base_url == "http://example.test"
    assert settings.massive_ws_host == "socket.example.test"


def test_settings_default_massive_channels_are_quote_only(monkeypatch) -> None:
    monkeypatch.delenv("OMEGA_MASSIVE_SUBSCRIBE_QUOTES", raising=False)
    monkeypatch.delenv("OMEGA_MASSIVE_SUBSCRIBE_TRADES", raising=False)
    settings = Settings()

    assert settings.resolved_massive_subscribe_quotes is True
    assert settings.resolved_massive_subscribe_trades is False


def test_settings_loads_gitignored_local_secrets_without_overriding_env(monkeypatch, tmp_path) -> None:
    local_secrets = tmp_path / "local.secrets.env"
    local_secrets.write_text(
        "\n".join(
            [
                'MASSIVE_API_KEY="file-key"',
                'OMEGA_MASSIVE_HOST="massive.test"',
                'OMEGA_MASSIVE_SECURE="0"',
            ]
        )
    )
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("OMEGA_DISABLE_LOCAL_SECRETS", raising=False)

    settings = Settings(local_secrets_path=local_secrets)

    assert settings.massive_api_key == "file-key"
    assert settings.massive_rest_base_url == "http://api.massive.test"

    monkeypatch.setenv("MASSIVE_API_KEY", "env-key")

    assert Settings(local_secrets_path=local_secrets).massive_api_key == "env-key"


def test_settings_can_disable_local_secrets(monkeypatch, tmp_path) -> None:
    local_secrets = tmp_path / "local.secrets.env"
    local_secrets.write_text('MASSIVE_API_KEY="file-key"\n')
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.setenv("OMEGA_DISABLE_LOCAL_SECRETS", "1")

    assert Settings(local_secrets_path=local_secrets).massive_api_key == ""


def test_massive_provider_starts_stock_and_option_sockets_concurrently(monkeypatch) -> None:
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key")
    stop_event = threading.Event()
    both_started = threading.Event()
    started: list[str] = []
    sent: list[tuple[str, str]] = []

    class FakeSocket:
        def __init__(self, url: str):
            self.url = url
            self.recv_count = 0

        async def __aenter__(self):
            started.append(self.url)
            if len(started) == 2:
                both_started.set()
            return self

        async def __aexit__(self, *_args):
            return None

        async def send(self, payload: str):
            sent.append((self.url, payload))

        async def recv(self):
            self.recv_count += 1
            if self.recv_count == 1:
                return '[{"ev":"status","status":"auth_success","message":"authenticated"}]'
            if self.recv_count == 2:
                return '[{"ev":"status","status":"success","message":"subscribed"}]'
            while not stop_event.is_set():
                await asyncio.sleep(0.01)
            return "[]"

    websockets_mod = types.ModuleType("websockets")
    websockets_mod.connect = lambda url, **_kwargs: FakeSocket(url)
    monkeypatch.setitem(sys.modules, "websockets", websockets_mod)

    provider = MassiveMarketDataProvider(
        Settings(massive_host="massive.test", massive_secure=False),
        MarketDataWatchlist(stocks=("AAPL",), options=("O:MU260618C01070000",)),
        lambda *_args: None,
        lambda *_args: None,
        stop_event,
    )

    thread = threading.Thread(target=provider.run, daemon=True)
    thread.start()

    assert both_started.wait(1.0)
    stop_event.set()
    thread.join(timeout=1.0)

    assert set(started) == {"ws://socket.massive.test/stocks", "ws://socket.massive.test/options"}
    assert any('"params": "test-key"' in payload for _, payload in sent)
    assert any('"params": "Q.AAPL"' in payload for _, payload in sent)
    assert any('"params": "Q.O:MU260618C01070000"' in payload for _, payload in sent)
    assert not any('"params": "T.AAPL,Q.AAPL"' in payload for _, payload in sent)


def test_massive_provider_can_enable_trade_channels() -> None:
    provider = MassiveMarketDataProvider(
        Settings(),
        MarketDataWatchlist(stocks=("AAPL",), options=()),
        lambda *_args: None,
        lambda *_args: None,
        threading.Event(),
        subscribe_quotes=True,
        subscribe_trades=True,
    )

    assert provider._channels_for_symbols(
        ("AAPL",),
        subscribe_quotes=provider.subscribe_quotes,
        subscribe_trades=provider.subscribe_trades,
    ) == ["T.AAPL", "Q.AAPL"]


def test_massive_provider_tracks_stock_and_option_ws_rpm() -> None:
    reset_massive_rate_limits()
    events: list[tuple[str, str, dict]] = []
    statuses: list[dict] = []
    provider = MassiveMarketDataProvider(
        Settings(),
        MarketDataWatchlist(stocks=("AAPL",), options=("O:MU260618C01070000",)),
        lambda bucket, symbol, data: events.append((bucket, symbol, data)),
        statuses.append,
        threading.Event(),
    )

    provider._handle_control_message(
        '[{"ev":"Q","sym":"AAPL","bp":199.9,"ap":200.1,"bs":1,"as":2}]',
        asset_class="stocks",
    )
    provider._handle_control_message(
        '[{"ev":"Q","sym":"O:MU260618C01070000","bp":8.4,"ap":8.6,"bs":3,"as":4}]',
        asset_class="options",
    )

    rate_limits = massive_rate_limit_snapshot()["channels"]

    assert rate_limits["websocket_stocks"]["rpm"] == 1
    assert rate_limits["websocket_options"]["rpm"] == 1
    assert rate_limits["websocket_options"]["limitRpm"] == 50
    assert any(data.get("rate_limits") for _, _, data in events)

from __future__ import annotations

from omega_quant.config import Settings
from omega_quant.broker.account_stream import TigerPushStreamer, asset_event_key, now_ms


def test_market_data_retry_seconds_backoffs_on_missing_permission() -> None:
    streamer = TigerPushStreamer(Settings())
    streamer._state["marketData"]["status"] = "missing_permission"
    streamer._state["marketData"]["permission"] = "missing_permission"

    assert streamer._market_data_retry_seconds(5.0) == 60.0


def test_market_data_retry_seconds_keeps_normal_delay_for_connected_stream() -> None:
    streamer = TigerPushStreamer(Settings())
    streamer._state["marketData"]["status"] = "realtime"
    streamer._state["marketData"]["permission"] = "realtime"

    assert streamer._market_data_retry_seconds(5.0) == 5.0


def test_market_data_callbacks_ignore_stale_generation() -> None:
    streamer = TigerPushStreamer(Settings(stream_enabled=False))
    streamer._market_data_generation = 2

    streamer._record_external_market_event(
        "quotes",
        "AAPL",
        {"source": "massive", "symbol": "AAPL", "latest_price": 200, "received_at_ms": now_ms()},
        generation=1,
    )
    streamer._set_market_data_status({"status": "authenticated", "connected": True}, generation=1)

    assert "AAPL" not in streamer._state["quotes"]
    assert streamer._state["marketData"]["status"] == "idle"

    streamer._set_market_data_status({"status": "authenticated", "connected": True}, generation=2)

    assert streamer._state["marketData"]["status"] == "authenticated"


def test_market_data_pauses_after_sse_idle_timeout() -> None:
    class FakeThread:
        def __init__(self) -> None:
            self.joined = False

        def is_alive(self) -> bool:
            return True

        def join(self, timeout=None) -> None:
            self.joined = True

    streamer = TigerPushStreamer(
        Settings(stream_enabled=False, market_data_pause_without_sse_seconds=0.001)
    )
    fake_thread = FakeThread()
    streamer._market_data_thread = fake_thread
    streamer._last_sse_disconnect_ms = now_ms() - 1000
    streamer._state["marketData"]["connected"] = True

    streamer._maybe_pause_market_data_for_idle_sse()

    assert streamer._market_data_stop_event.is_set()
    assert fake_thread.joined is True
    assert streamer._state["marketData"]["status"] == "paused_no_viewers"
    assert streamer._state["marketData"]["connected"] is False
    assert streamer._state["marketData"]["pausedByNoViewers"] is True


def test_sse_client_registration_blocks_idle_market_data_pause() -> None:
    class FakeThread:
        def is_alive(self) -> bool:
            return True

        def join(self, timeout=None) -> None:
            raise AssertionError("active SSE clients should not pause market data")

    streamer = TigerPushStreamer(
        Settings(stream_enabled=False, market_data_pause_without_sse_seconds=0.001)
    )
    streamer.register_sse_client()
    streamer._market_data_thread = FakeThread()
    streamer._last_sse_disconnect_ms = now_ms() - 1000

    streamer._maybe_pause_market_data_for_idle_sse()

    assert streamer._market_data_stop_event.is_set() is False
    assert streamer._state["marketData"]["viewerCount"] == 1


def test_asset_event_key_is_stable_without_segment_or_account() -> None:
    assert asset_event_key({"currency": "usd"}, 1000) == "USD"
    assert asset_event_key({"currency": "USD"}, 2000) == "USD"

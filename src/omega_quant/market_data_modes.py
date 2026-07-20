from __future__ import annotations

import os
import threading
from typing import Any

from .adapters.live_market_readonly import LiveMarketReadonlyAdapter
from .data import bars_with_market_event
from .market_events import DATA_MODES, MarketEvent, MarketEventSource, assess_market_event_quality, normalize_data_mode


class MarketDataUnavailable(RuntimeError):
    pass


class MarketDataModeController:
    def __init__(self, live_adapter: LiveMarketReadonlyAdapter | None = None, initial_mode: str | None = None):
        if live_adapter is not None:
            self.live_adapter = live_adapter
        elif os.environ.get("OMEGA_DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "on"}:
            from .adapters.synthetic_market import SyntheticMarketDataAdapter

            self.live_adapter = SyntheticMarketDataAdapter()
        else:
            self.live_adapter = LiveMarketReadonlyAdapter()
        self._mode = normalize_data_mode(initial_mode or os.environ.get("OMEGA_DATA_MODE") or "live_readonly")
        self._lock = threading.Lock()

    @property
    def mode(self) -> MarketEventSource:
        with self._lock:
            return self._mode

    def set_mode(self, mode: str) -> dict[str, Any]:
        next_mode = normalize_data_mode(mode)
        with self._lock:
            self._mode = next_mode
        self.live_adapter.connect()
        return self.status()

    def status(self) -> dict[str, Any]:
        live_status = self.live_adapter.status()
        mode = self.mode
        return {
            "data_mode": mode,
            "available_modes": list(DATA_MODES),
            "execution_mode": "live",
            "live_trading": "enabled",
            "live_readonly_status": live_status,
            "message": self._mode_message(mode, live_status),
        }

    def subscribe_live(self, symbols: list[str]) -> dict[str, Any]:
        status = self.live_adapter.subscribe(symbols)
        return self.live_adapter.connect() if self.mode == "live_readonly" else status

    def unsubscribe_live(self, symbols: list[str]) -> dict[str, Any]:
        return self.live_adapter.unsubscribe(symbols)

    def snapshot_with_bars(self, symbol: str) -> tuple[MarketEvent, Any]:
        quality: dict[str, Any] | None = None
        live_event = self.live_adapter.snapshot(symbol)
        if live_event is not None:
            quality = assess_market_event_quality(live_event, max_age_seconds=_live_max_age_seconds())
            if quality["ok"]:
                return live_event, bars_with_market_event(live_event)
            # A stale event is an explicit feed-integrity failure. Do not hide it
            # behind another provider: the caller must see the precise reason the
            # current live decision path was paused.
            if quality.get("message"):
                raise MarketDataUnavailable(str(quality["message"]))
        # Fallback 1: Massive REST (last trade + NBBO). WS 推送沉默时启用——许多 plan tier
        # 的 WS 不推 tick 但 REST 完全开放，能拿到真实 bid/ask/last。
        if _massive_rest_fallback_enabled():
            from .adapters.massive_rest_client import get_massive_rest_client

            try:
                rest_event = get_massive_rest_client().snapshot(symbol)
            except Exception:
                rest_event = None
            if rest_event is not None:
                return rest_event, bars_with_market_event(rest_event)
        # Fallback 2: TigerOpen delayed brief (~15min 延时), 仅在 Massive REST 也不可用时启用。
        if _delayed_fallback_enabled():
            from .adapters.tiger_delayed_quote import get_tiger_delayed_cache

            try:
                fallback_event = get_tiger_delayed_cache().snapshot(symbol)
            except Exception:
                fallback_event = None
            if fallback_event is not None:
                return fallback_event, bars_with_market_event(fallback_event)
        raise MarketDataUnavailable("实时只读行情暂不可用，Omega 已暂停本次纸面模拟；不会降级为模拟行情，也不会生成订单。")

    def market_snapshot(self, symbol: str) -> MarketEvent:
        event, _bars = self.snapshot_with_bars(symbol)
        return event

    @staticmethod
    def _mode_message(mode: MarketEventSource, live_status: dict[str, Any]) -> str:
        del mode
        if live_status.get("demo"):
            return "Omega demo is using deterministic synthetic data; no broker or data vendor is contacted."
        if live_status.get("connected"):
            return "当前使用真实实时行情驱动 TigerOpen 交易决策；订单会提交到当前配置的 TigerOpen 账户。"
        return "实时只读行情未连接或暂不可用；Omega 会暂停本次运行，不会降级为模拟行情。"


def _live_max_age_seconds() -> float:
    raw = os.environ.get("OMEGA_LIVE_READONLY_MAX_AGE_SECONDS")
    try:
        value = float(raw) if raw is not None else 300.0
    except ValueError:
        return 300.0
    return max(value, 1.0)


def _delayed_fallback_enabled() -> bool:
    raw = os.environ.get("OMEGA_TIGER_DELAYED_FALLBACK")
    if raw is None:
        return True  # default-on
    return raw.strip().lower() not in {"0", "false", "off", "no"}


def _massive_rest_fallback_enabled() -> bool:
    raw = os.environ.get("OMEGA_MASSIVE_REST_FALLBACK")
    if raw is None:
        return True  # default-on
    return raw.strip().lower() not in {"0", "false", "off", "no"}

"""Runtime wiring for Omega's vn.py-style core engines."""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..adapters.live_market_readonly import LiveMarketReadonlyAdapter
from ..gateways import MassiveMarketDataGateway, TigerOpenGateway
from ..market_events import MarketEvent
from ..models import CtaStrategyConfig, DataRecorderSetting, RiskManagerSetting
from .algo_trading import AlgoTradingEngine, IcebergAlgoSetting, TwapAlgoSetting
from .cta_backtester import CtaBacktestRequest, CtaBacktesterEngine
from .cta_strategy import CtaStrategyEngine
from .data_manager import DataManagerEngine
from .data_recorder import DataRecorderEngine, bar_to_recorded_bar, market_event_to_tick
from .engine import MainEngine
from .event import EVENT_TICK, Event
from .objects import BarData, CancelRequest, Direction, Exchange, PositionData, Product, TickData
from omega_quant.vnpy_core.trader.constant import Offset as _VnpyOffset, OrderType as _VnpyOrderType


def _ot(value: str) -> _VnpyOrderType:
    try:
        return _VnpyOrderType(value.lower())
    except ValueError:
        return _VnpyOrderType.LIMIT


def _offset(value: str) -> _VnpyOffset:
    key = (value or "").strip().lower()
    mapping = {"": _VnpyOffset.NONE, "none": _VnpyOffset.NONE, "open": _VnpyOffset.OPEN, "close": _VnpyOffset.CLOSE}
    return mapping.get(key, _VnpyOffset.NONE)
from .option_master import OptionMasterEngine
from .risk_manager import RiskManagerEngine
from .spread_trading import SpreadLeg, SpreadTradingEngine, make_option_leg, make_stock_leg


class OmegaCoreRuntime:
    """Single process runtime for headless vn.py-inspired engines."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        *,
        enable_tiger_push: bool = False,
        enable_tiger_gateway: bool = True,
        market_data_adapter: LiveMarketReadonlyAdapter | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.main_engine = MainEngine()
        self.main_engine.add_gateway(TigerOpenGateway)
        self.main_engine.add_gateway(MassiveMarketDataGateway)
        if enable_tiger_gateway:
            self.main_engine.connect({"enable_push": enable_tiger_push}, TigerOpenGateway.default_name)
        self.main_engine.connect({"adapter": market_data_adapter} if market_data_adapter is not None else {}, MassiveMarketDataGateway.default_name)
        self.algo_engine = self.main_engine.add_engine(AlgoTradingEngine)
        self.option_engine = self.main_engine.add_engine(OptionMasterEngine)
        self.spread_engine = self.main_engine.add_engine(SpreadTradingEngine)
        self.data_recorder = self.main_engine.add_engine(DataRecorderEngine, session_factory=session_factory)
        self.data_manager = self.main_engine.add_engine(DataManagerEngine, session_factory=session_factory)
        self.cta_backtester = self.main_engine.add_engine(CtaBacktesterEngine, session_factory=session_factory)
        self.cta_strategy = self.main_engine.add_engine(CtaStrategyEngine)
        self.risk_manager = self.main_engine.add_engine(RiskManagerEngine)
        self.restore_risk_manager_config()
        self.restore_data_recorder_settings()
        self.restore_cta_live_strategies()

    def close(self) -> None:
        self.main_engine.close()

    def status(self) -> dict[str, Any]:
        return {
            "runtime": "omega_core_runtime",
            "gateway_names": sorted(self.main_engine.gateways),
            "gateway_status": self._gateway_status(),
            "engine_names": sorted(self.main_engine.engines),
            "cta_strategies": self.cta_strategy.strategies_snapshot(),
            "active_algos": len(self.algo_engine.get_all_active_algos()),
            "finished_algos": len(self.algo_engine.get_all_finished_algos()),
            "option_portfolios": sorted(self.option_engine.portfolios),
            "spreads": sorted(self.spread_engine.spreads),
            "tick_recordings": sorted(self.data_recorder.tick_recordings),
            "bar_recordings": sorted(self.data_recorder.bar_recordings),
            "historical_datasets": len(self.data_manager.overview()),
            "risk_manager": self.risk_manager.snapshot(),
            "scope": "US stocks/options via TigerOpen gateway; no Qt, no domestic gateway, no crypto.",
        }

    def _gateway_status(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for name, gateway in sorted(self.main_engine.gateways.items()):
            if hasattr(gateway, "status"):
                payload[name.lower()] = gateway.status()
            else:
                payload[name.lower()] = {"gateway_name": name}
        if TigerOpenGateway.default_name.lower() not in payload:
            payload["tigeropen"] = {"connected": False, "push_connected": False, "push_error": "gateway missing"}
        return payload

    def gateways(self) -> list[dict[str, Any]]:
        status = self._gateway_status()
        return [status[name.lower()] for name in sorted(self.main_engine.gateways)]

    def orders(self) -> list[dict[str, Any]]:
        self.main_engine.event_engine.process_pending()
        return [
            {
                "vt_orderid": order.vt_orderid,
                "orderid": order.orderid,
                "symbol": order.symbol,
                "vt_symbol": order.vt_symbol,
                "exchange": order.exchange.value,
                "direction": order.direction.value,
                "volume": order.volume,
                "price": order.price,
                "traded": order.traded,
                "status": order.status.value,
                "datetime": order.datetime.isoformat() if order.datetime else None,
                "gateway_name": order.gateway_name,
                "broker_order_id": order.broker_order_id,
                "reference": order.reference,
                "risk_result": order.risk_result,
            }
            for order in self.main_engine.get_all_orders()
        ]

    def cancel_order(self, orderid: str, *, gateway_name: str = TigerOpenGateway.default_name) -> dict[str, Any]:
        self.main_engine.event_engine.process_pending()
        normalized = orderid.strip()
        order = self.main_engine.get_order(normalized)
        if order is None:
            order = next(
                (
                    item
                    for item in self.main_engine.get_all_orders()
                    if normalized in {item.orderid, item.broker_order_id or "", item.vt_orderid}
                ),
                None,
            )
        if order is None:
            raise ValueError(f"order not found: {orderid}")
        if not order.is_active():
            raise ValueError(f"order is not active: {order.vt_orderid} status={order.status.value}")
        cancel_id = order.broker_order_id or order.orderid
        cancel_requested = self.main_engine.cancel_order(
            CancelRequest(orderid=cancel_id, symbol=order.symbol, exchange=order.exchange),
            gateway_name,
        )
        self.main_engine.event_engine.process_pending()
        return {
            "cancel_requested": cancel_requested,
            "orderid": order.orderid,
            "vt_orderid": order.vt_orderid,
            "broker_order_id": order.broker_order_id,
            "cancel_request_id": cancel_id,
            "orders": self.orders(),
            "risk_manager": self.risk_manager.snapshot(),
        }

    def trades(self) -> list[dict[str, Any]]:
        self.main_engine.event_engine.process_pending()
        return [
            {
                "vt_tradeid": trade.vt_tradeid,
                "tradeid": trade.tradeid,
                "orderid": trade.orderid,
                "symbol": trade.symbol,
                "vt_symbol": trade.vt_symbol,
                "exchange": trade.exchange.value,
                "direction": trade.direction.value,
                "volume": trade.volume,
                "price": trade.price,
                "datetime": trade.datetime.isoformat(),
                "gateway_name": trade.gateway_name,
            }
            for trade in self.main_engine.get_all_trades()
        ]

    def accounts(self) -> list[dict[str, Any]]:
        self.main_engine.event_engine.process_pending()
        return [
            {
                "vt_accountid": account.vt_accountid,
                "accountid": account.accountid,
                "balance": account.balance,
                "frozen": account.frozen,
                "available": account.available,
                "cash": account.cash,
                "buying_power": account.buying_power,
                "gateway_name": account.gateway_name,
            }
            for account in self.main_engine.get_all_accounts()
        ]

    def positions(self) -> list[dict[str, Any]]:
        self.main_engine.event_engine.process_pending()
        return [
            {
                "vt_positionid": position.vt_positionid,
                "symbol": position.symbol,
                "vt_symbol": position.vt_symbol,
                "exchange": position.exchange.value,
                "direction": position.direction.value,
                "volume": position.volume,
                "price": position.price,
                "pnl": position.pnl,
                "gateway_name": position.gateway_name,
            }
            for position in self.main_engine.get_all_positions()
        ]

    def contracts(self) -> list[dict[str, Any]]:
        self.main_engine.event_engine.process_pending()
        return [
            {
                "vt_symbol": contract.vt_symbol,
                "symbol": contract.symbol,
                "exchange": contract.exchange.value,
                "name": contract.name,
                "product": contract.product.value,
                "size": contract.size,
                "pricetick": contract.pricetick,
                "currency": contract.currency,
                "gateway_name": contract.gateway_name,
            }
            for contract in self.main_engine.get_all_contracts()
        ]

    def query_contract(self, symbol: str, product: str | None = None) -> dict[str, Any]:
        gateway_name = TigerOpenGateway.default_name
        self.main_engine.query_contract(gateway_name, symbol, product=product)
        self.main_engine.event_engine.process_pending()
        normalized_symbol = symbol.strip().upper()
        for contract in self.contracts():
            if contract["symbol"] == normalized_symbol or contract["vt_symbol"] == f"{normalized_symbol}.SMART":
                return {"gateway_name": gateway_name, "contract": contract}
        return {"gateway_name": gateway_name, "contract": None}

    def query_tigeropen_snapshot(self) -> dict[str, Any]:
        gateway_name = TigerOpenGateway.default_name
        self.main_engine.query_account(gateway_name)
        self.main_engine.query_position(gateway_name)
        self.main_engine.query_order(gateway_name)
        self.main_engine.event_engine.process_pending()
        return {
            "gateway_name": gateway_name,
            "accounts": self.accounts(),
            "positions": self.positions(),
            "orders": self.orders(),
            "trades": self.trades(),
            "contracts": self.contracts(),
        }

    def submit_tick(
        self,
        *,
        symbol: str,
        last_price: float,
        bid_price: float | None = None,
        ask_price: float | None = None,
        volume: float = 0.0,
        exchange: Exchange = Exchange.SMART,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        tick = TickData(
            symbol=symbol.upper(),
            exchange=exchange,
            datetime=timestamp or datetime.now(timezone.utc),
            last_price=last_price,
            volume=volume,
            bid_price_1=bid_price,
            ask_price_1=ask_price,
            gateway_name="OMEGA_API",
        )
        return self._process_tick(tick, source="live_readonly")

    def submit_market_event(self, event: MarketEvent) -> dict[str, Any]:
        tick = market_event_to_tick(event, gateway_name="TIGEROPEN")
        return self._process_tick(tick, source=event.source, raw=event.raw)

    def _process_tick(self, tick: TickData, *, source: str, raw: dict | None = None) -> dict[str, Any]:
        tick_event = Event(EVENT_TICK, tick)
        self.main_engine.event_engine.put(tick_event)
        # Also process synchronously so API callers can immediately read updated state.
        oms = self.main_engine.get_engine("oms")
        if oms:
            oms.process_tick_event(tick_event)  # type: ignore[attr-defined]
        self.option_engine.process_tick_event(tick_event)
        self.spread_engine.process_tick_event(tick_event)
        self.cta_strategy.process_tick_event(tick_event)
        recorded_tick_id: str | None = None
        vt_symbol = tick.vt_symbol.upper()
        should_record_tick = vt_symbol in self.data_recorder.tick_recordings
        should_record_bar = vt_symbol in self.data_recorder.bar_recordings
        if should_record_tick or should_record_bar:
            row = self.data_recorder.record_tick_once(tick, source=source, raw=raw)
            recorded_tick_id = row.id if row else None
        return {
            "symbol": tick.symbol,
            "vt_symbol": tick.vt_symbol,
            "last_price": tick.last_price,
            "bid_price": tick.bid_price_1,
            "ask_price": tick.ask_price_1,
            "recorded_tick_id": recorded_tick_id,
            "updated": True,
        }

    def submit_position(
        self,
        *,
        symbol: str,
        direction: Direction,
        volume: float,
        price: float,
        exchange: Exchange = Exchange.SMART,
    ) -> dict[str, Any]:
        position = PositionData(
            symbol=symbol.upper(),
            exchange=exchange,
            direction=direction,
            volume=volume,
            price=price,
            gateway_name="OMEGA_API",
        )
        self.main_engine.event_engine.put(Event("ePosition.", position))
        self.option_engine.process_position_event(Event("ePosition.", position))
        self.spread_engine.process_position_event(Event("ePosition.", position))
        return {
            "symbol": position.symbol,
            "vt_symbol": position.vt_symbol,
            "direction": position.direction.value,
            "volume": position.volume,
            "price": position.price,
            "updated": True,
        }

    def recording_status(self) -> dict[str, Any]:
        return {
            "tick_recordings": sorted(self.data_recorder.tick_recordings),
            "bar_recordings": sorted(self.data_recorder.bar_recordings),
            "engine": "data_recorder",
        }

    def add_tick_recording(self, vt_symbol: str) -> dict[str, Any]:
        normalized = vt_symbol.upper()
        self.data_recorder.add_tick_recording(normalized)
        self._set_data_recorder_setting(normalized, "tick", enabled=True)
        return self.recording_status()

    def remove_tick_recording(self, vt_symbol: str) -> dict[str, Any]:
        normalized = vt_symbol.upper()
        self.data_recorder.remove_tick_recording(normalized)
        self._set_data_recorder_setting(normalized, "tick", enabled=False)
        return self.recording_status()

    def add_bar_recording(self, vt_symbol: str) -> dict[str, Any]:
        normalized = vt_symbol.upper()
        self.data_recorder.add_bar_recording(normalized)
        self._set_data_recorder_setting(normalized, "bar", enabled=True)
        return self.recording_status()

    def remove_bar_recording(self, vt_symbol: str) -> dict[str, Any]:
        normalized = vt_symbol.upper()
        self.data_recorder.remove_bar_recording(normalized)
        self._set_data_recorder_setting(normalized, "bar", enabled=False)
        return self.recording_status()

    def restore_data_recorder_settings(self) -> dict[str, Any]:
        if not self.session_factory:
            return self.recording_status()
        session = self.session_factory()
        try:
            rows = list(
                session.scalars(
                    select(DataRecorderSetting)
                    .where(DataRecorderSetting.enabled.is_(True))
                    .order_by(DataRecorderSetting.created_at)
                )
            )
        finally:
            session.close()
        for row in rows:
            if row.data_type == "tick":
                self.data_recorder.add_tick_recording(row.vt_symbol)
            elif row.data_type == "bar":
                self.data_recorder.add_bar_recording(row.vt_symbol)
        return self.recording_status()

    def _set_data_recorder_setting(self, vt_symbol: str, data_type: str, *, enabled: bool) -> None:
        if not self.session_factory:
            return
        session = self.session_factory()
        try:
            stmt = select(DataRecorderSetting).where(
                DataRecorderSetting.vt_symbol == vt_symbol.upper(),
                DataRecorderSetting.data_type == data_type,
            )
            row = session.scalars(stmt).first()
            if not row:
                row = DataRecorderSetting(vt_symbol=vt_symbol.upper(), data_type=data_type)
                session.add(row)
            row.enabled = enabled
            session.commit()
        finally:
            session.close()

    def record_bar(
        self,
        *,
        symbol: str,
        interval: str,
        timestamp: datetime,
        open_price: float,
        high_price: float,
        low_price: float,
        close_price: float,
        volume: float = 0.0,
        exchange: Exchange = Exchange.SMART,
        source: str = "live_readonly",
    ) -> dict[str, Any]:
        bar = BarData(
            symbol=symbol.upper(),
            exchange=exchange,
            datetime=timestamp,
            interval=interval,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            volume=volume,
            gateway_name="OMEGA_API",
        )
        row = self.data_recorder.record_bar(bar_to_recorded_bar(bar, source=source))
        return {
            "id": row.id,
            "symbol": row.symbol,
            "vt_symbol": row.vt_symbol,
            "interval": row.interval,
            "timestamp": row.timestamp.isoformat(),
            "close_price": row.close_price,
            "recorded": True,
        }

    def data_overview(self) -> list[dict[str, Any]]:
        return [
            {
                "vt_symbol": item.vt_symbol,
                "data_type": item.data_type,
                "interval": item.interval,
                "count": item.count,
                "start": item.start.isoformat() if item.start else None,
                "end": item.end.isoformat() if item.end else None,
                "sources": list(item.sources),
            }
            for item in self.data_manager.overview()
        ]

    def aggregate_ticks_to_bars(
        self,
        *,
        vt_symbol: str,
        interval: str = "1m",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        return self.data_manager.aggregate_ticks_to_bars(vt_symbol, interval=interval, start=start, end=end)

    def export_bars_csv(
        self,
        *,
        vt_symbol: str,
        interval: str = "1m",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> str:
        return self.data_manager.export_bars_csv(vt_symbol, interval=interval, start=start, end=end)

    def import_bars_csv(
        self,
        *,
        csv_text: str,
        vt_symbol: str | None = None,
        interval: str = "1m",
        source: str = "csv_import",
    ) -> dict[str, Any]:
        return self.data_manager.import_bars_csv(csv_text, vt_symbol=vt_symbol, interval=interval, source=source)

    def delete_symbol_data(self, vt_symbol: str) -> dict[str, int]:
        return self.data_manager.delete_symbol_data(vt_symbol)

    def download_tigeropen_history(
        self,
        *,
        vt_symbol: str,
        interval: str = "1m",
        start: datetime | None = None,
        end: datetime | None = None,
        product: str = "equity",
        limit: int | None = None,
    ) -> dict[str, Any]:
        from ..adapters.tigeropen_history import download_history_bars

        rows, summary = download_history_bars(
            vt_symbol,
            interval=interval,
            start=start,
            end=end,
            product=product,
            limit=limit,
        )
        if rows:
            result = self.data_manager.import_history_bars(rows, source="tigeropen_history")
            summary.bars_inserted = int(result.get("bars_created", 0) or 0)
            summary.bars_skipped = int(result.get("bars_skipped", 0) or 0)
        return summary.asdict()

    def run_cta_backtest(self, request: CtaBacktestRequest) -> dict[str, Any]:
        return self.cta_backtester.run_backtest(request)

    def optimize_cta_generic(
        self,
        base_request: CtaBacktestRequest,
        *,
        algorithm: str,
        target: str,
        parameter_values: dict[str, list[Any]] | None,
        parameter_ranges: dict[str, dict[str, Any]] | None,
        population_size: int = 20,
        generations: int = 10,
        crossover_prob: float = 0.7,
        mutation_prob: float = 0.3,
    ) -> dict[str, Any]:
        if algorithm == "genetic_algorithm":
            if not parameter_ranges and not parameter_values:
                raise ValueError("genetic_algorithm 需要 parameter_ranges 或 parameter_values 之一")
            return self.cta_backtester.optimize_ga_generic(
                base_request,
                parameter_ranges=parameter_ranges,
                parameter_values=parameter_values,
                target=target,
                population_size=population_size,
                generations=generations,
                crossover_prob=crossover_prob,
                mutation_prob=mutation_prob,
            )
        if not parameter_values:
            raise ValueError("brute_force 需要 parameter_values")
        return self.cta_backtester.optimize_generic(
            base_request,
            parameter_values=parameter_values,
            target=target,
        )

    def cta_strategies(self) -> list[dict[str, Any]]:
        return self.cta_backtester.list_strategies()

    def list_strategy_files(self) -> list[dict[str, Any]]:
        from .strategy_loader import list_strategy_files

        return [info.asdict() for info in list_strategy_files()]

    def list_builtin_strategies(self) -> list[dict[str, Any]]:
        from .strategy_loader import list_builtin_strategies

        return [info.asdict() for info in list_builtin_strategies()]

    def read_builtin_strategy(self, class_name: str) -> str:
        from .strategy_loader import read_builtin_strategy

        return read_builtin_strategy(class_name)

    def read_strategy_file(self, name: str) -> str:
        from .strategy_loader import read_strategy_file

        return read_strategy_file(name)

    def write_strategy_file(self, name: str, content: str) -> dict[str, Any]:
        from .strategy_loader import _validate_filename, _file_path, _LOADED_FILE_CLASSES, reload_strategy_file_with_engine

        # Write content first (with syntax pre-check); then reload + auto-restart
        path = _file_path(name)
        try:
            compile(content, _validate_filename(name), "exec")
        except SyntaxError as exc:
            raise ValueError(f"Python 语法错误: line {exc.lineno}: {exc.msg}") from exc
        path.write_text(content, encoding="utf-8")
        info, restarted = reload_strategy_file_with_engine(path.stem, engine=self.cta_strategy)
        result = info.asdict()
        result["restarted_strategies"] = restarted
        return result

    def delete_strategy_file(self, name: str) -> dict[str, Any]:
        from .strategy_loader import delete_strategy_file

        return {"deleted": delete_strategy_file(name), "name": name}

    def send_notification(self, *, title: str, body: str, level: str = "info") -> dict[str, Any]:
        """Generic notification dispatcher.

        Reads webhook URLs from env vars (no secrets baked in code):
          - OMEGA_WEBHOOK_URL: any JSON POST endpoint
          - OMEGA_WECHAT_WEBHOOK: WeChat work-group bot endpoint (markdown msg)

        Each is optional. Returns delivery status per channel.
        """

        import os
        import json as _json
        import urllib.request as _req

        delivered: dict[str, Any] = {}
        generic = os.environ.get("OMEGA_WEBHOOK_URL", "").strip()
        if generic:
            try:
                data = _json.dumps({"title": title, "body": body, "level": level}).encode("utf-8")
                req = _req.Request(generic, data=data, headers={"Content-Type": "application/json"})
                with _req.urlopen(req, timeout=5) as resp:
                    delivered["webhook"] = {"status": resp.status}
            except Exception as exc:  # noqa: BLE001
                delivered["webhook"] = {"error": str(exc)}
        wechat = os.environ.get("OMEGA_WECHAT_WEBHOOK", "").strip()
        if wechat:
            try:
                payload = {"msgtype": "markdown", "markdown": {"content": f"### {title}\n\n{body}"}}
                data = _json.dumps(payload).encode("utf-8")
                req = _req.Request(wechat, data=data, headers={"Content-Type": "application/json"})
                with _req.urlopen(req, timeout=5) as resp:
                    delivered["wechat"] = {"status": resp.status}
            except Exception as exc:  # noqa: BLE001
                delivered["wechat"] = {"error": str(exc)}
        if not delivered:
            delivered["skipped"] = "未配置 OMEGA_WEBHOOK_URL 或 OMEGA_WECHAT_WEBHOOK"
        return delivered

    def reload_strategy_files(self) -> list[dict[str, Any]]:
        from .strategy_loader import reload_all_strategy_files

        return [info.asdict() for info in reload_all_strategy_files()]

    def submit_manual_order(
        self,
        *,
        symbol: str,
        exchange: str,
        direction: str,
        order_type: str,
        volume: float,
        price: float = 0.0,
        offset: str = "NONE",
        reference: str = "manual",
        gateway_name: str | None = None,
        outside_rth: bool = False,
        time_in_force: str = "DAY",
    ) -> dict[str, Any]:
        from .objects import OrderRequest

        if volume <= 0:
            raise ValueError("数量必须大于 0")
        exchange_enum = Exchange(exchange.upper())
        direction_enum = Direction.LONG if direction.lower() in {"buy", "long"} else Direction.SHORT
        order_type_upper = order_type.upper()
        order_type_enum = {
            "LIMIT": _ot("LIMIT"),
            "MARKET": _ot("MARKET"),
            "STOP": _ot("STOP"),
        }.get(order_type_upper, _ot("LIMIT"))
        offset_enum = _offset(offset)
        req = OrderRequest(
            symbol=symbol.upper(),
            exchange=exchange_enum,
            direction=direction_enum,
            type=order_type_enum,
            volume=float(volume),
            price=float(price),
            offset=offset_enum,
            reference=reference,
        )
        gateway = gateway_name or "TIGEROPEN"
        # Buying-power preflight: estimate notional and check against latest
        # buying_power snapshot. Conservative — skip the check on sells (which
        # raise BP) and only block on shortfall, never on missing data.
        if direction_enum is Direction.LONG and price > 0:
            try:
                from ..adapters.account_context import get_real_account_snapshot

                snap = get_real_account_snapshot()
                bp = snap.get("buying_power")
                if bp is not None:
                    notional = float(price) * float(volume)
                    if notional > float(bp):
                        raise ValueError(
                            f"购买力不足: 需要 ${notional:,.2f} 但 buying_power 仅 ${float(bp):,.2f}"
                        )
            except ValueError:
                raise
            except Exception:
                # Don't block on snapshot errors; broker will reject if truly short.
                pass
        gateway_obj = self.main_engine.get_gateway(gateway)
        if gateway_obj is not None and hasattr(gateway_obj, "set_next_order_options"):
            gateway_obj.set_next_order_options(
                outside_rth=bool(outside_rth),
                time_in_force=str(time_in_force or "DAY").upper(),
            )
        vt_orderid = self.main_engine.send_order(req, gateway)
        return {
            "vt_orderid": vt_orderid,
            "symbol": symbol.upper(),
            "exchange": exchange_enum.value,
            "direction": direction_enum.value,
            "type": order_type_enum.value,
            "volume": float(volume),
            "price": float(price),
            "offset": offset_enum.value,
            "gateway_name": gateway,
            "outside_rth": bool(outside_rth),
            "time_in_force": str(time_in_force or "DAY").upper(),
        }

    def cta_live_templates(self) -> list[dict[str, Any]]:
        return self.cta_strategy.list_templates()

    def cta_live_strategies(self) -> list[dict[str, Any]]:
        return self.cta_strategy.strategies_snapshot()

    def add_cta_live_strategy(self, payload: Any) -> dict[str, Any]:
        snapshot = self.cta_strategy.add_strategy(
            strategy_name=payload.strategy_name,
            class_name=payload.class_name,
            vt_symbol=payload.vt_symbol,
            setting=payload.setting,
            gateway_name=payload.gateway_name or TigerOpenGateway.default_name,
            product=Product(payload.product),
            exchange=Exchange(payload.exchange),
        )
        self._upsert_cta_strategy_config(snapshot, status="stopped")
        return snapshot

    def start_cta_live_strategy(self, strategy_name: str) -> dict[str, Any]:
        snapshot = self.cta_strategy.start_strategy(strategy_name)
        self._set_cta_strategy_status(strategy_name, "running")
        return snapshot

    def stop_cta_live_strategy(self, strategy_name: str) -> dict[str, Any]:
        snapshot = self.cta_strategy.stop_strategy(strategy_name)
        self._set_cta_strategy_status(strategy_name, "stopped")
        return snapshot

    def remove_cta_live_strategy(self, strategy_name: str) -> dict[str, Any]:
        result = self.cta_strategy.remove_strategy(strategy_name)
        self._delete_cta_strategy_config(strategy_name)
        return result

    def edit_cta_live_strategy(self, strategy_name: str, setting: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.cta_strategy.edit_strategy(strategy_name, setting)
        self._persist_cta_strategy_setting(strategy_name, snapshot.get("parameters") or {})
        return snapshot

    def restore_cta_live_strategies(self) -> list[dict[str, Any]]:
        if not self.session_factory:
            return []
        session = self.session_factory()
        restored: list[dict[str, Any]] = []
        try:
            rows = list(session.scalars(select(CtaStrategyConfig).order_by(CtaStrategyConfig.created_at)))
        finally:
            session.close()
        for row in rows:
            try:
                snapshot = self.cta_strategy.add_strategy(
                    strategy_name=row.strategy_name,
                    class_name=row.class_name,
                    vt_symbol=row.vt_symbol,
                    setting=row.setting_json or {},
                    gateway_name=row.gateway_name,
                    product=Product(row.product),
                    exchange=Exchange(row.exchange),
                )
                if row.status == "running":
                    snapshot = self.cta_strategy.start_strategy(row.strategy_name)
                restored.append(snapshot)
            except Exception as exc:  # noqa: BLE001
                self.main_engine.write_log(
                    f"restore CTA strategy failed: {row.strategy_name}: {exc!r}",
                    source="cta_strategy",
                )
        return restored

    def _upsert_cta_strategy_config(self, snapshot: dict[str, Any], *, status: str) -> None:
        if not self.session_factory:
            return
        session = self.session_factory()
        try:
            row = session.get(CtaStrategyConfig, snapshot["strategy_name"])
            if not row:
                row = CtaStrategyConfig(strategy_name=snapshot["strategy_name"])
                session.add(row)
            row.class_name = snapshot["class_name"]
            row.vt_symbol = snapshot["vt_symbol"]
            row.setting_json = dict(snapshot.get("parameters") or {})
            row.gateway_name = snapshot["gateway_name"]
            row.product = snapshot["product"]
            symbol, exchange = snapshot["vt_symbol"].rsplit(".", 1) if "." in snapshot["vt_symbol"] else (snapshot["vt_symbol"], "SMART")
            _ = symbol
            row.exchange = exchange
            row.status = status
            session.commit()
        finally:
            session.close()

    def _set_cta_strategy_status(self, strategy_name: str, status: str) -> None:
        if not self.session_factory:
            return
        session = self.session_factory()
        try:
            row = session.get(CtaStrategyConfig, strategy_name)
            if row:
                row.status = status
                session.commit()
        finally:
            session.close()

    def _persist_cta_strategy_setting(self, strategy_name: str, setting: dict[str, Any]) -> None:
        if not self.session_factory or not setting:
            return
        session = self.session_factory()
        try:
            row = session.get(CtaStrategyConfig, strategy_name)
            if row:
                merged = dict(row.setting or {})
                merged.update(setting)
                row.setting = merged
                session.commit()
        finally:
            session.close()

    def _delete_cta_strategy_config(self, strategy_name: str) -> None:
        if not self.session_factory:
            return
        session = self.session_factory()
        try:
            row = session.get(CtaStrategyConfig, strategy_name)
            if row:
                session.delete(row)
                session.commit()
        finally:
            session.close()

    def optimize_cta_backtest(
        self,
        request: CtaBacktestRequest,
        *,
        fast_values: list[int],
        slow_values: list[int],
        target: str = "total_return",
    ) -> list[dict[str, Any]]:
        return self.cta_backtester.optimize(
            request,
            fast_values=fast_values,
            slow_values=slow_values,
            target=target,
        )

    def risk_manager_status(self) -> dict[str, Any]:
        return self.risk_manager.snapshot()

    def update_risk_manager_config(self, **patch: Any) -> dict[str, Any]:
        snapshot = self.risk_manager.update_config(**patch)
        self._upsert_risk_manager_setting(snapshot["config"])
        return snapshot

    def restore_risk_manager_config(self) -> dict[str, Any]:
        if not self.session_factory:
            return self.risk_manager.snapshot()
        session = self.session_factory()
        try:
            row = session.get(RiskManagerSetting, "default")
            config = dict(row.config_json or {}) if row else {}
        finally:
            session.close()
        if config:
            self.risk_manager.update_config(**_risk_config_from_json(config))
        return self.risk_manager.snapshot()

    def _upsert_risk_manager_setting(self, config: dict[str, Any]) -> None:
        if not self.session_factory:
            return
        session = self.session_factory()
        try:
            row = session.get(RiskManagerSetting, "default")
            if not row:
                row = RiskManagerSetting(id="default")
                session.add(row)
            row.config_json = _risk_config_to_json(config)
            session.commit()
        finally:
            session.close()

    def evaluate_risk_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        strategy_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        return self.risk_manager.evaluate_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            strategy_id=strategy_id,
            now=now,
        )

    def record_cancel(self, symbol: str) -> dict[str, Any]:
        return self.risk_manager.record_cancel(symbol)

    def start_twap(self, payload: Any) -> dict[str, Any]:
        setting = TwapAlgoSetting(
            symbol=payload.symbol,
            direction=Direction(payload.direction),
            total_volume=payload.total_volume,
            limit_price=payload.limit_price,
            gateway_name=payload.gateway_name or TigerOpenGateway.default_name,
            exchange=Exchange(payload.exchange),
            product=Product(payload.product),
            order_type=payload.order_type,
            interval_ticks=payload.interval_ticks,
            slices=payload.slices,
            clip_volume=payload.clip_volume,
            reference=payload.reference,
        )
        return self.algo_engine.start_twap(setting).snapshot()

    def start_iceberg(self, payload: Any) -> dict[str, Any]:
        setting = IcebergAlgoSetting(
            symbol=payload.symbol,
            direction=Direction(payload.direction),
            total_volume=payload.total_volume,
            display_volume=payload.display_volume,
            limit_price=payload.limit_price,
            gateway_name=payload.gateway_name or TigerOpenGateway.default_name,
            exchange=Exchange(payload.exchange),
            product=Product(payload.product),
            order_type=payload.order_type,
            interval_ticks=payload.interval_ticks,
            reference=payload.reference,
        )
        return self.algo_engine.start_iceberg(setting).snapshot()

    def pause_algo(self, algoid: str) -> dict[str, Any]:
        return self.algo_engine.pause_algo(algoid).snapshot()

    def resume_algo(self, algoid: str) -> dict[str, Any]:
        return self.algo_engine.resume_algo(algoid).snapshot()

    def stop_algo(self, algoid: str) -> dict[str, Any]:
        return self.algo_engine.stop_algo(algoid).snapshot()

    def build_spread_legs(self, rows: list[Any]) -> list[SpreadLeg]:
        legs: list[SpreadLeg] = []
        for row in rows:
            product = Product(row.product)
            if product == Product.OPTION:
                legs.append(
                    make_option_leg(
                        row.symbol,
                        price_multiplier=row.price_multiplier,
                        trading_multiplier=row.trading_multiplier,
                        variable_name=row.variable_name,
                    )
                )
            elif product in {Product.EQUITY, Product.ETF}:
                leg = make_stock_leg(
                    row.symbol,
                    price_multiplier=row.price_multiplier,
                    trading_multiplier=row.trading_multiplier,
                    variable_name=row.variable_name,
                )
                if product == Product.ETF:
                    leg = SpreadLeg(
                        symbol=leg.symbol,
                        exchange=leg.exchange,
                        product=Product.ETF,
                        price_multiplier=leg.price_multiplier,
                        trading_multiplier=leg.trading_multiplier,
                        variable_name=leg.variable_name,
                    )
                legs.append(leg)
            else:
                raise ValueError(f"unsupported spread leg product: {row.product}")
        return legs

    def send_spread_order(self, spread_name: str, payload: Any) -> dict[str, Any]:
        orderids = self.spread_engine.send_spread_order(
            spread_name,
            direction=Direction(payload.direction),
            volume=payload.volume,
            gateway_name=payload.gateway_name or TigerOpenGateway.default_name,
            price_offset=payload.price_offset,
            reference=payload.reference,
        )
        spread = self.spread_engine.get_spread(spread_name)
        return {
            "spread_name": spread_name.strip().upper(),
            "orderids": orderids,
            "spread": spread.snapshot() if spread else None,
        }


def _risk_config_to_json(config: dict[str, Any]) -> dict[str, Any]:
    payload = dict(config)
    sessions: list[dict[str, str]] = []
    for item in payload.get("allowed_sessions") or []:
        if isinstance(item, dict):
            start = item.get("start")
            end = item.get("end")
        else:
            start, end = item
        sessions.append({"start": _risk_time_to_string(start), "end": _risk_time_to_string(end)})
    payload["allowed_sessions"] = sessions
    return payload


def _risk_config_from_json(config: dict[str, Any]) -> dict[str, Any]:
    payload = dict(config)
    sessions: list[tuple[time, time]] = []
    for item in payload.get("allowed_sessions") or []:
        if isinstance(item, dict):
            start = item.get("start")
            end = item.get("end")
        else:
            start, end = item
        sessions.append((_risk_time_from_value(start), _risk_time_from_value(end)))
    payload["allowed_sessions"] = tuple(sessions)
    return payload


def _risk_time_to_string(value: Any) -> str:
    if isinstance(value, time):
        return value.isoformat(timespec="minutes")
    return str(value)


def _risk_time_from_value(value: Any) -> time:
    if isinstance(value, time):
        return value
    return time.fromisoformat(str(value))

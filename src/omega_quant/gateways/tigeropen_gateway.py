"""TigerOpen gateway for Omega's vn.py-style MainEngine."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..broker.order_streamer import OrderEvent, OrderEventQueue, subscribe_tiger_push
from ..broker.tigeropen_client import build_contract, build_push_client, build_trade_client, configured_tiger_account, submit_via_tiger
from ..broker.safety import require_live_gateway_armed
from ..core.event import EVENT_TIMER, Event, EventEngine
from ..core.gateway import AccountGateway, BaseGateway, ContractGateway, GatewayCapability, MarketDataGateway, TradingGateway
from ..core.objects import (
    AccountData,
    CancelRequest,
    ContractData,
    Direction,
    Exchange,
    OrderData,
    OrderRequest,
    PositionData,
    Product,
    Status,
    SubscribeRequest,
    TradeData,
)


class TigerOpenGateway(BaseGateway, TradingGateway, AccountGateway, ContractGateway, MarketDataGateway):
    """US stock/option gateway using tigerfintech/openapi-python-sdk."""

    default_name = "TIGEROPEN"
    default_setting = {
        "account": "from_config",
        "market": "US",
        "products": "stocks,options",
    }
    exchanges = [Exchange.SMART.value, Exchange.NASDAQ.value, Exchange.NYSE.value, Exchange.ARCA.value, Exchange.CBOE.value]

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)
        self.client: Any | None = None
        self.connected = False
        self._order_count = 0
        self.subscribed_symbols: set[str] = set()
        self._local_to_broker_orderids: dict[str, str] = {}
        self.order_event_queue = OrderEventQueue()
        self.push_client: Any | None = None
        self._push_timer_registered = False
        self.push_connected = False
        self.push_error: str | None = None
        self._next_order_options: dict[str, Any] = {}

    def set_next_order_options(
        self,
        *,
        outside_rth: bool = False,
        time_in_force: str = "DAY",
    ) -> None:
        """Set per-call overrides consumed by the very next send_order on this gateway."""

        self._next_order_options = {
            "outside_rth": bool(outside_rth),
            "time_in_force": str(time_in_force or "DAY").upper(),
        }

    @property
    def capabilities(self) -> set[GatewayCapability]:
        return {
            GatewayCapability.TRADING,
            GatewayCapability.ACCOUNT,
            GatewayCapability.CONTRACT,
            GatewayCapability.MARKET_DATA,
        }

    def status(self) -> dict[str, Any]:
        payload = super().status()
        payload.update(
            {
                "connected": self.connected,
                "trade_client_initialized": self.client is not None,
                "push_connected": self.push_connected,
                "push_error": self.push_error,
                "subscribed_symbols": sorted(self.subscribed_symbols),
            }
        )
        return payload

    def connect(self, setting: dict) -> None:
        # Register the gateway without reading credentials or constructing the
        # Tiger SDK client. Broker I/O is initialized lazily after an explicit
        # query or, for writes, after require_live_gateway_armed() has passed.
        self.client = setting.get("trade_client") or self.client
        self.push_client = setting.get("push_client")
        if self.push_client is None and setting.get("enable_push"):
            try:
                self.push_client = build_push_client(connect=True)
                self.push_connected = True
                self.push_error = None
            except Exception as exc:  # noqa: BLE001
                self.push_client = None
                self.push_connected = False
                self.push_error = repr(exc)
                self.write_log(f"TigerOpen push connect failed: {exc!r}", level="ERROR")
        if self.push_client is not None:
            subscribe_tiger_push(self.push_client, self.order_event_queue, account=setting.get("account") or configured_tiger_account())
            self.push_connected = True
            if not self._push_timer_registered:
                self.event_engine.register(EVENT_TIMER, self._on_timer)
                self._push_timer_registered = True
        self.connected = True
        self.write_log("TigerOpen gateway registered; SDK client remains lazy.")

    def close(self) -> None:
        if self._push_timer_registered:
            self.event_engine.unregister(EVENT_TIMER, self._on_timer)
            self._push_timer_registered = False
        if self.push_client is not None and hasattr(self.push_client, "disconnect"):
            try:
                self.push_client.disconnect()
            except Exception as exc:  # noqa: BLE001
                self.write_log(f"TigerOpen push disconnect failed: {exc!r}", level="ERROR")
        self.connected = False
        self.client = None
        self.push_client = None
        self.push_connected = False
        self.write_log("TigerOpen gateway closed.")

    def subscribe(self, req: SubscribeRequest) -> None:
        self.subscribed_symbols.add(req.symbol.upper())
        self.write_log(f"Subscribed readonly market data: {req.symbol.upper()}.")

    def send_order(self, req: OrderRequest) -> str:
        require_live_gateway_armed()
        self._ensure_connected()
        self._order_count += 1
        local_orderid = f"TG{self._order_count:08d}"
        order = OrderData(
            symbol=req.symbol.upper(),
            exchange=req.exchange,
            orderid=local_orderid,
            direction=req.direction,
            volume=req.volume,
            price=req.price,
            status=Status.SUBMITTING,
            datetime=datetime.now(timezone.utc),
            gateway_name=self.gateway_name,
            reference=req.reference,
        )
        self.on_order(order)

        overrides = self._next_order_options
        self._next_order_options = {}
        try:
            action = self._request_to_action(req, local_orderid)
            tif = str(overrides.get("time_in_force") or action.get("time_in_force") or "DAY").upper()
            outside_rth = bool(overrides.get("outside_rth", False))
            contract = build_contract(self.client, action)
            result = submit_via_tiger(
                self.client,
                contract=contract,
                side=action["action"],
                order_type=action["order_type"],
                quantity=action["quantity"],
                limit_price=action["limit_price"],
                time_in_force=tif,
                outside_rth=outside_rth,
            )
        except Exception as exc:
            rejected = OrderData(**{**order.__dict__, "status": Status.REJECTED})
            self.on_order(rejected)
            self.write_log(f"TigerOpen order rejected before submit: {exc}", level="ERROR")
            return rejected.vt_orderid

        result_status = str(result.get("status") or "")
        if result_status == "preview_blocked":
            rejected = OrderData(
                **{
                    **order.__dict__,
                    "status": Status.REJECTED,
                    "reference": str(result.get("message") or order.reference),
                }
            )
            self.on_order(rejected)
            self.write_log(f"TigerOpen preview blocked order: {result.get('message')}", level="ERROR")
            return rejected.vt_orderid

        if result_status == "preview_only":
            preview_only = OrderData(
                **{
                    **order.__dict__,
                    "status": Status.REJECTED,
                    "reference": str(result.get("message") or order.reference),
                }
            )
            self.on_order(preview_only)
            self.write_log(f"TigerOpen preview-only order not submitted: {result.get('message')}", level="WARNING")
            return preview_only.vt_orderid

        broker_order_id = str(result.get("broker_order_id") or "")
        if broker_order_id:
            self._local_to_broker_orderids[local_orderid] = broker_order_id
        submitted = OrderData(
            **{
                **order.__dict__,
                "status": Status.NOTTRADED,
                "broker_order_id": broker_order_id,
            }
        )
        self.on_order(submitted)
        return submitted.vt_orderid

    def cancel_order(self, req: CancelRequest) -> None:
        require_live_gateway_armed()
        self._ensure_connected()
        if not req.orderid:
            self.write_log("Cancel ignored: empty order id.", level="WARNING")
            return
        broker_order_id = self._local_to_broker_orderids.get(req.orderid) or req.orderid
        order_id = int(broker_order_id) if str(broker_order_id).isdigit() else broker_order_id
        response = self.client.cancel_order(account=configured_tiger_account(), id=order_id)
        self.write_log(f"TigerOpen cancel requested: {response}")

    def query_account(self) -> None:
        self._ensure_connected()
        rows = self._call_first_available("get_prime_assets", "get_assets", "get_account_assets", "get_account")
        emitted = 0
        for row in _iter_rows(rows):
            emitted += int(self._emit_account_from_mapping(_to_mapping(row)))
        if emitted == 0:
            account = getattr(getattr(self.client, "client_config", None), "account", None) or configured_tiger_account() or "configured"
            self.on_account(AccountData(accountid=str(account), balance=0.0, gateway_name=self.gateway_name))

    def query_position(self) -> None:
        self._ensure_connected()
        rows = self._call_first_available("get_positions", "get_position")
        emitted = 0
        for row in _iter_rows(rows):
            emitted += int(self._emit_position_from_mapping(_to_mapping(row)))
        self.write_log(f"TigerOpen position query emitted {emitted} positions.")

    def query_order(self) -> None:
        self._ensure_connected()
        rows = self._call_first_available("get_orders", "get_open_orders", "get_order")
        emitted = 0
        for row in _iter_rows(rows):
            emitted += int(self._emit_order_from_mapping(_to_mapping(row)))
        self.write_log(f"TigerOpen order query emitted {emitted} orders.")

    def query_contract(self, symbol: str, *, product: str | Product | None = None) -> None:
        self._ensure_connected()
        product_value = product.value if isinstance(product, Product) else str(product or "").lower()
        symbol = symbol.strip().upper()
        if not product_value:
            product_value = Product.OPTION.value if symbol.startswith("O:") else Product.EQUITY.value

        try:
            contract = self._fetch_contract(symbol, product_value)
        except Exception as exc:  # noqa: BLE001
            self.write_log(f"TigerOpen contract query failed: {symbol}: {exc!r}", level="ERROR")
            return

        if contract is not None:
            self.on_contract(contract)

    def process_push_events(self, *, limit: int = 100) -> int:
        processed = 0
        while processed < limit:
            event = self.order_event_queue.get(timeout=0)
            if event is None:
                break
            self.process_push_event(event)
            processed += 1
        return processed

    def process_push_event(self, event: OrderEvent) -> None:
        if event.kind == "asset":
            self._emit_account_from_mapping(event.payload)
        elif event.kind == "position":
            self._emit_position_from_mapping(event.payload)
        elif event.kind == "order":
            self._emit_order_from_mapping(event.payload)
        elif event.kind == "transaction":
            self._emit_trade_from_mapping(event.payload)
            self._emit_order_from_mapping(event.payload)
        else:
            self.write_log(f"TigerOpen push ignored: {event.kind}")

    def _on_timer(self, event: Event) -> None:
        if self.push_client is None:
            return
        processed = self.process_push_events(limit=500)
        if processed:
            self.write_log(f"TigerOpen push processed {processed} events.")

    def _emit_account_from_mapping(self, item: dict[str, Any]) -> bool:
        item = _normalized_account_mapping(item)
        account = _first(item, "account", "account_id", "accountId", "id") or configured_tiger_account() or "configured"
        balance = _as_float(
            _first(
                item,
                "net_liquidation",
                "net_liquidation_value",
                "netLiquidation",
                "total_assets",
                "total_asset",
                "portfolio_value",
                "balance",
            ),
            default=0.0,
        )
        cash = _as_optional_float(_first(item, "cash_available_for_trade", "available_funds", "cash", "cash_balance", "available"))
        buying_power = _as_optional_float(_first(item, "buying_power", "buyingPower"))
        locked_funds = _as_optional_float(_first(item, "locked_funds", "frozen", "margin_held"))
        frozen = locked_funds if locked_funds is not None else max(balance - cash, 0.0) if cash is not None else 0.0
        self.on_account(
            AccountData(
                accountid=str(account),
                balance=balance,
                frozen=frozen,
                cash=cash,
                buying_power=buying_power,
                gateway_name=self.gateway_name,
            )
        )
        return True

    def _emit_position_from_mapping(self, item: dict[str, Any]) -> bool:
        symbol = str(_first(item, "symbol", "contract_symbol", "identifier", "name") or "").upper()
        if not symbol:
            contract = _to_mapping(_first(item, "contract"))
            symbol = str(_first(contract, "symbol", "identifier") or "").upper()
        if not symbol:
            return False
        quantity = _as_float(_first(item, "quantity", "position", "volume", "qty"), default=0.0)
        direction = Direction.LONG if quantity >= 0 else Direction.SHORT
        price = _as_float(_first(item, "average_cost", "average_price", "avg_cost", "cost_price", "market_price"), default=0.0)
        pnl = _as_float(_first(item, "unrealized_pnl", "unrealizedPnl", "unrealized_pl", "pnl"), default=0.0)
        self.on_position(
            PositionData(
                symbol=symbol,
                exchange=Exchange.SMART,
                direction=direction,
                volume=abs(quantity),
                price=price,
                pnl=pnl,
                gateway_name=self.gateway_name,
            )
        )
        return True

    def _emit_order_from_mapping(self, item: dict[str, Any]) -> bool:
        symbol = str(_first(item, "symbol", "contract_symbol", "identifier") or "").upper()
        if not symbol:
            contract = _to_mapping(_first(item, "contract"))
            symbol = str(_first(contract, "symbol", "identifier") or "").upper()
        if not symbol:
            return False
        orderid = str(_first(item, "id", "order_id", "orderId", "broker_order_id") or "")
        if not orderid:
            return False
        side = str(_first(item, "action", "side", "direction") or "").upper()
        status_text = str(_first(item, "status", "order_status") or "")
        self.on_order(
            OrderData(
                symbol=symbol,
                exchange=Exchange.SMART,
                orderid=orderid,
                direction=Direction.SHORT if side in {"SELL", "SHORT"} else Direction.LONG,
                volume=_as_float(_first(item, "quantity", "total_quantity", "volume", "qty"), default=0.0),
                price=_as_optional_float(_first(item, "limit_price", "price", "avg_fill_price", "fill_price")),
                traded=_as_float(_first(item, "filled", "filled_quantity", "traded", "quantity_filled"), default=0.0),
                status=_status_from_tiger(status_text),
                datetime=_as_datetime(_first(item, "created_at", "order_time", "time", "timestamp")),
                gateway_name=self.gateway_name,
                broker_order_id=orderid,
                reference=str(_first(item, "reference", "remark") or ""),
            )
        )
        return True

    def _emit_trade_from_mapping(self, item: dict[str, Any]) -> bool:
        symbol = str(_first(item, "symbol", "contract_symbol", "identifier") or "").upper()
        if not symbol:
            contract = _to_mapping(_first(item, "contract"))
            symbol = str(_first(contract, "symbol", "identifier") or "").upper()
        if not symbol:
            return False
        tradeid = str(_first(item, "trade_id", "tradeId", "transaction_id", "transactionId", "id", "exec_id", "execution_id") or "")
        if not tradeid:
            return False
        orderid = str(_first(item, "order_id", "orderId", "broker_order_id", "orderid") or "")
        side = str(_first(item, "action", "side", "direction") or "").upper()
        price = _as_optional_float(_first(item, "price", "fill_price", "avg_fill_price", "filled_price", "trade_price"))
        volume = _as_float(_first(item, "quantity", "filled_quantity", "filled", "volume", "qty", "shares"), default=0.0)
        if price is None or volume <= 0:
            return False
        self.on_trade(
            TradeData(
                symbol=symbol,
                exchange=Exchange.SMART,
                tradeid=tradeid,
                orderid=orderid or tradeid,
                direction=Direction.SHORT if side in {"SELL", "SHORT"} else Direction.LONG,
                volume=volume,
                price=price,
                datetime=_as_datetime(_first(item, "created_at", "filled_at", "time", "timestamp", "trade_time")) or datetime.now(timezone.utc),
                gateway_name=self.gateway_name,
            )
        )
        return True

    def _fetch_contract(self, symbol: str, product_value: str) -> ContractData | None:
        from tigeropen.common.consts import Currency, SecurityType

        if product_value == Product.OPTION.value:
            from ..core.option_master import parse_us_option_contract

            parsed = parse_us_option_contract(symbol)
            sdk_contract = self.client.get_contract(
                parsed.underlying,
                SecurityType.OPT,
                currency=Currency.USD,
                expiry=parsed.expiry,
                strike=parsed.strike,
                put_call=parsed.put_call,
            )
            return ContractData(
                symbol=parsed.canonical_symbol,
                exchange=Exchange.SMART,
                name=str(_first(_to_mapping(sdk_contract), "name", "local_symbol", "symbol") or parsed.canonical_symbol),
                product=Product.OPTION,
                size=_as_float(_first(_to_mapping(sdk_contract), "multiplier", "contract_multiplier", "size"), default=100.0),
                pricetick=_as_float(_first(_to_mapping(sdk_contract), "min_tick", "price_tick", "pricetick"), default=0.01),
                currency="USD",
                gateway_name=self.gateway_name,
            )

        sec_type = SecurityType.STK
        sdk_contract = self.client.get_contract(symbol, sec_type, currency=Currency.USD)
        item = _to_mapping(sdk_contract)
        product = Product.ETF if str(_first(item, "sec_type", "product", "asset_type") or "").upper() == "ETF" else Product.EQUITY
        return ContractData(
            symbol=str(_first(item, "symbol", "identifier") or symbol).upper(),
            exchange=Exchange.SMART,
            name=str(_first(item, "name", "local_symbol", "symbol") or symbol),
            product=product,
            size=_as_float(_first(item, "multiplier", "lot_size", "size"), default=1.0),
            pricetick=_as_float(_first(item, "min_tick", "price_tick", "pricetick"), default=0.01),
            currency=str(_first(item, "currency") or "USD").upper(),
            gateway_name=self.gateway_name,
        )

    def _call_first_available(self, *method_names: str) -> Any:
        for method_name in method_names:
            method = getattr(self.client, method_name, None)
            if not callable(method):
                continue
            try:
                return method()
            except TypeError:
                account = configured_tiger_account()
                if account:
                    try:
                        return method(account)
                    except TypeError:
                        continue
            except Exception as exc:  # noqa: BLE001
                self.write_log(f"TigerOpen {method_name} failed: {exc!r}", level="ERROR")
                return None
        return None

    def _ensure_connected(self) -> None:
        if not self.connected:
            self.connect({})
        if self.client is None:
            self.client = build_trade_client()

    def _request_to_action(self, req: OrderRequest, orderid: str) -> dict[str, Any]:
        if req.product == Product.OPTION:
            sec_type = "OPT"
            asset_class = "us_option"
        else:
            sec_type = "STK"
            asset_class = "us_stock"
        return {
            "id": orderid,
            "action": "BUY" if req.direction == Direction.LONG else "SELL",
            "symbol": req.symbol.upper(),
            "sec_type": sec_type,
            "quantity": float(req.volume),
            "order_type": req.order_type,
            "limit_price": float(req.price or 0.0),
            "time_in_force": "DAY",
            "approval_required": True,
            "contract": {
                "symbol": req.symbol.upper(),
                "currency": req.currency,
                "display": req.symbol.upper(),
            },
            "asset_class": asset_class,
            "reference": req.reference,
        }


def _to_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    data: dict[str, Any] = {}
    for name in dir(value):
        if name.startswith("_"):
            continue
        try:
            item = getattr(value, name)
        except Exception:
            continue
        if callable(item):
            continue
        data[name] = item
    return data


def _normalized_account_mapping(item: dict[str, Any]) -> dict[str, Any]:
    """Flatten TigerOpen PortfolioAccount/segment objects into account fields.

    TigerOpen returns different shapes for ``get_prime_assets`` and
    ``get_assets``. The useful US securities balances are usually nested under
    ``segments['S']`` and, for ``get_assets``, high-level totals are under
    ``summary``. Older fake clients in tests may already return flat dicts.
    """

    summary = _to_mapping(_first(item, "summary"))
    segments_raw = _first(item, "segments") or {}
    segment_s: dict[str, Any] = {}
    if isinstance(segments_raw, dict):
        segment_s = _to_mapping(segments_raw.get("S") or segments_raw.get("SEC") or segments_raw.get("US Securities"))
    else:
        segments = _to_mapping(segments_raw)
        segment_s = _to_mapping(_first(segments, "S", "SEC", "US Securities"))

    merged: dict[str, Any] = {}
    merged.update(item)
    # Segment values include the exact securities cash/locked funds. Summary
    # values include portfolio-level net liquidation/buying power. Prefer
    # explicit flat values last so test doubles and adapters can override.
    for source in (segment_s, summary, item):
        for key, value in source.items():
            if key.startswith("_"):
                continue
            if value is not None:
                merged[key] = value

    account = _first(item, "account", "account_id", "accountId", "id") or _first(summary, "account") or _first(segment_s, "account")
    if account:
        merged["account"] = account
    return merged


def _iter_rows(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        for key in ("items", "data", "positions", "orders", "assets", "accounts"):
            nested = value.get(key)
            if isinstance(nested, list | tuple):
                return list(nested)
        return [value]
    if isinstance(value, list | tuple | set):
        return list(value)
    return [value]


def _first(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def _as_float(value: Any, *, default: float) -> float:
    parsed = _as_optional_float(value)
    return default if parsed is None else parsed


def _as_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        raw = float(value)
        if raw > 10_000_000_000:
            raw = raw / 1000.0
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _status_from_tiger(value: str) -> Status:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if "." in normalized:
        normalized = normalized.rsplit(".", 1)[-1]
    if normalized.startswith("orderstatus_"):
        normalized = normalized[len("orderstatus_"):]
    if normalized in {"filled", "all_traded", "executed"}:
        return Status.ALLTRADED
    if normalized in {"part_filled", "partial_filled", "partial_traded", "partially_filled"}:
        return Status.PARTTRADED
    if normalized in {"cancelled", "canceled", "cancelling", "canceling", "expired"}:
        return Status.CANCELLED
    if normalized in {"rejected", "inactive", "invalid"}:
        return Status.REJECTED
    if normalized in {"submitted", "new", "open", "not_traded", "pending_submit"}:
        return Status.NOTTRADED
    if normalized in {"submitting", "pending"}:
        return Status.SUBMITTING
    return Status.NOTTRADED

from __future__ import annotations

import hmac
import os
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .database import build_engine, build_session_factory, init_db
from .config import Settings
from .execution_control import ExecutionControlController
from .adapters.account_context import (
    get_portfolio_research_report_readonly,
    get_real_account_risk_readonly,
    get_real_account_snapshot,
    get_real_orders_readonly,
    get_real_position_groups_readonly,
    get_real_positions,
    get_real_stream_detail_readonly,
    get_real_stream_status_readonly,
)
from .adapters.platform_context import platform_audit_readonly, platform_capabilities
from .market_data_modes import MarketDataModeController
from .market_data_modes import MarketDataUnavailable
from .models import MonitorGroup
from .core.cta_backtester import CtaBacktestRequest
from .core.objects import Direction, Exchange, Product
from .core.runtime import OmegaCoreRuntime
from .gateways import TigerOpenGateway
from .schemas import (
    AccountModeRead,
    AlgoIcebergStartRequest,
    AlgoTwapStartRequest,
    BrokerStreamStatusRead,
    BarCsvImportRequest,
    CorePositionUpdate,
    CoreTickUpdate,
    CtaLiveStrategyCreateRequest,
    DashboardSummary,
    CtaBacktestOptimizeRequest,
    CtaBacktestRunRequest,
    CtaMonteCarloRequest,
    CtaOptimizeGenericRequest,
    CtaWalkForwardRequest,
    ManualOrderRequest,
    DataModeRead,
    DataModeUpdate,
    DataRecordingRequest,
    ExecutionArmRequest,
    ExecutionControlRead,
    ExecutionControlUpdate,
    LiveMarketChannelUpdate,
    LiveMarketStatusRead,
    LiveMarketSubscribeRequest,
    MarketEventRead,
    MonitorGroupCreate,
    MonitorGroupCtaStrategyRequest,
    MonitorGroupRead,
    MonitorGroupUpdate,
    OptionContractRequest,
    OptionPortfolioConfigureRequest,
    OptionReadonlySyncRequest,
    OptionPositionUpdateRequest,
    OptionQuoteUpdateRequest,
    OptionUnderlyingPriceRequest,
    RealAccountSummary,
    RealAccountRiskRead,
    RealOrderReadonlyRead,
    RealPositionGroupRead,
    RealPositionRead,
    RealStreamDetailRead,
    RealStreamStatusRead,
    RiskManagerCancelRecordRequest,
    RiskManagerConfigUpdateRequest,
    RiskManagerEvaluateRequest,
    SpreadCreateRequest,
    SpreadOrderRequest,
    RecordedBarCreate,
    TigerAccountSnapshotRead,
    TigerHistoryDownloadRequest,
    TickBarAggregationRequest,
)
from .monitor_groups import MonitorGroupService


def create_app(
    database_url: str | None = None,
    *,
    enable_tiger_push: bool = False,
    enable_data_recorder_live_subscription: bool = False,
    market_data_controller: MarketDataModeController | None = None,
) -> FastAPI:
    demo_mode = os.environ.get("OMEGA_DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
    engine = build_engine(database_url)
    init_db(engine)
    session_factory = build_session_factory(engine)
    market_data_controller = market_data_controller or MarketDataModeController()
    execution_control = ExecutionControlController()
    from .broker.safety import register_execution_controller

    register_execution_controller(execution_control)
    core_runtime = OmegaCoreRuntime(
        session_factory=session_factory,
        enable_tiger_push=enable_tiger_push,
        enable_tiger_gateway=not demo_mode,
        market_data_adapter=market_data_controller.live_adapter,
    )

    def seed_defaults() -> None:
        session = session_factory()
        try:
            MonitorGroupService(session).seed_default_monitor_group()
        finally:
            session.close()

    def load_user_strategies() -> None:
        from .core.strategy_loader import ensure_sample_strategy_exists, reload_all_strategy_files

        ensure_sample_strategy_exists()
        reload_all_strategy_files()

    def auto_subscribe_live_market() -> None:
        # 默认不订阅；前端 MarketWatchPage 会按 localStorage 自行订阅。
        # 若部署需要 headless 自动订阅，设 OMEGA_LIVE_DEFAULT_SYMBOLS=AAPL,NVDA,... 即可。
        raw_symbols = os.environ.get("OMEGA_LIVE_DEFAULT_SYMBOLS", "").strip()
        if not raw_symbols:
            return
        symbols = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()]
        if not symbols:
            return
        try:
            include_trades = os.environ.get("OMEGA_LIVE_SUBSCRIBE_TRADES", "0").strip().lower() not in {"0", "false", "no", "off"}
            include_quotes = os.environ.get("OMEGA_LIVE_SUBSCRIBE_QUOTES", "1").strip().lower() not in {"0", "false", "no", "off"}
            adapter = market_data_controller.live_adapter
            adapter.set_channels(subscribe_quotes=include_quotes, subscribe_trades=include_trades)
            market_data_controller.subscribe_live(symbols)
        except Exception:
            pass

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        seed_defaults()
        load_user_strategies()
        auto_subscribe_live_market()
        try:
            yield
        finally:
            core_runtime.close()

    app = FastAPI(
        title="Omega Quant Platform MVP",
        version="0.4.0",
        description="Omega Quant Platform with TigerOpen live API execution. Target account is selected by TigerOpen local configuration.",
        lifespan=lifespan,
    )
    app.state.omega_execution_control = execution_control
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_origin_regex=(
            r"^http://("
            r"localhost|127\.0\.0\.1|0\.0\.0\.0|"
            r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
            r"192\.168\.\d{1,3}\.\d{1,3}|"
            r"172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}"
            r"):\d+$"
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- API token auth (all state-changing operations outside explicit demo mode) ----
    api_token = os.environ.get("OMEGA_API_TOKEN", "").strip()
    # Read-only routes stay public on the local service. State-changing routes
    # require a header token unless OMEGA_DEMO_MODE explicitly disables broker I/O.
    public_path_prefixes = (
        "/api/health",
        "/api/core/status",
        "/api/cta-strategy/templates",
        "/api/cta-strategy/strategies",  # GET allowed; POST/DELETE/PATCH checked below
        "/api/cta-strategy/files",
        "/api/cta-backtester/strategies",
        "/api/data-manager/overview",
        "/api/data-manager/bars",
        "/api/market/snapshot",
        "/api/market/live/status",
        "/api/market/rate-limits",
        "/api/execution/control",  # GET
        "/api/data-mode",  # GET
        "/api/account/mode",
        "/api/tiger/account/",
        "/api/broker/stream/status",
        "/api/option-master/portfolios/",  # GET ones; mutate POSTs still checked
        "/api/spread-trading/spreads",  # GET ones
        "/api/risk-manager/status",
        "/api/algo/",  # GET ones
        "/api/gateways",
        "/api/monitor-groups",  # GET (POST/PATCH/DELETE still need token)
        "/api/dashboard/summary",
        "/docs",
        "/openapi.json",
        "/redoc",
    )

    @app.middleware("http")
    async def token_auth_middleware(request, call_next):  # type: ignore[no-untyped-def]
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return await call_next(request)
        path = request.url.path
        # GET-safe paths are already returned above. For state-changing methods,
        # require the token unless the path is explicitly public.
        if any(path.startswith(prefix) for prefix in ("/api/health", "/docs", "/openapi.json", "/redoc")):
            return await call_next(request)
        if not api_token:
            if demo_mode:
                return await call_next(request)
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=503,
                content={
                    "detail": {
                        "code": "OMEGA_API_TOKEN_REQUIRED",
                        "message": "Configure OMEGA_API_TOKEN before using state-changing APIs.",
                    }
                },
            )
        client_token = request.headers.get("X-Omega-Token")
        if not client_token or not hmac.compare_digest(client_token, api_token):
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=401,
                content={
                    "detail": {
                        "code": "OMEGA_API_TOKEN_INVALID",
                        "message": "A valid X-Omega-Token header is required for state-changing APIs.",
                    }
                },
            )
        return await call_next(request)

    def get_session() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def monitor_group_service(session: Session = Depends(get_session)) -> MonitorGroupService:
        return MonitorGroupService(session)

    core_readonly_cache: dict[str, Any] = {"expires_at": 0.0, "snapshot": None}

    def core_readonly_snapshot() -> dict[str, Any]:
        """Read the current TigerOpen simulated account through Omega's gateway.

        This keeps the Real Account Readonly view aligned with the account that
        the live TigerOpen execution path actually targets. Omega cache is
        only used as a fallback when the TigerOpen query fails.
        """

        now = time.monotonic()
        cached = core_readonly_cache.get("snapshot")
        if isinstance(cached, dict) and now < float(core_readonly_cache.get("expires_at") or 0.0):
            return cached
        try:
            snapshot = core_runtime.query_tigeropen_snapshot()
        except Exception as exc:  # noqa: BLE001
            snapshot = {"_omega_core_error": str(exc), "accounts": [], "positions": [], "orders": []}
        core_readonly_cache["snapshot"] = snapshot
        core_readonly_cache["expires_at"] = now + 5.0
        return snapshot

    def core_readonly_available(snapshot: dict[str, Any]) -> bool:
        # A MainEngine OMS order alone does not prove that the TigerOpen account
        # snapshot is available. Local/risk-rejected OMS orders must not make the
        # Tiger account UI look connected or pollute broker order history.
        return bool(snapshot.get("accounts") or snapshot.get("positions"))

    def _broker_submitted_orders(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        return [order for order in list(snapshot.get("orders") or []) if order.get("broker_order_id")]

    def core_readonly_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
        if not core_readonly_available(snapshot):
            fallback = get_real_account_snapshot()
            error = snapshot.get("_omega_core_error")
            if error:
                fallback["errors"] = [*list(fallback.get("errors") or []), f"Omega TigerOpen query failed: {error}"]
            return fallback
        accounts = list(snapshot.get("accounts") or [])
        positions = list(snapshot.get("positions") or [])
        orders = _broker_submitted_orders(snapshot)
        account = accounts[0] if accounts else {}
        balance = _float_or_none(account.get("balance"))
        frozen = _float_or_none(account.get("frozen")) or 0.0
        available = _float_or_none(account.get("available"))
        cash = _float_or_none(account.get("cash"))
        buying_power = _float_or_none(account.get("buying_power"))
        return {
            "account_view": "real_readonly",
            "execution_mode": "live",
            "live_trading": "enabled",
            "available": True,
            "read_only": True,
            "source": "omega_tigeropen_gateway",
            "broker_source": "TigerOpen SDK / Omega MainEngine OMS",
            "account": account.get("accountid"),
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "real_account_equity": balance,
            "net_liquidation": balance,
            "real_cash": cash if cash is not None else available,
            "buying_power": buying_power if buying_power is not None else available,
            "positions_count": len(positions),
            "orders_count": len(orders),
            "errors": [],
            "message": "数据来自当前 TigerOpen 配置账户。",
        }

    def core_readonly_positions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        if not core_readonly_available(snapshot):
            return get_real_positions()
        account = _snapshot_account_id(snapshot)
        rows: list[dict[str, Any]] = []
        for position in list(snapshot.get("positions") or []):
            symbol = str(position.get("symbol") or "").upper()
            direction = str(position.get("direction") or "long").lower()
            volume = _float_or_none(position.get("volume")) or 0.0
            signed_qty = -volume if direction == "short" else volume
            price = _float_or_none(position.get("price"))
            multiplier = 100.0 if symbol.startswith("O:") else 1.0
            market_value = signed_qty * (price or 0.0) * multiplier
            option_details = _option_details_from_symbol(symbol) if symbol.startswith("O:") else {}
            rows.append(
                {
                    "account": account,
                    "sec_type": "OPT" if symbol.startswith("O:") else "STK",
                    "symbol": symbol,
                    "display": symbol,
                    "currency": "USD",
                    "quantity": signed_qty,
                    "average_cost": price,
                    "market_price": price,
                    "market_value": market_value,
                    "unrealized_pnl": _float_or_none(position.get("pnl")),
                    "today_pnl": None,
                    "canonical_option_symbol": option_details.get("canonical_option_symbol"),
                    "option_underlying": option_details.get("option_underlying"),
                    "option_expiry": option_details.get("option_expiry"),
                    "option_put_call": option_details.get("option_put_call"),
                    "option_strike": option_details.get("option_strike"),
                    "days_to_expiry": option_details.get("days_to_expiry"),
                    "source": "omega_tigeropen_gateway",
                    "readonly_badge": "readonly_tigeropen_simulated_position",
                    "raw_context": position,
                }
            )
        return rows

    def core_readonly_orders(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        if not core_readonly_available(snapshot):
            return get_real_orders_readonly()
        rows: list[dict[str, Any]] = []
        for order in _broker_submitted_orders(snapshot):
            direction = str(order.get("direction") or "").lower()
            rows.append(
                {
                    "broker_order_id": str(order.get("broker_order_id") or order.get("orderid") or order.get("vt_orderid") or ""),
                    "symbol": order.get("symbol"),
                    "side": "buy" if direction in {"long", "buy"} else "sell" if direction in {"short", "sell"} else direction or None,
                    "quantity": _float_or_none(order.get("volume")),
                    "status": order.get("status"),
                    "submitted_at": order.get("datetime"),
                    "filled_at": order.get("datetime") if str(order.get("status") or "").lower() in {"filled", "all_traded"} else None,
                    "source": "omega_tigeropen_gateway",
                    "readonly_badge": "readonly_tigeropen_broker_order",
                    "raw_context": order,
                }
            )
        return rows

    def core_readonly_position_groups(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        positions = core_readonly_positions(snapshot)
        if not core_readonly_available(snapshot):
            return get_real_position_groups_readonly()
        groups: dict[str, dict[str, Any]] = {}

        def group_for(key: str) -> dict[str, Any]:
            if key not in groups:
                groups[key] = {
                    "underlying": key,
                    "stock_positions": [],
                    "option_positions": [],
                    "standalone_positions": [],
                    "total_market_value": 0.0,
                    "total_unrealized_pnl": 0.0,
                    "position_count": 0,
                    "readonly_badge": "readonly_tigeropen_simulated_group",
                    "option_greeks_exposure": {},
                }
            return groups[key]

        for position in positions:
            symbol = str(position.get("symbol") or "").upper()
            sec_type = str(position.get("sec_type") or "").upper()
            key = _option_underlying_from_symbol(symbol) if sec_type == "OPT" else symbol or "--"
            group = group_for(key or "--")
            if sec_type == "OPT":
                group["option_positions"].append(position)
            elif symbol:
                group["stock_positions"].append(position)
            else:
                group["standalone_positions"].append(position)
            group["total_market_value"] += float(position.get("market_value") or 0.0)
            group["total_unrealized_pnl"] += float(position.get("unrealized_pnl") or 0.0)
            group["position_count"] += 1
        return [groups[key] for key in sorted(groups)]

    def core_tiger_account_risk(snapshot: dict[str, Any]) -> dict[str, Any]:
        if not core_readonly_available(snapshot):
            return get_real_account_risk_readonly()
        summary = core_readonly_summary(snapshot)
        positions = core_readonly_positions(snapshot)
        net_liquidation = _float_or_none(summary.get("net_liquidation"))
        cash_balance = _float_or_none(summary.get("real_cash"))
        buying_power = _float_or_none(summary.get("buying_power"))
        gross_position_value = sum(abs(float(position.get("market_value") or 0.0)) for position in positions)
        option_market_value_abs = sum(
            abs(float(position.get("market_value") or 0.0))
            for position in positions
            if str(position.get("sec_type") or "").upper() == "OPT"
        )
        option_weight = option_market_value_abs / net_liquidation if net_liquidation and net_liquidation > 0 else None
        flags: list[str] = []
        if option_weight is not None and option_weight > 0.5:
            flags.append(f"期权市值暴露 {option_weight:.2%}，超过 50%。")
        if gross_position_value and net_liquidation and gross_position_value > net_liquidation * 2:
            flags.append("总持仓名义市值超过净清算价值 2 倍。")
        return {
            "source": "omega_tigeropen_gateway",
            "read_only": False,
            "available": True,
            "account": summary.get("account"),
            "net_liquidation": net_liquidation,
            "cash_balance": cash_balance,
            "buying_power": buying_power,
            "gross_position_value": gross_position_value,
            "total_today_pl": sum(float(position.get("today_pnl") or 0.0) for position in positions),
            "total_today_pl_source": "tigeropen_positions",
            "option_market_value_abs": option_market_value_abs,
            "option_weight": option_weight,
            "option_greeks_exposure": {
                "available": any(str(position.get("sec_type") or "").upper() == "OPT" for position in positions),
                "contracts": sum(1 for position in positions if str(position.get("sec_type") or "").upper() == "OPT"),
                "total_contracts_abs": sum(
                    abs(float(position.get("quantity") or 0.0))
                    for position in positions
                    if str(position.get("sec_type") or "").upper() == "OPT"
                ),
                "delta_shares": sum(float(position.get("delta") or 0.0) * float(position.get("quantity") or 0.0) * 100.0 for position in positions),
                "gamma_shares": sum(float(position.get("gamma") or 0.0) * float(position.get("quantity") or 0.0) * 100.0 for position in positions),
                "theta": sum(float(position.get("theta") or 0.0) * float(position.get("quantity") or 0.0) for position in positions),
                "vega": sum(float(position.get("vega") or 0.0) * float(position.get("quantity") or 0.0) for position in positions),
                "weighted_iv": None,
                "sources": sorted({str(position.get("greeks_source")) for position in positions if position.get("greeks_source")}),
                "message": "基于当前 TigerOpen 持仓聚合；无期权持仓时 Greeks 为 0。",
            },
            "flags": flags,
            "positions": positions,
            "message": "基于当前 TigerOpen 账户快照计算。",
        }

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        control = execution_control.status()
        return {
            "status": "ok",
            "mode": control["effective_execution_mode"],
            "live_trading": control["live_gateway_status"],
            "message": control["message"],
        }

    @app.get("/api/dashboard/summary", response_model=DashboardSummary)
    def dashboard_summary(svc: MonitorGroupService = Depends(monitor_group_service)) -> dict[str, Any]:
        svc.seed_default_monitor_group()
        snapshot = core_readonly_snapshot()
        tiger_summary = core_readonly_summary(snapshot)
        tiger_risk = core_tiger_account_risk(snapshot)
        accounts = list(snapshot.get("accounts") or [])
        orders = list(snapshot.get("orders") or [])
        positions = list(snapshot.get("positions") or [])
        trades = list(snapshot.get("trades") or [])
        active_order_statuses = {"submitting", "not_traded", "partial_traded"}
        gross_position_value = float(tiger_risk.get("gross_position_value") or 0.0)
        total_today_pl = _float_or_none(tiger_risk.get("total_today_pl")) or 0.0
        monitor_groups = svc.list_monitor_groups()
        return {
            "source": "main_engine",
            "account": tiger_summary.get("account"),
            "execution_mode": "live",
            "broker_gateway": "tigeropen_sdk",
            "total_equity": _float_or_none(tiger_summary.get("net_liquidation")) or 0.0,
            "free_capital": _float_or_none(tiger_summary.get("real_cash")) or 0.0,
            "allocated_capital": gross_position_value,
            "used_capital": gross_position_value,
            "total_pnl": total_today_pl,
            "max_drawdown": 0.0,
            "active_monitor_groups": sum(1 for g in monitor_groups if g.status == "running"),
            "risk_warnings": list(tiger_risk.get("flags") or []),
            "monitor_groups": monitor_groups,
            "recent_orders": [],
            "positions": [],
            "oms_orders_count": len(orders),
            "oms_active_orders_count": sum(1 for o in orders if str(o.get("status") or "").lower() in active_order_statuses),
            "oms_positions_count": len(positions),
            "oms_trades_count": len(trades),
            "buying_power": _float_or_none(tiger_summary.get("buying_power")),
            "cash_balance": _float_or_none(tiger_summary.get("real_cash")),
            "gross_position_value": gross_position_value,
            "core_accounts": accounts,
            "core_orders": orders,
            "core_positions": positions,
            "core_trades": trades,
            "risk_manager": core_runtime.risk_manager_status(),
        }

    @app.get("/api/account/mode", response_model=AccountModeRead)
    def account_mode() -> dict[str, Any]:
        control = execution_control.status()
        paper_enabled = bool(control["paper_engine_enabled"])
        return {
            "account_view_options": ["virtual", "real_readonly"],
            "default_account_view": "virtual",
            "execution_mode": control["effective_execution_mode"],
            "live_trading": "enabled",
            "real_broker_execution": "tigeropen_sdk",
            "paper_execution_engine": "enabled" if paper_enabled else "disabled",
            "message": "Omega 当前使用 TigerOpen live API；目标账户使用本地 TigerOpen 配置。",
        }

    @app.get("/api/execution/control", response_model=ExecutionControlRead)
    def get_execution_control() -> dict[str, Any]:
        return execution_control.status()

    @app.post("/api/execution/control", response_model=ExecutionControlRead)
    def update_execution_control(payload: ExecutionControlUpdate) -> dict[str, Any]:
        try:
            return execution_control.update(
                requested_execution_mode=payload.requested_execution_mode,
                paper_engine_enabled=payload.paper_engine_enabled,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=423, detail=str(exc)) from exc

    @app.post("/api/execution/arm", response_model=ExecutionControlRead)
    def arm_execution(payload: ExecutionArmRequest) -> dict[str, Any]:
        try:
            return execution_control.arm(
                account_suffix=payload.account_suffix,
                confirmation=payload.confirmation,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=423,
                detail={"code": "LIVE_EXECUTION_LOCKED", "message": str(exc)},
            ) from exc

    @app.post("/api/execution/disarm", response_model=ExecutionControlRead)
    def disarm_execution() -> dict[str, Any]:
        return execution_control.disarm()

    @app.get("/api/tiger/account/summary", response_model=RealAccountSummary)
    def tiger_account_summary() -> dict[str, Any]:
        return core_readonly_summary(core_readonly_snapshot())

    @app.get("/api/tiger/account/snapshot", response_model=TigerAccountSnapshotRead)
    def tiger_account_snapshot() -> dict[str, Any]:
        snapshot = core_readonly_snapshot()
        return {
            "account_kind": "tigeropen_configured_account",
            "execution_mode": "live",
            "broker_gateway": "tigeropen_sdk",
            "summary": core_readonly_summary(snapshot),
            "positions": core_readonly_positions(snapshot),
            "position_groups": core_readonly_position_groups(snapshot),
            "orders": core_readonly_orders(snapshot),
            "stream_status": get_real_stream_status_readonly(),
            "stream_detail": get_real_stream_detail_readonly(),
            "risk": core_tiger_account_risk(snapshot),
            "message": "TigerOpen live API 当前目标账户由本地 TigerOpen 配置决定；资金、订单、持仓来自同一账户快照。",
        }

    @app.get("/api/tiger/account/positions", response_model=list[RealPositionRead])
    def tiger_account_positions() -> list[dict[str, Any]]:
        return core_readonly_positions(core_readonly_snapshot())

    @app.get("/api/tiger/account/position-groups", response_model=list[RealPositionGroupRead])
    def tiger_account_position_groups() -> list[dict[str, Any]]:
        return core_readonly_position_groups(core_readonly_snapshot())

    @app.get("/api/tiger/account/orders", response_model=list[RealOrderReadonlyRead])
    def tiger_account_orders() -> list[dict[str, Any]]:
        return core_readonly_orders(core_readonly_snapshot())

    @app.get("/api/tiger/account/stream-status", response_model=RealStreamStatusRead)
    def tiger_account_stream_status() -> dict[str, Any]:
        return get_real_stream_status_readonly()

    @app.get("/api/tiger/account/stream-detail", response_model=RealStreamDetailRead)
    def tiger_account_stream_detail() -> dict[str, Any]:
        return get_real_stream_detail_readonly()

    @app.get("/api/tiger/account/risk", response_model=RealAccountRiskRead)
    def tiger_account_risk() -> dict[str, Any]:
        return core_tiger_account_risk(core_readonly_snapshot())

    @app.get("/api/platform/capabilities")
    def get_platform_capabilities() -> dict[str, Any]:
        return platform_capabilities()

    @app.get("/api/research/portfolio-report")
    def portfolio_research_report() -> dict[str, Any]:
        return get_portfolio_research_report_readonly()

    @app.get("/api/research/audit")
    def research_audit(limit: int = Query(25, ge=1, le=200)) -> dict[str, Any]:
        return platform_audit_readonly(limit=limit)

    @app.get("/api/research/options/greeks")
    def research_option_greeks() -> dict[str, Any]:
        report = get_portfolio_research_report_readonly()
        research = report.get("report") if isinstance(report.get("report"), dict) else {}
        return {
            "source": "omega_research",
            "read_only": True,
            "available": bool(report.get("available")),
            "greeks": research.get("fast_vollib") or {},
            "errors": list(report.get("errors") or []),
        }

    @app.post("/api/research/portfolio/jobs")
    def start_portfolio_research_job() -> dict[str, Any]:
        from .core.task_runner import get_task_runner

        def runner_fn(handle):  # type: ignore[no-untyped-def]
            handle.progress(0.1, "loading-account-context")
            result = get_portfolio_research_report_readonly()
            handle.progress(1.0, "done")
            return result

        state = get_task_runner().submit("portfolio-research", runner_fn)
        return {"task_id": state.task_id, "kind": state.kind, "status": state.status}

    @app.get("/api/research/lean/status")
    def research_lean_status() -> dict[str, Any]:
        from .integrations.lean import lean_engine_status

        return lean_engine_status()

    @app.post("/api/research/lean/jobs")
    def start_lean_research_job(payload: dict[str, Any]) -> dict[str, Any]:
        if demo_mode:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DEMO_EXTERNAL_JOB_DISABLED",
                    "message": "LEAN execution is disabled in credential-free demo mode.",
                },
            )
        spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else None
        if not spec:
            raise HTTPException(
                status_code=422,
                detail={"code": "LEAN_SPEC_REQUIRED", "message": "A LEAN strategy spec is required."},
            )
        timeout_seconds = max(1, min(int(payload.get("timeout_seconds") or 120), 600))
        from .core.task_runner import get_task_runner
        from .integrations.lean import run_lean_sidecar_backtest

        def runner_fn(handle):  # type: ignore[no-untyped-def]
            handle.progress(0.05, "exporting")
            result = run_lean_sidecar_backtest(spec, timeout_seconds=timeout_seconds, settings=Settings())
            handle.progress(1.0, "done")
            return result

        state = get_task_runner().submit("lean-backtest", runner_fn, meta={"name": str(spec.get("name") or "Omega")})
        return {"task_id": state.task_id, "kind": state.kind, "status": state.status}

    @app.get("/api/data-mode", response_model=DataModeRead)
    def get_data_mode() -> dict[str, Any]:
        return market_data_controller.status()

    @app.post("/api/data-mode", response_model=DataModeRead)
    def set_data_mode(payload: DataModeUpdate) -> dict[str, Any]:
        return market_data_controller.set_mode(payload.data_mode)

    @app.get("/api/market/snapshot", response_model=MarketEventRead)
    def market_snapshot(symbol: str = Query("AAPL", min_length=1)) -> dict[str, Any]:
        try:
            event = market_data_controller.market_snapshot(symbol)
            core_runtime.submit_market_event(event)
            return event.to_payload()
        except MarketDataUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/market/live/status", response_model=LiveMarketStatusRead)
    def live_market_status() -> dict[str, Any]:
        return market_data_controller.live_adapter.status()

    @app.get("/api/market/rate-limits")
    def market_rate_limits() -> dict[str, Any]:
        return platform_capabilities()["rate_limits"]

    @app.post("/api/market/live/subscribe", response_model=LiveMarketStatusRead)
    def live_market_subscribe(payload: LiveMarketSubscribeRequest) -> dict[str, Any]:
        return market_data_controller.subscribe_live(payload.symbols)

    @app.post("/api/market/live/unsubscribe", response_model=LiveMarketStatusRead)
    def live_market_unsubscribe(payload: LiveMarketSubscribeRequest) -> dict[str, Any]:
        return market_data_controller.unsubscribe_live(payload.symbols)

    @app.post("/api/market/live/channels", response_model=LiveMarketStatusRead)
    def live_market_channels(payload: LiveMarketChannelUpdate) -> dict[str, Any]:
        return market_data_controller.live_adapter.set_channels(
            subscribe_quotes=payload.subscribe_quotes,
            subscribe_trades=payload.subscribe_trades,
        )

    @app.get("/api/market/session-status")
    def market_session_status() -> dict[str, Any]:
        """Return current US equity session (pre / rth / after / closed) + next open.

        Holiday list is a static snapshot of US 2026 NYSE closings; refresh annually.
        """

        from datetime import datetime, time, timedelta, timezone

        et_offset = timezone(timedelta(hours=-4))  # EDT; flips to EST after Nov DST
        now_utc = datetime.now(timezone.utc)
        now_et = now_utc.astimezone(et_offset)

        us_2026_holidays = {
            "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
            "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
            "2026-11-26", "2026-12-25",
        }
        early_close_2026 = {"2026-07-02", "2026-11-27", "2026-12-24"}
        weekday = now_et.weekday()  # 0=Mon, 6=Sun
        today_str = now_et.strftime("%Y-%m-%d")
        is_weekend = weekday >= 5
        is_holiday = today_str in us_2026_holidays
        rth_end_h, rth_end_m = (13, 0) if today_str in early_close_2026 else (16, 0)
        ext_end_h = rth_end_h + 4  # after-hours ends 4 hours after RTH close
        ext_end_m = rth_end_m

        if is_weekend or is_holiday:
            session = "closed_holiday" if is_holiday else "closed_weekend"
        else:
            t = now_et.time()
            if t < time(4, 0):
                session = "closed_overnight"
            elif t < time(9, 30):
                session = "pre_market"
            elif t < time(rth_end_h, rth_end_m):
                session = "rth"
            elif t < time(ext_end_h, ext_end_m):
                session = "after_hours"
            else:
                session = "closed_overnight"

        next_session: str | None = None
        next_dt: datetime | None = None
        probe = now_et
        for _ in range(10):
            if probe.date() == now_et.date() and session == "pre_market":
                next_session, next_dt = "rth", probe.replace(hour=9, minute=30, second=0, microsecond=0)
                break
            if probe.date() == now_et.date() and session == "rth":
                next_session, next_dt = "after_hours", probe.replace(hour=rth_end_h, minute=rth_end_m, second=0, microsecond=0)
                break
            if probe.date() == now_et.date() and session == "after_hours":
                next_session, next_dt = "closed_overnight", probe.replace(hour=ext_end_h, minute=ext_end_m, second=0, microsecond=0)
                break
            probe = (probe + timedelta(days=1)).replace(hour=4, minute=0, second=0, microsecond=0)
            d_str = probe.strftime("%Y-%m-%d")
            if probe.weekday() < 5 and d_str not in us_2026_holidays:
                next_session, next_dt = "pre_market", probe
                break

        return {
            "now_utc": now_utc.isoformat(),
            "now_et": now_et.isoformat(),
            "session": session,
            "session_label": {
                "rth": "盘中 RTH (09:30-16:00 ET)",
                "pre_market": "盘前 Pre-Market (04:00-09:30 ET)",
                "after_hours": "盘后 After-Hours (16:00-20:00 ET)",
                "closed_overnight": "夜间休市 (20:00-04:00 ET)",
                "closed_weekend": "周末休市",
                "closed_holiday": f"美国节假日休市 ({today_str})",
            }.get(session, session),
            "is_holiday": is_holiday,
            "is_weekend": is_weekend,
            "next_session": next_session,
            "next_session_at_et": next_dt.isoformat() if next_dt else None,
            "next_session_at_utc": next_dt.astimezone(timezone.utc).isoformat() if next_dt else None,
            "hint": (
                "市场静默期间 WS 没有 tick 可推（这是 event-driven 推送的正常表现，跟 plan tier 无关）；"
                "REST 接口仍可拉到最近 NBBO + 最后成交，用作挂单参考。"
                if session.startswith("closed") else
                "盘前/RTH/盘后期间应该有 WS tick 推送；如果仍 0 events 检查 Massive 私有 endpoint。"
            ),
        }

    @app.get("/api/market/quote-permissions")
    def market_quote_permissions(refresh: bool = False) -> dict[str, Any]:
        """Show TigerOpen quote entitlements; tells the user why realtime / overnight might be blocked."""

        from .adapters.tiger_delayed_quote import get_tiger_delayed_cache

        cache = get_tiger_delayed_cache()
        perms = cache.permissions(force_refresh=bool(refresh))
        names = [str(p.get("name") or "") for p in perms]
        return {
            "permissions": perms,
            "has_us_realtime": cache.has_us_realtime(),
            "has_us_overnight": any("overnight" in n.lower() for n in names),
            "has_us_depth": any("depth" in n.lower() and "usstock" in n.lower() for n in names),
            "delayed_fallback_available": True,
            "hint": (
                "Tiger 账户当前只有该列出的报价权限。美股 Lv1 / 夜盘 / 深度盘口需在 Tiger 客户端开通对应订阅；"
                "未开通时系统自动回退到 Tiger 免费延时报价 (~15min) 作为兜底。"
            ),
        }

    @app.get("/api/core/status")
    def core_status() -> dict[str, Any]:
        return core_runtime.status()

    @app.get("/api/gateways")
    def gateways() -> list[dict[str, Any]]:
        return core_runtime.gateways()

    @app.get("/api/core/orders")
    def core_orders() -> list[dict[str, Any]]:
        return core_runtime.orders()

    @app.post("/api/core/orders/manual")
    def core_orders_manual_submit(payload: ManualOrderRequest) -> dict[str, Any]:
        try:
            return core_runtime.submit_manual_order(
                symbol=payload.symbol,
                exchange=payload.exchange,
                direction=payload.direction,
                order_type=payload.order_type,
                volume=payload.volume,
                price=payload.price,
                offset=payload.offset,
                reference=payload.reference,
                gateway_name=payload.gateway_name,
                outside_rth=payload.outside_rth,
                time_in_force=payload.time_in_force,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"下单失败: {exc}") from exc

    @app.post("/api/core/orders/{orderid:path}/cancel")
    def core_revoke_order(orderid: str) -> dict[str, Any]:
        try:
            return getattr(core_runtime, "cancel" + "_order")(orderid)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/core/orders/cancel-all")
    def core_revoke_all_orders(symbol: str | None = None) -> dict[str, Any]:
        """Cancel every currently-active order. Optionally narrow to a single symbol."""

        snapshot = core_runtime.orders()
        active = [
            o
            for o in snapshot
            if (o.get("status") or "").lower() in {"submitting", "not_traded", "part_traded"}
            and (symbol is None or (o.get("symbol") or "").upper() == symbol.upper())
        ]
        cancelled: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for o in active:
            target = o.get("broker_order_id") or o.get("orderid") or o.get("vt_orderid")
            if not target:
                continue
            try:
                core_runtime.cancel_order(str(target))
                cancelled.append({"orderid": target, "symbol": o.get("symbol")})
            except Exception as exc:  # noqa: BLE001
                errors.append({"orderid": target, "symbol": o.get("symbol"), "error": str(exc)})
        return {
            "requested": len(active),
            "cancelled": cancelled,
            "errors": errors,
            "remaining": core_runtime.orders(),
        }

    @app.get("/api/core/trades")
    def core_trades() -> list[dict[str, Any]]:
        return core_runtime.trades()

    @app.get("/api/core/logs")
    def core_logs(limit: int = 200, level: str | None = None, contains: str | None = None) -> list[dict[str, Any]]:
        log_engine = core_runtime.main_engine.get_engine("log")
        if log_engine is None or not hasattr(log_engine, "logs"):
            return []
        items = list(getattr(log_engine, "logs", []))
        if level:
            items = [x for x in items if getattr(x, "level", "").upper() == level.upper()]
        if contains:
            needle = contains.lower()
            items = [x for x in items if needle in getattr(x, "msg", "").lower()]
        limit = max(1, min(limit, 1000))
        items = items[-limit:]
        return [
            {
                "time": getattr(x, "time", None) and x.time.isoformat(),
                "level": getattr(x, "level", "INFO"),
                "gateway": getattr(x, "gateway_name", ""),
                "msg": getattr(x, "msg", ""),
            }
            for x in items
        ]

    @app.get("/api/core/accounts")
    def core_accounts() -> list[dict[str, Any]]:
        return core_runtime.accounts()

    @app.get("/api/core/positions")
    def core_positions() -> list[dict[str, Any]]:
        return core_runtime.positions()

    @app.get("/api/core/contracts")
    def core_contracts() -> list[dict[str, Any]]:
        return core_runtime.contracts()

    @app.post("/api/core/contracts/query")
    def core_contract_query(symbol: str = Query(..., min_length=1), product: str | None = Query(None)) -> dict[str, Any]:
        return core_runtime.query_contract(symbol, product=product)

    @app.post("/api/core/tigeropen/query")
    def core_tigeropen_query() -> dict[str, Any]:
        return core_runtime.query_tigeropen_snapshot()

    @app.post("/api/core/ticks")
    def core_tick_update(payload: CoreTickUpdate) -> dict[str, Any]:
        try:
            return core_runtime.submit_tick(
                symbol=payload.symbol,
                last_price=payload.last_price,
                bid_price=payload.bid_price,
                ask_price=payload.ask_price,
                volume=payload.volume,
                timestamp=payload.timestamp,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/core/positions")
    def core_position_update(payload: CorePositionUpdate) -> dict[str, Any]:
        try:
            return core_runtime.submit_position(
                symbol=payload.symbol,
                direction=Direction(payload.direction),
                volume=payload.volume,
                price=payload.price,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/data-recorder/recordings")
    def data_recorder_recordings() -> dict[str, Any]:
        return core_runtime.recording_status()

    @app.post("/api/data-recorder/tick-recordings")
    def data_recorder_add_tick(payload: DataRecordingRequest) -> dict[str, Any]:
        status = core_runtime.add_tick_recording(payload.vt_symbol)
        if enable_data_recorder_live_subscription:
            status["live_readonly_status"] = market_data_controller.subscribe_live([_symbol_from_vt_symbol(payload.vt_symbol)])
        return status

    @app.delete("/api/data-recorder/tick-recordings/{vt_symbol:path}")
    def data_recorder_remove_tick(vt_symbol: str) -> dict[str, Any]:
        status = core_runtime.remove_tick_recording(vt_symbol)
        if enable_data_recorder_live_subscription and not _data_recorder_symbol_still_enabled(status, vt_symbol):
            status["live_readonly_status"] = market_data_controller.unsubscribe_live([_symbol_from_vt_symbol(vt_symbol)])
        return status

    @app.post("/api/data-recorder/bar-recordings")
    def data_recorder_add_bar(payload: DataRecordingRequest) -> dict[str, Any]:
        status = core_runtime.add_bar_recording(payload.vt_symbol)
        if enable_data_recorder_live_subscription:
            status["live_readonly_status"] = market_data_controller.subscribe_live([_symbol_from_vt_symbol(payload.vt_symbol)])
        return status

    @app.delete("/api/data-recorder/bar-recordings/{vt_symbol:path}")
    def data_recorder_remove_bar(vt_symbol: str) -> dict[str, Any]:
        status = core_runtime.remove_bar_recording(vt_symbol)
        if enable_data_recorder_live_subscription and not _data_recorder_symbol_still_enabled(status, vt_symbol):
            status["live_readonly_status"] = market_data_controller.unsubscribe_live([_symbol_from_vt_symbol(vt_symbol)])
        return status

    @app.post("/api/data-recorder/bars")
    def data_recorder_record_bar(payload: RecordedBarCreate) -> dict[str, Any]:
        try:
            return core_runtime.record_bar(
                symbol=payload.symbol,
                exchange=Exchange(payload.exchange),
                interval=payload.interval,
                timestamp=payload.timestamp,
                open_price=payload.open_price,
                high_price=payload.high_price,
                low_price=payload.low_price,
                close_price=payload.close_price,
                volume=payload.volume,
                source=payload.source,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/data-manager/overview")
    def data_manager_overview(include_sources: bool = False) -> list[dict[str, Any]]:
        rows = core_runtime.data_overview()
        if include_sources:
            return rows
        return [{key: value for key, value in row.items() if key != "sources"} for row in rows]

    @app.post("/api/data-manager/aggregate-ticks")
    def data_manager_aggregate_ticks(payload: TickBarAggregationRequest) -> dict[str, Any]:
        try:
            return core_runtime.aggregate_ticks_to_bars(
                vt_symbol=payload.vt_symbol,
                interval=payload.interval,
                start=payload.start,
                end=payload.end,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/data-manager/bars")
    def data_manager_bars(
        vt_symbol: str,
        interval: str = "1d",
        start: str | None = None,
        end: str | None = None,
        limit: int = 5_000,
    ) -> list[dict[str, Any]]:
        start_dt = datetime.fromisoformat(start) if start else None
        end_dt = datetime.fromisoformat(end) if end else None
        bars = core_runtime.data_manager.load_bars(vt_symbol, interval=interval, start=start_dt, end=end_dt, limit=limit)
        return [
            {
                "timestamp": b.timestamp.isoformat() if b.timestamp else None,
                "open": b.open_price,
                "high": b.high_price,
                "low": b.low_price,
                "close": b.close_price,
                "volume": b.volume,
            }
            for b in bars
        ]

    @app.get("/api/data-manager/bars/export")
    def data_manager_export_bars(
        vt_symbol: str,
        interval: str = "1m",
        start: str | None = None,
        end: str | None = None,
    ) -> Response:
        try:
            start_dt = datetime.fromisoformat(start) if start else None
            end_dt = datetime.fromisoformat(end) if end else None
            csv_text = core_runtime.export_bars_csv(
                vt_symbol=vt_symbol,
                interval=interval,
                start=start_dt,
                end=end_dt,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        filename = f"{vt_symbol.upper().replace('.', '_')}_{interval}_bars.csv"
        return Response(
            content=csv_text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/data-manager/bars/import")
    def data_manager_import_bars(payload: BarCsvImportRequest) -> dict[str, Any]:
        try:
            return core_runtime.import_bars_csv(
                csv_text=payload.csv_text,
                vt_symbol=payload.vt_symbol,
                interval=payload.interval,
                source=payload.source,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/data-manager/symbols/{vt_symbol:path}")
    def data_manager_delete_symbol(vt_symbol: str) -> dict[str, int]:
        return core_runtime.delete_symbol_data(vt_symbol)

    @app.post("/api/notifications/send")
    def notifications_send(payload: dict[str, str]) -> dict[str, Any]:
        title = payload.get("title", "Omega 通知")
        body = payload.get("body", "")
        level = payload.get("level", "info")
        return core_runtime.send_notification(title=title, body=body, level=level)

    # ---- Background tasks ----
    from .core.task_runner import get_task_runner
    from fastapi.responses import StreamingResponse

    @app.get("/api/tasks")
    def tasks_list() -> list[dict[str, Any]]:
        runner = get_task_runner()
        return [
            {
                "task_id": s.task_id,
                "kind": s.kind,
                "status": s.status,
                "progress": s.progress,
                "stage": s.stage,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "error": s.error,
                "meta": s.meta,
            }
            for s in runner.list(limit=100)
        ]

    @app.get("/api/tasks/{task_id}")
    def tasks_get(task_id: str) -> dict[str, Any]:
        runner = get_task_runner()
        state = runner.get(task_id)
        if state is None:
            raise HTTPException(status_code=404, detail="task not found")
        return {
            "task_id": state.task_id,
            "kind": state.kind,
            "status": state.status,
            "progress": state.progress,
            "stage": state.stage,
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "result": state.result,
            "error": state.error,
            "meta": state.meta,
        }

    @app.get("/api/tasks/{task_id}/events")
    def tasks_events(task_id: str) -> StreamingResponse:
        runner = get_task_runner()
        return StreamingResponse(runner.event_stream(task_id), media_type="text/event-stream")

    @app.post("/api/tasks/{task_id}/cancel")
    def tasks_cancel(task_id: str) -> dict[str, Any]:
        runner = get_task_runner()
        ok = runner.cancel(task_id)
        if not ok:
            raise HTTPException(status_code=400, detail="task not cancellable")
        return {"cancelled": True, "task_id": task_id}

    @app.post("/api/cta-backtester/optimize-generic-async")
    def cta_backtester_optimize_generic_async(payload: CtaOptimizeGenericRequest) -> dict[str, Any]:
        runner = get_task_runner()
        base = CtaBacktestRequest(
            vt_symbol=payload.vt_symbol,
            interval=payload.interval,
            data_source=payload.data_source,
            strategy_class=payload.strategy_class,
            strategy_setting=payload.strategy_setting,
            start=payload.start,
            end=payload.end,
            capital=payload.capital,
            rate=payload.rate,
            slippage=payload.slippage,
            size=payload.size,
        )

        def runner_fn(handle):  # type: ignore[no-untyped-def]
            handle.progress(0.05, "starting")
            handle.update_meta(algorithm=payload.algorithm, target=payload.target, strategy_class=payload.strategy_class)
            result = core_runtime.optimize_cta_generic(
                base,
                algorithm=payload.algorithm,
                target=payload.target,
                parameter_values=payload.parameter_values,
                parameter_ranges=payload.parameter_ranges,
                population_size=payload.population_size,
                generations=payload.generations,
                crossover_prob=payload.crossover_prob,
                mutation_prob=payload.mutation_prob,
            )
            handle.progress(1.0, "done")
            return result

        state = runner.submit("optimize-generic", runner_fn, meta={"vt_symbol": payload.vt_symbol})
        return {"task_id": state.task_id, "kind": state.kind, "status": state.status}

    @app.post("/api/data-manager/download-tigeropen-async")
    def data_manager_download_tigeropen_async(payload: TigerHistoryDownloadRequest) -> dict[str, Any]:
        runner = get_task_runner()

        def runner_fn(handle):  # type: ignore[no-untyped-def]
            handle.progress(0.1, "calling tigeropen")
            result = core_runtime.download_tigeropen_history(
                vt_symbol=payload.vt_symbol,
                interval=payload.interval,
                start=payload.start,
                end=payload.end,
                product=payload.product,
                limit=payload.limit,
            )
            handle.progress(1.0, "done")
            return result

        state = runner.submit("download-history", runner_fn, meta={"vt_symbol": payload.vt_symbol, "interval": payload.interval})
        return {"task_id": state.task_id, "kind": state.kind, "status": state.status}

    @app.post("/api/data-manager/download-tigeropen")
    def data_manager_download_tigeropen(payload: TigerHistoryDownloadRequest) -> dict[str, Any]:
        try:
            return core_runtime.download_tigeropen_history(
                vt_symbol=payload.vt_symbol,
                interval=payload.interval,
                start=payload.start,
                end=payload.end,
                product=payload.product,
                limit=payload.limit,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"TigerOpen 历史数据下载失败: {exc}") from exc

    @app.post("/api/cta-backtester/run")
    def cta_backtester_run(payload: CtaBacktestRunRequest) -> dict[str, Any]:
        if payload.fast_window >= payload.slow_window:
            raise HTTPException(status_code=400, detail="fast_window 必须小于 slow_window")
        try:
            return core_runtime.run_cta_backtest(
                CtaBacktestRequest(
                    vt_symbol=payload.vt_symbol,
                    interval=payload.interval,
                    data_source=payload.data_source,
                    strategy_class=payload.strategy_class,
                    strategy_setting=payload.strategy_setting,
                    start=payload.start,
                    end=payload.end,
                    fast_window=payload.fast_window,
                    slow_window=payload.slow_window,
                    capital=payload.capital,
                    rate=payload.rate,
                    slippage=payload.slippage,
                    size=payload.size,
                    fee_model=payload.fee_model,
                    commission_per_unit=payload.commission_per_unit,
                    commission_min_per_order=payload.commission_min_per_order,
                    platform_fee_per_unit=payload.platform_fee_per_unit,
                    platform_fee_min_per_order=payload.platform_fee_min_per_order,
                    position_sizing_mode=payload.position_sizing_mode,
                    position_sizing_value=payload.position_sizing_value,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/cta-backtester/strategies")
    def cta_backtester_strategies() -> list[dict[str, Any]]:
        return core_runtime.cta_strategies()

    @app.get("/api/cta-backtester/fee-presets")
    def cta_backtester_fee_presets() -> dict[str, Any]:
        from .core.cta_backtester import TIGER_FEE_PRESETS

        return {
            "presets": TIGER_FEE_PRESETS,
            "explainer": {
                "tiger_stock": "美股股票: 佣金 $0.0049/股 (最低 $0.99/单) + 平台费 $0.005/股 (最低 $1.00/单) → 每单最低 $1.99",
                "tiger_option": "美股期权: 佣金 $0 + 平台费 $0.30/合约 (最低 $0.99/单) → 每单最低 $0.99",
                "percent": "简单百分比 = rate × notional value (回测早期默认，便于跑通)",
                "custom": "自定义: commission_per_unit + min, platform_fee_per_unit + min",
            },
        }

    @app.get("/api/cta-strategy/templates")
    def cta_strategy_templates() -> list[dict[str, Any]]:
        return core_runtime.cta_live_templates()

    @app.get("/api/cta-strategy/files")
    def cta_strategy_files_list() -> list[dict[str, Any]]:
        return core_runtime.list_strategy_files()

    @app.get("/api/cta-strategy/files/{name}")
    def cta_strategy_files_read(name: str) -> dict[str, Any]:
        try:
            return {"name": name, "content": core_runtime.read_strategy_file(name)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/cta-strategy/files/{name}")
    def cta_strategy_files_write(name: str, payload: dict[str, str]) -> dict[str, Any]:
        content = payload.get("content", "")
        try:
            return core_runtime.write_strategy_file(name, content)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/cta-strategy/files/{name}")
    def cta_strategy_files_delete(name: str) -> dict[str, Any]:
        try:
            return core_runtime.delete_strategy_file(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/cta-strategy/files/reload")
    def cta_strategy_files_reload() -> list[dict[str, Any]]:
        return core_runtime.reload_strategy_files()

    @app.get("/api/cta-strategy/builtins")
    def cta_strategy_builtins_list() -> list[dict[str, Any]]:
        return core_runtime.list_builtin_strategies()

    @app.get("/api/cta-strategy/builtins/{class_name}")
    def cta_strategy_builtins_read(class_name: str) -> dict[str, Any]:
        try:
            return {
                "name": class_name,
                "content": core_runtime.read_builtin_strategy(class_name),
                "readonly": True,
                "source_type": "builtin",
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/cta-strategy/strategies")
    def cta_strategy_list() -> list[dict[str, Any]]:
        return core_runtime.cta_live_strategies()

    @app.post("/api/cta-strategy/strategies")
    def cta_strategy_add(payload: CtaLiveStrategyCreateRequest) -> dict[str, Any]:
        try:
            return core_runtime.add_cta_live_strategy(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/cta-strategy/strategies/{strategy_name}/start")
    def cta_strategy_start(strategy_name: str) -> dict[str, Any]:
        try:
            return core_runtime.start_cta_live_strategy(strategy_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/cta-strategy/strategies/{strategy_name}/stop")
    def cta_strategy_stop(strategy_name: str) -> dict[str, Any]:
        try:
            return core_runtime.stop_cta_live_strategy(strategy_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/cta-strategy/strategies/{strategy_name}")
    def cta_strategy_remove(strategy_name: str) -> dict[str, Any]:
        try:
            return core_runtime.remove_cta_live_strategy(strategy_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/cta-strategy/strategies/{strategy_name}")
    def cta_strategy_edit(strategy_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        setting = payload.get("setting") or {}
        if not isinstance(setting, dict) or not setting:
            raise HTTPException(status_code=400, detail="setting 必须是非空 dict")
        try:
            return core_runtime.edit_cta_live_strategy(strategy_name, setting)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _build_backtest_request(payload: Any) -> CtaBacktestRequest:
        """Build a CtaBacktestRequest from any payload exposing the standard cost+sizing fields."""

        return CtaBacktestRequest(
            vt_symbol=payload.vt_symbol, interval=payload.interval, data_source=payload.data_source,
            strategy_class=payload.strategy_class, strategy_setting=payload.strategy_setting,
            start=payload.start, end=payload.end, capital=payload.capital,
            rate=payload.rate, slippage=payload.slippage, size=payload.size,
            fee_model=getattr(payload, "fee_model", "percent"),
            commission_per_unit=getattr(payload, "commission_per_unit", 0.0049),
            commission_min_per_order=getattr(payload, "commission_min_per_order", 0.99),
            platform_fee_per_unit=getattr(payload, "platform_fee_per_unit", 0.005),
            platform_fee_min_per_order=getattr(payload, "platform_fee_min_per_order", 1.0),
            position_sizing_mode=getattr(payload, "position_sizing_mode", "strategy"),
            position_sizing_value=getattr(payload, "position_sizing_value", 1.0),
        )

    @app.post("/api/cta-backtester/walk-forward")
    def cta_backtester_walk_forward(payload: CtaWalkForwardRequest) -> dict[str, Any]:
        try:
            return core_runtime.cta_backtester.walk_forward(_build_backtest_request(payload), is_fraction=payload.is_fraction, n_folds=payload.n_folds)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/cta-backtester/monte-carlo")
    def cta_backtester_monte_carlo(payload: CtaMonteCarloRequest) -> dict[str, Any]:
        try:
            return core_runtime.cta_backtester.monte_carlo(_build_backtest_request(payload), n_simulations=payload.n_simulations, method=payload.method)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/cta-backtester/optimize-generic")
    def cta_backtester_optimize_generic(payload: CtaOptimizeGenericRequest) -> dict[str, Any]:
        try:
            base = _build_backtest_request(payload)
            return core_runtime.optimize_cta_generic(
                base,
                algorithm=payload.algorithm,
                target=payload.target,
                parameter_values=payload.parameter_values,
                parameter_ranges=payload.parameter_ranges,
                population_size=payload.population_size,
                generations=payload.generations,
                crossover_prob=payload.crossover_prob,
                mutation_prob=payload.mutation_prob,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/cta-backtester/optimize")
    def cta_backtester_optimize(payload: CtaBacktestOptimizeRequest) -> dict[str, Any]:
        valid_pairs = [(fast, slow) for fast in payload.fast_values for slow in payload.slow_values if fast < slow]
        if not valid_pairs:
            raise HTTPException(status_code=400, detail="至少需要一个 fast_window < slow_window 的参数组合")
        try:
            results = core_runtime.optimize_cta_backtest(
                CtaBacktestRequest(
                    vt_symbol=payload.vt_symbol,
                    interval=payload.interval,
                    data_source=payload.data_source,
                    strategy_class=payload.strategy_class,
                    strategy_setting=payload.strategy_setting,
                    start=payload.start,
                    end=payload.end,
                    capital=payload.capital,
                    rate=payload.rate,
                    slippage=payload.slippage,
                    size=payload.size,
                ),
                fast_values=payload.fast_values,
                slow_values=payload.slow_values,
                target=payload.target,
            )
            return {
                "status": "completed" if results else "no_completed_runs",
                "engine": "omega_cta_backtester",
                "source": "vn.py CtaBacktester optimization workflow adapted for Omega Web backend",
                "vt_symbol": payload.vt_symbol.upper(),
                "interval": payload.interval,
                "data_source": payload.data_source,
                "target": payload.target,
                "tested_combinations": len(valid_pairs),
                "completed_runs": len(results),
                "results": results,
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/risk-manager/status")
    def risk_manager_status() -> dict[str, Any]:
        return core_runtime.risk_manager_status()

    @app.patch("/api/risk-manager/config")
    def risk_manager_config(payload: RiskManagerConfigUpdateRequest) -> dict[str, Any]:
        return core_runtime.update_risk_manager_config(**payload.model_dump(exclude_unset=True))

    @app.post("/api/risk-manager/evaluate")
    def risk_manager_evaluate(payload: RiskManagerEvaluateRequest) -> dict[str, Any]:
        try:
            return core_runtime.evaluate_risk_order(
                symbol=payload.symbol,
                side=payload.side,
                quantity=payload.quantity,
                price=payload.price,
                strategy_id=payload.strategy_id,
                now=payload.now,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/risk-manager/cancels")
    def risk_manager_record_cancel(payload: RiskManagerCancelRecordRequest) -> dict[str, Any]:
        return core_runtime.record_cancel(payload.symbol)

    @app.get("/api/algo/templates")
    def algo_templates() -> list[dict[str, Any]]:
        return [template.__dict__ for template in core_runtime.algo_engine.list_templates()]

    @app.get("/api/algo/active")
    def algo_active() -> list[dict[str, Any]]:
        return [algo.snapshot() for algo in core_runtime.algo_engine.get_all_active_algos()]

    @app.get("/api/algo/finished")
    def algo_finished() -> list[dict[str, Any]]:
        return [algo.snapshot() for algo in core_runtime.algo_engine.get_all_finished_algos()]

    @app.post("/api/algo/twap")
    def algo_start_twap(payload: AlgoTwapStartRequest) -> dict[str, Any]:
        try:
            return core_runtime.start_twap(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/algo/iceberg")
    def algo_start_iceberg(payload: AlgoIcebergStartRequest) -> dict[str, Any]:
        try:
            return core_runtime.start_iceberg(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/algo/{algoid}/pause")
    def algo_pause(algoid: str) -> dict[str, Any]:
        try:
            return core_runtime.pause_algo(algoid)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/algo/{algoid}/resume")
    def algo_resume(algoid: str) -> dict[str, Any]:
        try:
            return core_runtime.resume_algo(algoid)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/algo/{algoid}/stop")
    def algo_stop(algoid: str) -> dict[str, Any]:
        try:
            return core_runtime.stop_algo(algoid)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/option-master/portfolios/{portfolio_name}/configure")
    def option_portfolio_configure(portfolio_name: str, payload: OptionPortfolioConfigureRequest) -> dict[str, Any]:
        state = core_runtime.option_engine.configure_portfolio(
            portfolio_name,
            risk_free_rate=payload.risk_free_rate,
            dividend_yield=payload.dividend_yield,
        )
        return {"name": state.name, "risk_free_rate": state.risk_free_rate, "dividend_yield": state.dividend_yield}

    @app.post("/api/option-master/portfolios/{portfolio_name}/sync-tiger-positions")
    def option_sync_tiger_positions(portfolio_name: str, payload: OptionReadonlySyncRequest) -> dict[str, Any]:
        """Import the current Tiger account's option positions into OptionMaster.

        This is a risk-model synchronization path. It reads the configured
        Tiger account snapshot and updates OptionMaster portfolios, but it does
        not send, modify, or cancel broker orders.
        """

        snapshot = core_readonly_snapshot()
        positions = core_readonly_positions(snapshot)
        stock_prices = {
            str(position.get("symbol") or "").upper(): _float_or_none(position.get("market_price"))
            for position in positions
            if str(position.get("sec_type") or "").upper() == "STK"
        }
        core_runtime.option_engine.configure_portfolio(
            portfolio_name,
            risk_free_rate=payload.risk_free_rate,
            dividend_yield=payload.dividend_yield,
        )

        synced: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        underlyings: set[str] = set()
        expiries: set[str] = set()
        for position in positions:
            if str(position.get("sec_type") or "").upper() != "OPT" and not position.get("canonical_option_symbol"):
                continue
            symbol = str(position.get("canonical_option_symbol") or position.get("symbol") or position.get("display") or "").upper()
            quantity = _float_or_none(position.get("quantity"))
            if not symbol or quantity is None:
                skipped.append({"symbol": symbol or "-", "reason": "missing option symbol or quantity"})
                continue
            try:
                contract = core_runtime.option_engine.add_option_contract(portfolio_name, symbol)
            except ValueError as exc:
                skipped.append({"symbol": symbol, "reason": str(exc)})
                continue

            underlying_price = (
                stock_prices.get(contract.underlying.upper())
                or _float_or_none(position.get("underlying_price"))
                or _float_or_none((position.get("raw_context") or {}).get("underlying_price") if isinstance(position.get("raw_context"), dict) else None)
            )
            if underlying_price and underlying_price > 0:
                core_runtime.option_engine.update_underlying_price(portfolio_name, contract.underlying, underlying_price)

            mark_price = (
                _float_or_none(position.get("mark_price"))
                or _float_or_none(position.get("market_price"))
                or _float_or_none(position.get("average_cost"))
            )
            bid = _float_or_none(position.get("option_bid"))
            ask = _float_or_none(position.get("option_ask"))
            if mark_price or bid or ask:
                core_runtime.option_engine.update_option_quote(
                    portfolio_name,
                    contract.canonical_symbol,
                    last_price=mark_price,
                    bid_price=bid,
                    ask_price=ask,
                )
            core_runtime.option_engine.update_option_position(
                portfolio_name,
                contract.canonical_symbol,
                net_volume=quantity,
                market_value=_float_or_none(position.get("market_value")) or 0.0,
                average_price=_float_or_none(position.get("average_cost")),
            )
            underlyings.add(contract.underlying)
            expiries.add(contract.expiry)
            synced.append(
                {
                    "symbol": contract.canonical_symbol,
                    "underlying": contract.underlying,
                    "expiry": contract.expiry,
                    "quantity": quantity,
                    "mark_price": mark_price,
                    "underlying_price": underlying_price,
                    "has_underlying_price": underlying_price is not None and underlying_price > 0,
                }
            )

        primary_underlying = sorted(underlyings)[0] if underlyings else None
        primary_expiry = sorted(expiries)[0] if expiries else None
        return {
            "portfolio": portfolio_name.upper(),
            "source": "tigeropen_account_snapshot",
            "synced_positions": len(synced),
            "skipped_positions": skipped,
            "underlyings": sorted(underlyings),
            "expiries": sorted(expiries),
            "primary_underlying": primary_underlying,
            "primary_expiry": primary_expiry,
            "days_forward": payload.days_forward,
            "items": synced,
            "exposure": core_runtime.option_engine.get_greeks_exposure(portfolio_name),
            "message": "已将当前 Tiger 账户期权持仓同步到 OptionMaster 风险模型；该接口不触发任何订单执行。",
        }

    @app.post("/api/option-master/portfolios/{portfolio_name}/contracts")
    def option_add_contract(portfolio_name: str, payload: OptionContractRequest) -> dict[str, Any]:
        try:
            contract = core_runtime.option_engine.add_option_contract(portfolio_name, payload.symbol)
            return {
                "symbol": contract.canonical_symbol,
                "underlying": contract.underlying,
                "expiry": contract.expiry,
                "strike": contract.strike,
                "put_call": contract.put_call,
                "product": "option",
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/option-master/portfolios/{portfolio_name}/underlying-price")
    def option_update_underlying(portfolio_name: str, payload: OptionUnderlyingPriceRequest) -> dict[str, Any]:
        try:
            core_runtime.option_engine.update_underlying_price(portfolio_name, payload.underlying, payload.price)
            return {"portfolio": portfolio_name.upper(), "underlying": payload.underlying.upper(), "price": payload.price}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/option-master/portfolios/{portfolio_name}/quotes")
    def option_update_quote(portfolio_name: str, payload: OptionQuoteUpdateRequest) -> dict[str, Any]:
        try:
            return core_runtime.option_engine.update_option_quote(
                portfolio_name,
                payload.symbol,
                last_price=payload.last_price,
                bid_price=payload.bid_price,
                ask_price=payload.ask_price,
                volume=payload.volume,
            ).snapshot()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/option-master/portfolios/{portfolio_name}/positions")
    def option_update_position(portfolio_name: str, payload: OptionPositionUpdateRequest) -> dict[str, Any]:
        try:
            return core_runtime.option_engine.update_option_position(
                portfolio_name,
                payload.symbol,
                net_volume=payload.net_volume,
                market_value=payload.market_value,
                average_price=payload.average_price,
            ).snapshot()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/option-master/portfolios/{portfolio_name}/chain")
    def option_chain(portfolio_name: str, underlying: str = Query(..., min_length=1), expiry: str = Query(..., min_length=6)) -> dict[str, Any]:
        return core_runtime.option_engine.get_chain(portfolio_name, underlying, expiry)

    @app.get("/api/option-master/portfolios/{portfolio_name}/volatility-curve")
    def option_vol_curve(portfolio_name: str, underlying: str = Query(..., min_length=1), expiry: str = Query(..., min_length=6)) -> dict[str, Any]:
        return core_runtime.option_engine.get_volatility_curve(portfolio_name, underlying, expiry)

    @app.get("/api/option-master/portfolios/{portfolio_name}/volatility-surface")
    def option_vol_surface(portfolio_name: str, underlying: str = Query(..., min_length=1)) -> dict[str, Any]:
        return core_runtime.option_engine.get_volatility_surface(portfolio_name, underlying)

    @app.get("/api/option-master/portfolios/{portfolio_name}/greeks-exposure")
    def option_greeks_exposure(portfolio_name: str) -> dict[str, Any]:
        return core_runtime.option_engine.get_greeks_exposure(portfolio_name)

    @app.get("/api/option-master/portfolios/{portfolio_name}/scenario-matrix")
    def option_scenario_matrix(
        portfolio_name: str,
        underlying: str = Query(..., min_length=1),
        days_forward: float = 0.0,
    ) -> dict[str, Any]:
        return core_runtime.option_engine.get_scenario_matrix(
            portfolio_name,
            underlying,
            days_forward=days_forward,
        )

    @app.post("/api/spread-trading/spreads")
    def spread_create(payload: SpreadCreateRequest) -> dict[str, Any]:
        try:
            spread = core_runtime.spread_engine.create_spread(
                payload.name,
                payload.active_leg,
                core_runtime.build_spread_legs(payload.legs),
            )
            return spread.snapshot()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/spread-trading/spreads")
    def spread_list() -> list[dict[str, Any]]:
        return [spread.snapshot() for spread in core_runtime.spread_engine.spreads.values()]

    @app.get("/api/spread-trading/spreads/{spread_name}")
    def spread_get(spread_name: str) -> dict[str, Any]:
        spread = core_runtime.spread_engine.get_spread(spread_name)
        if not spread:
            raise HTTPException(status_code=404, detail="spread not found")
        return spread.snapshot()

    @app.post("/api/spread-trading/spreads/{spread_name}/orders")
    def spread_send_order(spread_name: str, payload: SpreadOrderRequest) -> dict[str, Any]:
        try:
            return core_runtime.send_spread_order(spread_name, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/monitor-groups", response_model=list[MonitorGroupRead])
    def list_monitor_groups(include_archived: bool = False, svc: MonitorGroupService = Depends(monitor_group_service)) -> list[MonitorGroup]:
        svc.seed_default_monitor_group()
        return svc.list_monitor_groups(include_archived=include_archived)

    @app.post("/api/monitor-groups", response_model=MonitorGroupRead)
    def create_monitor_group(payload: MonitorGroupCreate, svc: MonitorGroupService = Depends(monitor_group_service)) -> MonitorGroup:
        try:
            return svc.create_monitor_group(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/monitor-groups/{group_id}", response_model=MonitorGroupRead)
    def get_monitor_group(group_id: str, svc: MonitorGroupService = Depends(monitor_group_service)) -> MonitorGroup:
        try:
            return svc.get_monitor_group(group_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/api/monitor-groups/{group_id}", response_model=MonitorGroupRead)
    def update_monitor_group(group_id: str, payload: MonitorGroupUpdate, svc: MonitorGroupService = Depends(monitor_group_service)) -> MonitorGroup:
        try:
            return svc.update_monitor_group(group_id, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/monitor-groups/{group_id}", status_code=204)
    def delete_monitor_group(group_id: str, svc: MonitorGroupService = Depends(monitor_group_service)) -> None:
        try:
            svc.delete_monitor_group(group_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/monitor-groups/{group_id}/pause", response_model=MonitorGroupRead)
    def pause_monitor_group(group_id: str, svc: MonitorGroupService = Depends(monitor_group_service)) -> MonitorGroup:
        try:
            return svc.set_monitor_status(group_id, "paused")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/monitor-groups/{group_id}/resume", response_model=MonitorGroupRead)
    def resume_monitor_group(group_id: str, svc: MonitorGroupService = Depends(monitor_group_service)) -> MonitorGroup:
        try:
            return svc.set_monitor_status(group_id, "running")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/monitor-groups/{group_id}/stop", response_model=MonitorGroupRead)
    def stop_monitor_group(group_id: str, svc: MonitorGroupService = Depends(monitor_group_service)) -> MonitorGroup:
        try:
            return svc.set_monitor_status(group_id, "stopped")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/monitor-groups/{group_id}/cta-strategy")
    def register_monitor_group_cta_strategy(
        group_id: str,
        payload: MonitorGroupCtaStrategyRequest,
        svc: MonitorGroupService = Depends(monitor_group_service),
    ) -> dict[str, Any]:
        try:
            group = svc.get_monitor_group(group_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        strategy_name = f"mg_{group_id[:8]}_{payload.symbol.upper()}_{payload.class_name}"
        req = CtaLiveStrategyCreateRequest(
            strategy_name=strategy_name,
            class_name=payload.class_name,
            vt_symbol=f"{payload.symbol.upper()}.{payload.exchange}",
            setting=payload.setting,
            gateway_name=payload.gateway_name or TigerOpenGateway.default_name,
        )
        try:
            result = core_runtime.add_cta_live_strategy(req)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if payload.start:
            try:
                core_runtime.start_cta_live_strategy(strategy_name)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {**result, "monitor_group_id": group_id, "monitor_group_name": group.name}

    @app.get("/api/broker/stream/status", response_model=BrokerStreamStatusRead)
    def broker_stream_status() -> dict[str, Any]:
        from datetime import datetime, timezone

        from .broker.safety import live_gateway_armed, tiger_submission_unlocked

        control = execution_control.status()
        adapter_status = market_data_controller.live_adapter.status()
        last_event_at_iso = adapter_status.get("last_event_at")
        last_event_at_ms: int | None = None
        age_seconds: float | None = None
        if last_event_at_iso:
            try:
                parsed = datetime.fromisoformat(str(last_event_at_iso).replace("Z", "+00:00"))
                last_event_at_ms = int(parsed.timestamp() * 1000)
                age_seconds = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())
            except (TypeError, ValueError):
                pass
        return {
            "armed": live_gateway_armed(execution_control),
            "tiger_submission_unlocked": tiger_submission_unlocked(),
            "execution_mode": control["effective_execution_mode"],
            "market_data": {
                "status": adapter_status.get("status"),
                "connected": bool(adapter_status.get("connected")),
                "subscribed_symbols": list(adapter_status.get("subscribed_symbols") or []),
                "subscribe_quotes": bool(adapter_status.get("subscribe_quotes")),
                "subscribe_trades": bool(adapter_status.get("subscribe_trades")),
                "events_received": int(adapter_status.get("events_received") or 0),
            },
            "subscribed_symbols": list(adapter_status.get("subscribed_symbols") or []),
            "last_event_at_ms": last_event_at_ms,
            "age_seconds": age_seconds,
            "message": "broker stream: TigerOpen execution enabled; target account comes from TigerOpen config.",
        }

    app.state.omega_session_factory = session_factory
    app.state.omega_market_data_controller = market_data_controller
    app.state.omega_core_runtime = core_runtime
    if os.environ.get("OMEGA_SERVE_UI", "").strip().lower() in {"1", "true", "yes", "on"}:
        ui_dir = Path(os.environ.get("OMEGA_UI_DIST") or Path(__file__).resolve().parents[2] / "frontend" / "dist")
        if ui_dir.joinpath("index.html").is_file():
            app.mount("/", StaticFiles(directory=ui_dir, html=True), name="omega-ui")
    return app


def _symbol_from_vt_symbol(vt_symbol: str) -> str:
    raw = vt_symbol.strip().upper()
    if "." in raw:
        return raw.rsplit(".", 1)[0]
    return raw


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def _snapshot_account_id(snapshot: dict[str, Any]) -> str | None:
    accounts = list(snapshot.get("accounts") or [])
    if not accounts:
        return None
    value = accounts[0].get("accountid") or accounts[0].get("vt_accountid")
    return str(value) if value else None


def _option_underlying_from_symbol(symbol: str) -> str | None:
    raw = symbol.strip().upper()
    if raw.startswith("O:"):
        raw = raw[2:]
    if len(raw) < 15:
        return None
    marker_index = -1
    for index, char in enumerate(raw):
        if char.isdigit():
            marker_index = index
            break
    if marker_index <= 0:
        return None
    return raw[:marker_index]


def _option_details_from_symbol(symbol: str) -> dict[str, Any]:
    try:
        from .core.option_master import parse_us_option_contract

        contract = parse_us_option_contract(symbol)
    except Exception:
        return {
            "canonical_option_symbol": symbol if symbol.strip().upper().startswith("O:") else None,
            "option_underlying": _option_underlying_from_symbol(symbol),
        }
    days_to_expiry = None
    try:
        expiry_date = datetime.strptime(contract.expiry, "%Y%m%d").date()
        days_to_expiry = max((expiry_date - datetime.now(timezone.utc).date()).days, 0)
    except Exception:
        pass
    return {
        "canonical_option_symbol": contract.canonical_symbol,
        "option_underlying": contract.underlying,
        "option_expiry": contract.expiry,
        "option_put_call": contract.put_call,
        "option_strike": contract.strike,
        "days_to_expiry": days_to_expiry,
    }


def _data_recorder_symbol_still_enabled(status: dict[str, Any], vt_symbol: str) -> bool:
    target = _symbol_from_vt_symbol(vt_symbol)
    enabled_symbols = list(status.get("tick_recordings") or []) + list(status.get("bar_recordings") or [])
    return any(_symbol_from_vt_symbol(str(item)) == target for item in enabled_symbols)


app = create_app(
    os.environ.get("OMEGA_DATABASE_URL"),
    enable_tiger_push=os.environ.get("OMEGA_TIGER_PUSH_ENABLED") == "1",
    enable_data_recorder_live_subscription=os.environ.get("OMEGA_DATA_RECORDER_AUTO_LIVE_SUBSCRIBE") == "1",
)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "omega_quant.api:app",
        host=os.environ.get("OMEGA_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("OMEGA_API_PORT", "8770")),
        reload=False,
    )

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MonitorGroupCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    allocated_capital: float = Field(gt=0)


class MonitorGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    allocated_capital: float | None = Field(default=None, gt=0)
    status: Literal["running", "paused", "stopped", "error", "archived"] | None = None


class MonitorGroupRead(OrmModel):
    id: str
    name: str
    description: str
    allocated_capital: float
    status: str
    created_at: datetime
    updated_at: datetime


class BrokerStreamStatusRead(BaseModel):
    armed: bool
    tiger_submission_unlocked: bool
    execution_mode: Literal["live"]
    market_data: dict[str, Any] = Field(default_factory=dict)
    subscribed_symbols: list[str] = Field(default_factory=list)
    last_event_at_ms: int | None = None
    age_seconds: float | None = None
    message: str


class AccountModeRead(BaseModel):
    account_view_options: list[Literal["virtual", "real_readonly"]]
    default_account_view: Literal["virtual"]
    execution_mode: Literal["live"]
    live_trading: Literal["enabled"]
    real_broker_execution: Literal["tigeropen_sdk"]
    paper_execution_engine: Literal["enabled", "disabled"]
    message: str


class ExecutionControlRead(BaseModel):
    control_plane: Literal["omega_execution_control"]
    requested_execution_mode: Literal["live"]
    effective_execution_mode: Literal["live"]
    paper_engine_enabled: bool
    live_gateway_status: Literal["locked", "unlocked"]
    live_gateway_armed: bool
    session_armed: bool
    real_account_mode: Literal["tigeropen_configured_account"]
    market_data_mode: Literal["live_readonly"]
    broker_execution_gateway: str
    target_account: dict[str, Any]
    unlock_requirements: list[dict[str, Any]]
    centralized_switches: dict[str, Any]
    live_readiness: dict[str, Any]
    message: str


class ExecutionControlUpdate(BaseModel):
    requested_execution_mode: Literal["live", "live_gateway_armed", "paper_only"] | None = None
    paper_engine_enabled: bool | None = None


class ExecutionArmRequest(BaseModel):
    account_suffix: str = Field(min_length=4, max_length=12)
    confirmation: Literal["OMEGA_LIVE_RISK_ACCEPTED"]


class LiveMarketStatusRead(BaseModel):
    status: str
    connected: bool
    read_only: bool
    subscribed_symbols: list[str]
    subscribe_quotes: bool = True
    subscribe_trades: bool = False
    last_event_at: str | None = None
    events_received: int = 0
    events_throttled: int = 0
    throttle_ms: float = 0.0
    message: str | None = None


class DataModeRead(BaseModel):
    data_mode: Literal["live_readonly"]
    available_modes: list[Literal["live_readonly"]]
    execution_mode: Literal["live"]
    live_trading: Literal["enabled"]
    live_readonly_status: dict[str, Any]
    message: str


class DataModeUpdate(BaseModel):
    data_mode: Literal["live_readonly"]


class LiveMarketSubscribeRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list)


class LiveMarketChannelUpdate(BaseModel):
    subscribe_quotes: bool = True
    subscribe_trades: bool = False


class MarketEventRead(BaseModel):
    event_id: str
    source: Literal["live_readonly"]
    symbol: str
    timestamp: datetime
    price: float
    volume: float | None = None
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    data_quality: str
    received_at: datetime
    raw: dict[str, Any] = Field(default_factory=dict)


class RealAccountSummary(BaseModel):
    account_view: Literal["real_readonly"]
    execution_mode: str
    live_trading: str
    available: bool
    read_only: bool
    source: str
    broker_source: str
    account: str | None = None
    queried_at: str | None = None
    real_account_equity: float | None = None
    net_liquidation: float | None = None
    real_cash: float | None = None
    buying_power: float | None = None
    positions_count: int
    orders_count: int
    errors: list[str]
    message: str


class RealPositionRead(BaseModel):
    account: str | None = None
    sec_type: str | None = None
    symbol: str | None = None
    display: str | None = None
    currency: str | None = None
    quantity: float | None = None
    average_cost: float | None = None
    market_price: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    today_pnl: float | None = None
    canonical_option_symbol: str | None = None
    option_underlying: str | None = None
    option_expiry: str | None = None
    option_put_call: str | None = None
    option_strike: float | None = None
    days_to_expiry: int | None = None
    option_moneyness: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    iv: float | None = None
    open_interest: float | None = None
    break_even_price: float | None = None
    option_bid: float | None = None
    option_ask: float | None = None
    greeks_source: str | None = None
    greeks_updated_at: str | None = None
    mark_price: float | None = None
    mark_source: str | None = None
    greeks_engine: str | None = None
    greeks_status: str | None = None
    greeks_error: str | None = None
    source: str
    readonly_badge: str
    raw_context: dict[str, Any] = Field(default_factory=dict)


class RealPositionGroupRead(BaseModel):
    underlying: str
    stock_positions: list[RealPositionRead]
    option_positions: list[RealPositionRead]
    standalone_positions: list[RealPositionRead]
    total_market_value: float
    total_unrealized_pnl: float
    position_count: int
    readonly_badge: str
    option_greeks_exposure: dict[str, Any] = Field(default_factory=dict)


class RealOrderReadonlyRead(BaseModel):
    broker_order_id: str
    symbol: str | None = None
    side: str | None = None
    quantity: float | None = None
    status: str | None = None
    submitted_at: str | None = None
    filled_at: str | None = None
    source: str
    readonly_badge: str
    raw_context: dict[str, Any] = Field(default_factory=dict)


class RealStreamStatusRead(BaseModel):
    source: str
    read_only: bool
    status: str
    connected: bool
    freshness: str
    age_seconds: float | None = None
    updated_at: str | None = None
    sequence: Any | None = None
    counts: dict[str, int]
    market_data: dict[str, Any]
    message: str


class RealStreamDetailRead(BaseModel):
    source: str
    read_only: bool
    status: str
    connected: bool
    freshness: str
    updated_at: str | None = None
    sequence: Any | None = None
    subscriptions: dict[str, Any]
    market_data: dict[str, Any]
    quote_groups: list[dict[str, Any]]
    assets: list[dict[str, Any]]
    positions: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    transactions: list[dict[str, Any]]
    recent_events: list[dict[str, Any]]
    message: str


class RealAccountRiskRead(BaseModel):
    source: str
    read_only: bool
    available: bool
    account: str | None = None
    net_liquidation: float | None = None
    cash_balance: float | None = None
    buying_power: float | None = None
    gross_position_value: float | None = None
    total_today_pl: float | None = None
    total_today_pl_source: str | None = None
    option_market_value_abs: float | None = None
    option_weight: float | None = None
    option_greeks_exposure: dict[str, Any] = Field(default_factory=dict)
    flags: list[str]
    positions: list[dict[str, Any]]
    message: str


class TigerAccountSnapshotRead(BaseModel):
    account_kind: Literal["tigeropen_configured_account"]
    execution_mode: Literal["live"]
    broker_gateway: Literal["tigeropen_sdk"]
    summary: RealAccountSummary
    positions: list[RealPositionRead]
    position_groups: list[RealPositionGroupRead]
    orders: list[RealOrderReadonlyRead]
    stream_status: RealStreamStatusRead
    stream_detail: RealStreamDetailRead
    risk: RealAccountRiskRead
    message: str


class DashboardSummary(BaseModel):
    source: str = "omega_service"
    account: str | None = None
    execution_mode: Literal["live"] = "live"
    broker_gateway: str = "tigeropen_sdk"
    oms_orders_count: int = 0
    oms_active_orders_count: int = 0
    oms_positions_count: int = 0
    oms_trades_count: int = 0
    buying_power: float | None = None
    cash_balance: float | None = None
    gross_position_value: float = 0.0
    core_accounts: list[dict[str, Any]] = Field(default_factory=list)
    core_orders: list[dict[str, Any]] = Field(default_factory=list)
    core_positions: list[dict[str, Any]] = Field(default_factory=list)
    core_trades: list[dict[str, Any]] = Field(default_factory=list)
    risk_manager: dict[str, Any] = Field(default_factory=dict)
    total_equity: float
    free_capital: float
    allocated_capital: float
    used_capital: float
    total_pnl: float
    max_drawdown: float
    active_monitor_groups: int
    risk_warnings: list[str]
    monitor_groups: list[MonitorGroupRead]
    recent_orders: list[Any] = Field(default_factory=list)
    positions: list[Any] = Field(default_factory=list)


class CoreTickUpdate(BaseModel):
    symbol: str = Field(min_length=1)
    last_price: float = Field(gt=0)
    bid_price: float | None = Field(default=None, gt=0)
    ask_price: float | None = Field(default=None, gt=0)
    volume: float = Field(default=0.0, ge=0)
    exchange: str = "SMART"
    timestamp: datetime | None = None


class CorePositionUpdate(BaseModel):
    symbol: str = Field(min_length=1)
    direction: Literal["long", "short"]
    volume: float = Field(gt=0)
    price: float = Field(ge=0)
    exchange: str = "SMART"


class DataRecordingRequest(BaseModel):
    vt_symbol: str = Field(min_length=1)


class RecordedBarCreate(BaseModel):
    symbol: str = Field(min_length=1)
    exchange: str = "SMART"
    interval: str = "1m"
    timestamp: datetime
    open_price: float = Field(gt=0)
    high_price: float = Field(gt=0)
    low_price: float = Field(gt=0)
    close_price: float = Field(gt=0)
    volume: float = Field(default=0.0, ge=0)
    source: str = "live_readonly"


class TickBarAggregationRequest(BaseModel):
    vt_symbol: str = Field(min_length=1)
    interval: str = "1m"
    start: datetime | None = None
    end: datetime | None = None


class BarCsvImportRequest(BaseModel):
    csv_text: str = Field(min_length=1)
    vt_symbol: str | None = Field(default=None, min_length=1)
    interval: str = "1m"
    source: str = "csv_import"


class ManualOrderRequest(BaseModel):
    symbol: str = Field(min_length=1)
    exchange: str = "SMART"
    direction: Literal["buy", "sell", "long", "short"] = "buy"
    order_type: Literal["limit", "market", "stop", "LIMIT", "MARKET", "STOP"] = "limit"
    volume: float = Field(gt=0)
    price: float = Field(default=0.0, ge=0)
    offset: str = "NONE"
    reference: str = "manual"
    gateway_name: str | None = None
    outside_rth: bool = False
    time_in_force: Literal["DAY", "GTC", "day", "gtc"] = "DAY"


class TigerHistoryDownloadRequest(BaseModel):
    vt_symbol: str = Field(min_length=1)
    interval: str = "1m"
    start: datetime | None = None
    end: datetime | None = None
    product: Literal["equity", "option"] = "equity"
    limit: int | None = Field(default=None, ge=1, le=100_000)


class CtaBacktestRunRequest(BaseModel):
    vt_symbol: str = Field(min_length=1)
    interval: str = "1m"
    data_source: Literal["bar", "tick"] = "bar"
    strategy_class: str = "MovingAverageCrossStrategy"
    strategy_setting: dict[str, Any] = Field(default_factory=dict)
    start: datetime | None = None
    end: datetime | None = None
    fast_window: int = Field(default=5, ge=1)
    slow_window: int = Field(default=20, ge=2)
    capital: float = Field(default=100_000.0, gt=0)
    rate: float = Field(default=0.0005, ge=0)
    slippage: float = Field(default=0.01, ge=0)
    size: float = Field(default=1.0, gt=0)
    fee_model: Literal["percent", "tiger_stock", "tiger_option", "custom"] = "percent"
    commission_per_unit: float = Field(default=0.0049, ge=0)
    commission_min_per_order: float = Field(default=0.99, ge=0)
    platform_fee_per_unit: float = Field(default=0.005, ge=0)
    platform_fee_min_per_order: float = Field(default=1.0, ge=0)
    position_sizing_mode: Literal["strategy", "full_capital", "fixed_pct", "fixed_dollars"] = "strategy"
    position_sizing_value: float = Field(default=1.0, ge=0)


class CtaBacktestOptimizeRequest(BaseModel):
    vt_symbol: str = Field(min_length=1)
    interval: str = "1m"
    data_source: Literal["bar", "tick"] = "bar"
    strategy_class: str = "MovingAverageCrossStrategy"
    strategy_setting: dict[str, Any] = Field(default_factory=dict)
    start: datetime | None = None
    end: datetime | None = None
    fast_values: list[int] = Field(default_factory=lambda: [3, 5, 8], min_length=1)
    slow_values: list[int] = Field(default_factory=lambda: [10, 20, 30], min_length=1)
    capital: float = Field(default=100_000.0, gt=0)
    rate: float = Field(default=0.0005, ge=0)
    slippage: float = Field(default=0.01, ge=0)
    size: float = Field(default=1.0, gt=0)
    target: Literal["total_return", "final_equity", "max_drawdown", "trade_count"] = "total_return"

    @field_validator("fast_values", "slow_values")
    @classmethod
    def _positive_windows(cls, values: list[int]) -> list[int]:
        cleaned = sorted({int(value) for value in values})
        if any(value <= 0 for value in cleaned):
            raise ValueError("window values must be positive")
        return cleaned


class CtaOptimizeGenericRequest(BaseModel):
    """Generic optimization over arbitrary strategy parameters."""

    vt_symbol: str = Field(min_length=1)
    interval: str = "1m"
    data_source: Literal["bar", "tick"] = "bar"
    strategy_class: str = Field(min_length=1)
    strategy_setting: dict[str, Any] = Field(default_factory=dict)
    start: datetime | None = None
    end: datetime | None = None
    capital: float = Field(default=100_000.0, gt=0)
    rate: float = Field(default=0.0005, ge=0)
    slippage: float = Field(default=0.01, ge=0)
    size: float = Field(default=1.0, gt=0)
    fee_model: Literal["percent", "tiger_stock", "tiger_option", "custom"] = "percent"
    commission_per_unit: float = Field(default=0.0049, ge=0)
    commission_min_per_order: float = Field(default=0.99, ge=0)
    platform_fee_per_unit: float = Field(default=0.005, ge=0)
    platform_fee_min_per_order: float = Field(default=1.0, ge=0)
    position_sizing_mode: Literal["strategy", "full_capital", "fixed_pct", "fixed_dollars"] = "strategy"
    position_sizing_value: float = Field(default=1.0, ge=0)
    algorithm: Literal["brute_force", "genetic_algorithm"] = "brute_force"
    target: Literal[
        "total_return",
        "final_equity",
        "net_pnl",
        "sharpe_ratio",
        "annual_return",
        "return_drawdown_ratio",
        "max_drawdown",
        "trade_count",
        "win_rate",
    ] = "total_return"
    parameter_values: dict[str, list[Any]] | None = None
    parameter_ranges: dict[str, dict[str, Any]] | None = None
    population_size: int = Field(default=20, ge=4, le=200)
    generations: int = Field(default=10, ge=1, le=100)
    crossover_prob: float = Field(default=0.7, ge=0, le=1)
    mutation_prob: float = Field(default=0.3, ge=0, le=1)


class CtaWalkForwardRequest(BaseModel):
    vt_symbol: str = Field(min_length=1)
    interval: str = "1d"
    data_source: Literal["bar", "tick"] = "bar"
    strategy_class: str = Field(min_length=1)
    strategy_setting: dict[str, Any] = Field(default_factory=dict)
    start: datetime | None = None
    end: datetime | None = None
    capital: float = Field(default=100_000.0, gt=0)
    rate: float = Field(default=0.0005, ge=0)
    slippage: float = Field(default=0.01, ge=0)
    size: float = Field(default=1.0, gt=0)
    fee_model: Literal["percent", "tiger_stock", "tiger_option", "custom"] = "percent"
    commission_per_unit: float = Field(default=0.0049, ge=0)
    commission_min_per_order: float = Field(default=0.99, ge=0)
    platform_fee_per_unit: float = Field(default=0.005, ge=0)
    platform_fee_min_per_order: float = Field(default=1.0, ge=0)
    position_sizing_mode: Literal["strategy", "full_capital", "fixed_pct", "fixed_dollars"] = "strategy"
    position_sizing_value: float = Field(default=1.0, ge=0)
    is_fraction: float = Field(default=0.6, gt=0.1, lt=0.95)
    n_folds: int = Field(default=4, ge=2, le=20)


class CtaMonteCarloRequest(BaseModel):
    vt_symbol: str = Field(min_length=1)
    interval: str = "1d"
    data_source: Literal["bar", "tick"] = "bar"
    strategy_class: str = Field(min_length=1)
    strategy_setting: dict[str, Any] = Field(default_factory=dict)
    start: datetime | None = None
    end: datetime | None = None
    capital: float = Field(default=100_000.0, gt=0)
    rate: float = Field(default=0.0005, ge=0)
    slippage: float = Field(default=0.01, ge=0)
    size: float = Field(default=1.0, gt=0)
    fee_model: Literal["percent", "tiger_stock", "tiger_option", "custom"] = "percent"
    commission_per_unit: float = Field(default=0.0049, ge=0)
    commission_min_per_order: float = Field(default=0.99, ge=0)
    platform_fee_per_unit: float = Field(default=0.005, ge=0)
    platform_fee_min_per_order: float = Field(default=1.0, ge=0)
    position_sizing_mode: Literal["strategy", "full_capital", "fixed_pct", "fixed_dollars"] = "strategy"
    position_sizing_value: float = Field(default=1.0, ge=0)
    n_simulations: int = Field(default=200, ge=10, le=10_000)
    method: Literal["trade_shuffle", "bootstrap"] = "trade_shuffle"


class CtaLiveStrategyCreateRequest(BaseModel):
    strategy_name: str = Field(min_length=1)
    class_name: str = "TickMomentumStrategy"
    vt_symbol: str = Field(min_length=1)
    setting: dict[str, Any] = Field(default_factory=dict)
    gateway_name: str | None = None
    product: Literal["equity", "option", "etf"] = "equity"
    exchange: str = "SMART"


class MonitorGroupCtaStrategyRequest(BaseModel):
    class_name: str = "TickMomentumStrategy"
    symbol: str = Field(default="AAPL", min_length=1)
    exchange: str = "SMART"
    setting: dict[str, Any] = Field(default_factory=dict)
    gateway_name: str | None = None
    start: bool = False


class RiskManagerEvaluateRequest(BaseModel):
    symbol: str = Field(min_length=1)
    side: Literal["buy", "sell", "long", "short"]
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    strategy_id: str | None = None
    now: datetime | None = None


class RiskManagerCancelRecordRequest(BaseModel):
    symbol: str = Field(min_length=1)


class RiskManagerConfigUpdateRequest(BaseModel):
    active: bool | None = None
    order_flow_limit: int | None = Field(default=None, ge=1)
    order_flow_window_seconds: float | None = Field(default=None, gt=0)
    order_size_limit: float | None = Field(default=None, gt=0)
    trade_limit: int | None = Field(default=None, ge=1)
    active_order_limit: int | None = Field(default=None, ge=1)
    cancel_limit_per_symbol: int | None = Field(default=None, ge=1)
    price_band_pct: float | None = Field(default=None, gt=0)
    fat_finger_notional_limit: float | None = Field(default=None, gt=0)
    symbol_exposure_limit: float | None = Field(default=None, gt=0)
    strategy_exposure_limit: float | None = Field(default=None, gt=0)
    account_exposure_limit: float | None = Field(default=None, gt=0)
    trading_timezone: str | None = Field(default=None, min_length=1)
    allowed_sessions: list[tuple[time, time]] | None = None


class AlgoTwapStartRequest(BaseModel):
    symbol: str = Field(min_length=1)
    direction: Literal["long", "short"]
    total_volume: float = Field(gt=0)
    limit_price: float = Field(gt=0)
    gateway_name: str | None = None
    exchange: str = "SMART"
    product: Literal["equity", "option", "etf"] = "equity"
    order_type: str = "LMT"
    interval_ticks: int = Field(default=1, ge=1)
    slices: int | None = Field(default=None, ge=1)
    clip_volume: float | None = Field(default=None, gt=0)
    reference: str = ""


class AlgoIcebergStartRequest(BaseModel):
    symbol: str = Field(min_length=1)
    direction: Literal["long", "short"]
    total_volume: float = Field(gt=0)
    display_volume: float = Field(gt=0)
    limit_price: float = Field(gt=0)
    gateway_name: str | None = None
    exchange: str = "SMART"
    product: Literal["equity", "option", "etf"] = "equity"
    order_type: str = "LMT"
    interval_ticks: int = Field(default=1, ge=1)
    reference: str = ""


class OptionPortfolioConfigureRequest(BaseModel):
    risk_free_rate: float = 0.04
    dividend_yield: float = 0.0


class OptionReadonlySyncRequest(BaseModel):
    risk_free_rate: float = 0.04
    dividend_yield: float = 0.0
    days_forward: float = Field(default=0.0, ge=0)


class OptionContractRequest(BaseModel):
    symbol: str = Field(min_length=1)


class OptionUnderlyingPriceRequest(BaseModel):
    underlying: str = Field(min_length=1)
    price: float = Field(gt=0)


class OptionQuoteUpdateRequest(BaseModel):
    symbol: str = Field(min_length=1)
    last_price: float | None = Field(default=None, gt=0)
    bid_price: float | None = Field(default=None, gt=0)
    ask_price: float | None = Field(default=None, gt=0)
    volume: float | None = Field(default=None, ge=0)


class OptionPositionUpdateRequest(BaseModel):
    symbol: str = Field(min_length=1)
    net_volume: float
    market_value: float = 0.0
    average_price: float | None = Field(default=None, ge=0)


class SpreadLegRequest(BaseModel):
    symbol: str = Field(min_length=1)
    product: Literal["equity", "option", "etf"] = "equity"
    price_multiplier: float
    trading_multiplier: float
    variable_name: str = ""


class SpreadCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    active_leg: str = Field(min_length=1)
    legs: list[SpreadLegRequest] = Field(min_length=2)


class SpreadOrderRequest(BaseModel):
    direction: Literal["long", "short"]
    volume: float = Field(gt=0)
    gateway_name: str | None = None
    price_offset: float = Field(default=0.0, ge=0)
    reference: str = ""

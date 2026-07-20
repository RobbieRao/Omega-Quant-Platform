import type {
  DashboardSummary,
  DataModeState,
  ExecutionControl,
  RateLimitSnapshot,
  RealAccountRisk,
  RealAccountSummary,
  RealStreamDetail,
  RealStreamStatus
} from "../types";

export const defaultSummary: DashboardSummary = {
  source: "main_engine",
  account: null,
  execution_mode: "live",
  broker_gateway: "tigeropen_sdk",
  oms_orders_count: 0,
  oms_active_orders_count: 0,
  oms_positions_count: 0,
  oms_trades_count: 0,
  buying_power: null,
  cash_balance: null,
  gross_position_value: 0,
  core_accounts: [],
  core_orders: [],
  core_positions: [],
  core_trades: [],
  risk_manager: {},
  total_equity: 0,
  free_capital: 0,
  allocated_capital: 0,
  used_capital: 0,
  total_pnl: 0,
  max_drawdown: 0,
  active_monitor_groups: 0,
  risk_warnings: [],
  monitor_groups: [],
  recent_orders: [],
  positions: []
};

export const defaultRealSummary: RealAccountSummary = {
  account_view: "real_readonly",
  execution_mode: "live",
  live_trading: "enabled",
  available: false,
  read_only: true,
  source: "unavailable",
  broker_source: "Tiger/Massive readonly context",
  account: null,
  queried_at: null,
  real_account_equity: null,
  net_liquidation: null,
  real_cash: null,
  buying_power: null,
  positions_count: 0,
  orders_count: 0,
  errors: [],
  message: "当前 TigerOpen 账户数据暂不可用；请确认 Omega 后端与 TigerOpen SDK 已连接。"
};

export const defaultExecutionControl: ExecutionControl = {
  control_plane: "omega_execution_control",
  requested_execution_mode: "live",
  effective_execution_mode: "live",
  paper_engine_enabled: false,
  live_gateway_status: "locked",
  live_gateway_armed: false,
  session_armed: false,
  real_account_mode: "tigeropen_configured_account",
  market_data_mode: "live_readonly",
  broker_execution_gateway: "tigeropen_sdk",
  target_account: {
    configured: false,
    account_masked: null,
    account_kind: "unknown",
    source: "TIGEROPEN_ACCOUNT from local env",
    sdk: "tigerfintech/openapi-python-sdk"
  },
  unlock_requirements: [],
  live_readiness: {
    status: "ready",
    effective_execution_mode: "live",
    requirements_total: 0,
    requirements_satisfied: 0,
    missing: [],
    broker_execution_available: true,
    message: "TigerOpen 执行路径已启用；目标账户由当前 TigerOpen account 配置决定。"
  },
  centralized_switches: {
    paper_engine: false,
    live_gateway: "locked",
    real_account: "tigeropen_configured_account",
    market_data: "live_readonly",
    target_account_kind: "unknown",
    target_account_masked: null
  },
  message: "当前执行模式为 TigerOpen live API；目标账户由 TigerOpen account 配置决定。"
};

export const defaultDataMode: DataModeState = {
  data_mode: "live_readonly",
  available_modes: ["live_readonly"],
  execution_mode: "live",
  live_trading: "enabled",
  live_readonly_status: {
    status: "disconnected",
    connected: false,
    read_only: true,
    subscribed_symbols: [],
    subscribe_quotes: true,
    subscribe_trades: false,
    last_event_at: null,
    message: "Massive 实时行情未连接。"
  },
  message: "Massive 实时行情未连接；策略运行会暂停而非使用模拟数据。"
};

export const defaultRateLimits: RateLimitSnapshot = {
  provider: "massive",
  windowSeconds: 60,
  limitsSource: "Massive private server contract",
  channels: {}
};

export const defaultRealStreamStatus: RealStreamStatus = {
  source: "unavailable",
  read_only: true,
  status: "unavailable",
  connected: false,
  freshness: "unknown",
  age_seconds: null,
  updated_at: null,
  sequence: null,
  counts: {},
  market_data: {},
  message: "真实账户推送流只读上下文暂不可用。"
};

export const defaultRealStreamDetail: RealStreamDetail = {
  source: "unavailable",
  read_only: true,
  status: "unavailable",
  connected: false,
  freshness: "unknown",
  updated_at: null,
  sequence: null,
  subscriptions: {},
  market_data: {},
  quote_groups: [],
  assets: [],
  positions: [],
  orders: [],
  transactions: [],
  recent_events: [],
  message: "真实账户推送流详情暂不可用。"
};

export const defaultRealRisk: RealAccountRisk = {
  source: "unavailable",
  read_only: true,
  available: false,
  account: null,
  net_liquidation: null,
  cash_balance: null,
  buying_power: null,
  gross_position_value: null,
  total_today_pl: null,
  total_today_pl_source: null,
  option_market_value_abs: null,
  option_weight: null,
  option_greeks_exposure: {},
  flags: [],
  positions: [],
  message: "真实账户风险上下文暂不可用。"
};

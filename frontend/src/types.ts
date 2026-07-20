export type MonitorStatus = "running" | "paused" | "stopped" | "error" | "archived";

export type ExecutionMode = "live";

export interface MonitorGroup {
  id: string;
  name: string;
  description: string;
  allocated_capital: number;
  status: MonitorStatus;
  created_at: string;
  updated_at: string;
}

export interface MonitorGroupPayload {
  name: string;
  description?: string;
  allocated_capital: number;
}

export interface MonitorGroupUpdatePayload {
  name?: string;
  description?: string;
  allocated_capital?: number;
  status?: MonitorStatus;
}

export interface BrokerStreamStatus {
  armed: boolean;
  tiger_submission_unlocked: boolean;
  execution_mode: "live";
  market_data: Record<string, unknown>;
  subscribed_symbols: string[];
  last_event_at_ms: number | null;
  age_seconds: number | null;
  message: string;
}

export type DataMode = "live_readonly";

export interface AccountMode {
  account_view_options: string[];
  default_account_view: string;
  execution_mode: "live";
  live_trading: "enabled";
  real_broker_execution: "tigeropen_sdk";
  paper_execution_engine: "enabled" | "disabled";
  message: string;
}

export interface ExecutionControl {
  control_plane: "omega_execution_control";
  requested_execution_mode: "live";
  effective_execution_mode: "live";
  paper_engine_enabled: boolean;
  live_gateway_status: "locked" | "unlocked";
  live_gateway_armed: boolean;
  session_armed: boolean;
  real_account_mode: "tigeropen_configured_account";
  market_data_mode: "live_readonly";
  broker_execution_gateway: string;
  target_account: {
    configured: boolean;
    account_masked: string | null;
    account_kind: string;
    source: string;
    sdk: string;
  };
  unlock_requirements: Array<Record<string, unknown>>;
  centralized_switches: Record<string, unknown>;
  live_readiness: Record<string, unknown>;
  message: string;
}

export interface PlatformCapabilities {
  platform?: string;
  capabilities?: Record<string, unknown> | Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface PortfolioResearchReport { [key: string]: unknown; }
export interface ResearchAudit { [key: string]: unknown; }

export interface LiveMarketStatus {
  status: string;
  connected: boolean;
  read_only: boolean;
  subscribed_symbols: string[];
  subscribe_quotes: boolean;
  subscribe_trades: boolean;
  last_event_at: string | null;
  events_received?: number;
  events_throttled?: number;
  throttle_ms?: number;
  message: string | null;
}

export interface DataModeState {
  data_mode: DataMode;
  available_modes: DataMode[];
  execution_mode: "live";
  live_trading: "enabled";
  live_readonly_status: LiveMarketStatus;
  message: string;
}

export interface MarketEvent {
  event_id: string;
  source: DataMode;
  symbol: string;
  timestamp: string;
  price: number;
  volume: number | null;
  bid: number | null;
  ask: number | null;
  last: number | null;
  data_quality: string;
  received_at: string;
  raw: Record<string, unknown>;
}

export interface RealStreamDetail {
  source: string;
  read_only: boolean;
  status: string;
  connected: boolean;
  freshness: string;
  updated_at: string | null;
  sequence: unknown;
  subscriptions: Record<string, unknown>;
  market_data: Record<string, unknown>;
  quote_groups: Array<Record<string, unknown>>;
  assets: Array<Record<string, unknown>>;
  positions: Array<Record<string, unknown>>;
  orders: Array<Record<string, unknown>>;
  transactions: Array<Record<string, unknown>>;
  recent_events: Array<Record<string, unknown>>;
  message: string;
}

export interface RateLimitChannel {
  key: string;
  label: string;
  rpm: number;
  limitRpm: number;
  remainingRpm: number;
  usagePct: number | null;
  status: "ok" | "warning" | "danger" | "unknown";
}

export interface RateLimitSnapshot {
  provider: string;
  windowSeconds: number;
  limitsSource: string;
  channels: Record<string, RateLimitChannel>;
}

export interface RealAccountSummary {
  account_view: "real_readonly";
  execution_mode: string;
  live_trading: string;
  available: boolean;
  read_only: boolean;
  source: string;
  broker_source: string;
  account: string | null;
  queried_at: string | null;
  real_account_equity: number | null;
  net_liquidation: number | null;
  real_cash: number | null;
  buying_power: number | null;
  positions_count: number;
  orders_count: number;
  errors: string[];
  message: string;
}

export interface RealPosition {
  account: string | null;
  sec_type: string | null;
  symbol: string | null;
  display: string | null;
  currency: string | null;
  quantity: number | null;
  average_cost: number | null;
  market_price: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  today_pnl: number | null;
  canonical_option_symbol: string | null;
  option_underlying: string | null;
  option_expiry: string | null;
  option_put_call: string | null;
  option_strike: number | null;
  days_to_expiry: number | null;
  option_moneyness: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  iv: number | null;
  open_interest: number | null;
  break_even_price: number | null;
  option_bid: number | null;
  option_ask: number | null;
  greeks_source: string | null;
  greeks_updated_at: string | null;
  mark_price: number | null;
  mark_source: string | null;
  greeks_engine: string | null;
  greeks_status: string | null;
  greeks_error: string | null;
  source: string;
  readonly_badge: string;
  raw_context: Record<string, unknown>;
}

export interface RealPositionGroup {
  underlying: string;
  stock_positions: RealPosition[];
  option_positions: RealPosition[];
  standalone_positions: RealPosition[];
  total_market_value: number;
  total_unrealized_pnl: number;
  position_count: number;
  readonly_badge: string;
  option_greeks_exposure: Record<string, unknown>;
}

export interface RealOrderReadonly {
  broker_order_id: string;
  symbol: string | null;
  side: string | null;
  quantity: number | null;
  status: string | null;
  submitted_at: string | null;
  filled_at: string | null;
  source: string;
  readonly_badge: string;
  raw_context: Record<string, unknown>;
}

export interface RealStreamStatus {
  source: string;
  read_only: boolean;
  status: string;
  connected: boolean;
  freshness: string;
  age_seconds: number | null;
  updated_at: string | null;
  sequence: unknown;
  counts: Record<string, number>;
  market_data: Record<string, unknown>;
  message: string;
}

export interface RealAccountRisk {
  source: string;
  read_only: boolean;
  available: boolean;
  account: string | null;
  net_liquidation: number | null;
  cash_balance: number | null;
  buying_power: number | null;
  gross_position_value: number | null;
  total_today_pl: number | null;
  total_today_pl_source: string | null;
  option_market_value_abs: number | null;
  option_weight: number | null;
  option_greeks_exposure: Record<string, unknown>;
  flags: string[];
  positions: Array<Record<string, unknown>>;
  message: string;
}

export interface TigerAccountSnapshot {
  account_kind: "tigeropen_configured_account";
  execution_mode: "live";
  broker_gateway: "tigeropen_sdk";
  summary: RealAccountSummary;
  positions: RealPosition[];
  position_groups: RealPositionGroup[];
  orders: RealOrderReadonly[];
  stream_status: RealStreamStatus;
  stream_detail: RealStreamDetail;
  risk: RealAccountRisk;
  message: string;
}

export interface DashboardSummary {
  source: string;
  account: string | null;
  execution_mode: "live";
  broker_gateway: string;
  oms_orders_count: number;
  oms_active_orders_count: number;
  oms_positions_count: number;
  oms_trades_count: number;
  buying_power: number | null;
  cash_balance: number | null;
  gross_position_value: number;
  core_accounts: CoreAccount[];
  core_orders: CoreOrder[];
  core_positions: CorePosition[];
  core_trades: CoreTrade[];
  risk_manager: Record<string, unknown>;
  total_equity: number;
  free_capital: number;
  allocated_capital: number;
  used_capital: number;
  total_pnl: number;
  max_drawdown: number;
  active_monitor_groups: number;
  risk_warnings: string[];
  monitor_groups: MonitorGroup[];
  recent_orders: unknown[];
  positions: unknown[];
}

export interface CoreRuntimeStatus {
  runtime: string;
  gateway_names: string[];
  gateway_status: Record<string, GatewayStatus>;
  engine_names: string[];
  cta_strategies?: CtaLiveStrategyState[];
  active_algos: number;
  finished_algos: number;
  option_portfolios: string[];
  spreads: string[];
  tick_recordings: string[];
  bar_recordings: string[];
  historical_datasets: number;
  scope: string;
}

export interface GatewayStatus {
  gateway_name: string;
  capabilities: string[];
  connected?: boolean;
  push_connected?: boolean;
  push_error?: string | null;
  subscribed_symbols?: string[];
  provider_status?: Record<string, unknown>;
}

export interface AlgoTemplate {
  name: string;
  display_name: string;
  description: string;
  required_fields: string[];
}

export interface AlgoState {
  algoid: string;
  algo_type: string;
  status: string;
  symbol: string;
  vt_symbol: string;
  direction: string;
  total_volume: number;
  sent_volume: number;
  traded_volume: number;
  remaining_volume: number;
  limit_price: number;
  display_volume?: number | null;
  interval_ticks?: number;
  gateway_name: string;
  child_orderids: string[];
  active_orderids: string[];
  message: string;
  created_at: string;
  updated_at: string;
}

export interface TwapStartPayload {
  symbol: string;
  direction: "long" | "short";
  total_volume: number;
  limit_price: number;
  gateway_name?: string | null;
  exchange?: string;
  product?: "equity" | "option" | "etf";
  order_type?: string;
  interval_ticks?: number;
  slices?: number | null;
  clip_volume?: number | null;
  reference?: string;
}

export interface IcebergStartPayload {
  symbol: string;
  direction: "long" | "short";
  total_volume: number;
  display_volume: number;
  limit_price: number;
  gateway_name?: string | null;
  exchange?: string;
  product?: "equity" | "option" | "etf";
  order_type?: string;
  interval_ticks?: number;
  reference?: string;
}

export interface CoreTickPayload {
  symbol: string;
  last_price: number;
  bid_price?: number | null;
  ask_price?: number | null;
  volume?: number;
  timestamp?: string;
}

export interface CoreOrder {
  vt_orderid: string;
  orderid: string;
  symbol: string;
  vt_symbol: string;
  exchange: string;
  direction: string;
  volume: number;
  price: number | null;
  traded: number;
  status: string;
  datetime: string | null;
  gateway_name: string;
  broker_order_id: string | null;
  reference: string;
  risk_result: RiskManagerEvaluateResult | null;
}

export interface CoreTrade {
  vt_tradeid: string;
  tradeid: string;
  orderid: string;
  symbol: string;
  vt_symbol: string;
  exchange: string;
  direction: string;
  volume: number;
  price: number;
  datetime: string;
  gateway_name: string;
}

export interface CoreAccount {
  vt_accountid: string;
  accountid: string;
  balance: number;
  frozen: number;
  available: number;
  gateway_name: string;
}

export interface CorePosition {
  vt_positionid: string;
  symbol: string;
  vt_symbol: string;
  exchange: string;
  direction: string;
  volume: number;
  price: number;
  pnl: number;
  gateway_name: string;
}

export interface CoreContract {
  vt_symbol: string;
  symbol: string;
  exchange: string;
  name: string;
  product: string;
  size: number;
  pricetick: number;
  currency: string;
  gateway_name: string;
}

export interface DataRecordingStatus {
  tick_recordings: string[];
  bar_recordings: string[];
  engine: string;
  live_readonly_status?: LiveMarketStatus;
}

export interface DataOverviewRow {
  vt_symbol: string;
  data_type: "tick" | "bar";
  interval: string | null;
  count: number;
  start: string | null;
  end: string | null;
  sources?: string[];
}

export interface TickBarAggregationResult {
  vt_symbol: string;
  interval: string;
  source: string;
  ticks_read: number;
  bars_created: number;
  bars_skipped: number;
}

export interface BarCsvImportResult {
  vt_symbol: string;
  interval: string;
  source: string;
  rows_read: number;
  bars_created: number;
  bars_skipped: number;
}

export interface RiskManagerStatus {
  engine: string;
  config: RiskManagerConfig;
  state: {
    order_flow_count: number;
    active_order_count: number;
    trades_today: number;
    cancel_counts_by_symbol: Record<string, number>;
    symbol_exposure: Record<string, number>;
    strategy_exposure: Record<string, number>;
    account_exposure: number;
  };
}

export interface RiskManagerConfig {
  active: boolean;
  order_flow_limit: number;
  order_flow_window_seconds: number;
  order_size_limit: number;
  trade_limit: number;
  active_order_limit: number;
  cancel_limit_per_symbol: number;
  price_band_pct: number;
  fat_finger_notional_limit: number;
  symbol_exposure_limit: number | null;
  strategy_exposure_limit: number | null;
  account_exposure_limit: number | null;
  trading_timezone: string;
  allowed_sessions: Array<{ start: string; end: string }>;
}

export type RiskManagerConfigUpdate = Partial<
  Omit<RiskManagerConfig, "allowed_sessions"> & {
    allowed_sessions: Array<[string, string]>;
  }
>;

export interface RiskManagerEvaluateResult {
  approved: boolean;
  reason: string;
  checks: Array<{ name: string; passed: boolean; message: string }>;
  reference_price: number | null;
  symbol: string;
  side: string;
  quantity: number;
  price: number | null;
  strategy_id: string | null;
}

export interface CtaBacktestResult {
  status: string;
  engine?: string;
  source?: string;
  data_source?: "bar" | "tick";
  strategy_class?: string;
  vt_symbol?: string;
  interval?: string;
  tick_count?: number | null;
  bar_count?: number;
  trade_count?: number;
  closed_trade_count?: number;
  capital?: number;
  final_equity?: number;
  total_return?: number;
  max_drawdown?: number;
  max_drawdown_value?: number;
  sharpe_ratio?: number;
  annual_return?: number;
  return_drawdown_ratio?: number;
  win_rate?: number;
  average_win?: number;
  average_loss?: number;
  gross_pnl?: number;
  net_pnl?: number;
  total_commission?: number;
  total_turnover?: number;
  total_slippage?: number;
  buy_and_hold?: {
    total_return: number;
    final_equity: number;
    entry_price: number;
    exit_price: number;
    shares: number;
    fees: number;
    max_drawdown: number;
  };
  alpha_vs_buy_and_hold?: number;
  pnl_source?: string;
  daily_results?: Array<{
    date: string;
    close_price: number;
    trade_count: number;
    turnover: number;
    commission: number;
    trading_pnl: number;
    holding_pnl: number;
    total_pnl: number;
    net_pnl: number;
    start_pos: number;
    end_pos: number;
    balance: number;
    return_percent: number;
    highlevel: number;
    drawdown: number;
    ddpercent: number;
  }>;
  vnpy_statistics?: {
    start_date?: string;
    end_date?: string;
    capital?: number;
    end_balance?: number;
    total_days?: number;
    profit_days?: number;
    loss_days?: number;
    total_net_pnl?: number;
    daily_net_pnl?: number;
    total_commission?: number;
    daily_commission?: number;
    total_turnover?: number;
    daily_turnover?: number;
    total_trade_count?: number;
    daily_trade_count?: number;
    total_return_percent?: number;
    annual_return_percent?: number;
    daily_return_percent?: number;
    return_std_percent?: number;
    sharpe_ratio?: number;
    max_drawdown?: number;
    max_ddpercent?: number;
    max_drawdown_duration?: number;
    return_drawdown_ratio?: number;
  };
  data_range?: {
    start?: string | null;
    end?: string | null;
    rows?: number;
    source?: string;
  };
  trades?: Array<Record<string, unknown>>;
  equity_curve?: Array<Record<string, unknown>>;
  logs?: string[];
  parameters?: Record<string, unknown>;
  message?: string;
  required_bars?: number;
}

export interface CtaOptimizationResult {
  status: string;
  engine?: string;
  source?: string;
  vt_symbol?: string;
  interval?: string;
  data_source?: "bar" | "tick";
  target?: string;
  tested_combinations?: number;
  completed_runs?: number;
  results: Array<{
    fast_window: number;
    slow_window: number;
    total_return?: number;
    final_equity?: number;
    max_drawdown?: number;
    trade_count?: number;
    result?: CtaBacktestResult;
  }>;
}

export interface CtaStrategyMetadata {
  class_name: string;
  display_name: string;
  author: string;
  parameters: string[];
  variables: string[];
  description: string;
  parameter_defaults?: Record<string, number | string | boolean>;
  category?: string;
  tags?: string[];
}

export interface StrategyFileInfo {
  name: string;
  filename: string;
  bytes: number;
  modified_at: number;
  classes: string[];
  error: string | null;
  category: string;
  tags: string[];
  readonly: boolean;
  source_type: "user" | "builtin";
}

export interface CtaLiveStrategyState {
  strategy_name: string;
  class_name: string;
  vt_symbol: string;
  gateway_name: string;
  product: "equity" | "option" | "etf";
  inited: boolean;
  trading: boolean;
  pos: number;
  active_orderids: string[];
  bar_count?: number;
  last_bar?: {
    vt_symbol: string;
    interval: string;
    datetime: string;
    open_price: number;
    high_price: number;
    low_price: number;
    close_price: number;
    volume: number;
    gateway_name: string;
  } | null;
  parameters: Record<string, unknown>;
  variables: Record<string, unknown>;
  logs: string[];
}

export interface CtaLiveStrategyCreatePayload {
  strategy_name: string;
  class_name: string;
  vt_symbol: string;
  setting?: Record<string, unknown>;
  gateway_name?: string | null;
  product?: "equity" | "option" | "etf";
  exchange?: string;
}

export interface OptionContractResponse {
  symbol: string;
  underlying: string;
  expiry: string;
  strike: number;
  put_call: string;
  product: string;
}

export interface OptionQuoteState {
  symbol: string;
  underlying: string;
  expiry: string;
  strike: number;
  put_call: string;
  last_price: number | null;
  bid_price: number | null;
  ask_price: number | null;
  mark_price: number | null;
  mark_source: string | null;
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  greeks_status: string | null;
  greeks_engine: string | null;
  updated_at: string | null;
}

export interface OptionChainRow {
  strike: number;
  call: OptionQuoteState | null;
  put: OptionQuoteState | null;
}

export interface OptionChain {
  portfolio: string;
  underlying: string;
  expiry: string;
  underlying_price: number | null;
  rows: OptionChainRow[];
}

export interface OptionVolPoint {
  strike: number;
  call_iv: number | null;
  put_iv: number | null;
  mid_iv: number | null;
}

export interface OptionVolCurve {
  portfolio: string;
  underlying: string;
  expiry: string;
  points: OptionVolPoint[];
}

export interface OptionGreeksExposure {
  portfolio: string;
  contracts: number;
  net_contracts: number;
  delta_shares: number;
  gamma_shares: number;
  theta: number;
  vega: number;
  weighted_iv: number | null;
  by_underlying: Record<string, Record<string, number>>;
}

export interface OptionTigerSyncResult {
  portfolio: string;
  source: string;
  synced_positions: number;
  skipped_positions: Array<{ symbol: string; reason: string }>;
  underlyings: string[];
  expiries: string[];
  primary_underlying: string | null;
  primary_expiry: string | null;
  days_forward: number;
  items: Array<Record<string, unknown>>;
  exposure: OptionGreeksExposure;
  message: string;
}

export interface OptionScenarioRow {
  price_move: number;
  iv_shift: number;
  days_forward: number;
  underlying_price: number | null;
  delta_pnl: number;
  gamma_pnl: number;
  vega_pnl: number;
  theta_pnl: number;
  total_pnl: number;
}

export interface OptionScenarioMatrix {
  portfolio: string;
  underlying: string;
  base_underlying_price: number | null;
  base_market_value: number;
  contracts_used: number;
  price_moves: number[];
  iv_shifts: number[];
  days_forward: number;
  rows: OptionScenarioRow[];
}

export interface SpreadLegPayload {
  symbol: string;
  product: "equity" | "option" | "etf";
  price_multiplier: number;
  trading_multiplier: number;
  variable_name?: string;
}

export interface SpreadTick {
  name: string;
  datetime: string;
  bid_price: number | null;
  ask_price: number | null;
  last_price: number | null;
  leg_prices: Record<string, Record<string, number | null>>;
}

export interface SpreadData {
  name: string;
  active_leg: string;
  legs: Array<SpreadLegPayload & { vt_symbol: string }>;
  tick: SpreadTick | null;
  net_position: number;
  leg_positions: Record<string, number>;
  child_orderids: string[];
  updated_at: string;
}

export interface SpreadOrderResult {
  spread_name: string;
  orderids: string[];
  spread: SpreadData | null;
}

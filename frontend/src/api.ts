import type {
  AccountMode,
  BarCsvImportResult,
  BrokerStreamStatus,
  CoreAccount,
  CoreContract,
  AlgoState,
  AlgoTemplate,
  CoreOrder,
  CorePosition,
  CoreRuntimeStatus,
  CoreTickPayload,
  CoreTrade,
  CtaBacktestResult,
  CtaLiveStrategyCreatePayload,
  CtaLiveStrategyState,
  CtaOptimizationResult,
  CtaStrategyMetadata,
  DashboardSummary,
  DataMode,
  DataModeState,
  DataOverviewRow,
  DataRecordingStatus,
  ExecutionControl,
  GatewayStatus,
  IcebergStartPayload,
  LiveMarketStatus,
  MarketEvent,
  MonitorGroup,
  MonitorGroupPayload,
  MonitorGroupUpdatePayload,
  OptionChain,
  OptionContractResponse,
  OptionGreeksExposure,
  OptionQuoteState,
  OptionScenarioMatrix,
  OptionTigerSyncResult,
  OptionVolCurve,
  PlatformCapabilities,
  PortfolioResearchReport,
  RealAccountSummary,
  RealAccountRisk,
  RealOrderReadonly,
  RealPositionGroup,
  RealPosition,
  RealStreamDetail,
  RealStreamStatus,
  RateLimitSnapshot,
  RiskManagerEvaluateResult,
  RiskManagerConfigUpdate,
  RiskManagerStatus,
  ResearchAudit,
  SpreadData,
  SpreadLegPayload,
  SpreadOrderResult,
  StrategyFileInfo,
  TigerAccountSnapshot,
  TickBarAggregationResult,
  TwapStartPayload
} from "./types";

const API_BASE = import.meta.env.VITE_OMEGA_API_BASE ?? "";

function getStoredToken(): string {
  try { return localStorage.getItem("omega_api_token") ?? ""; } catch { return ""; }
}
export function setStoredToken(value: string) {
  try {
    if (value) localStorage.setItem("omega_api_token", value);
    else localStorage.removeItem("omega_api_token");
  } catch { /* ignore */ }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getStoredToken();
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(init?.headers as Record<string, string> ?? {}) };
  if (token) headers["X-Omega-Token"] = token;
  const response = await fetch(`${API_BASE}${path}`, {
    headers,
    ...init,
  });
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = body?.detail ?? JSON.stringify(body);
    } catch {
      detail = await response.text();
    }
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }
  if (response.status === 204) {
    return undefined as unknown as T;
  }
  return (await response.json()) as T;
}

async function requestText(path: string, init?: RequestInit): Promise<string> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.text();
}

export const api = {
  accountMode: () => request<AccountMode>("/api/account/mode"),
  executionControl: () => request<ExecutionControl>("/api/execution/control"),
  updateExecutionControl: (payload: { requested_execution_mode?: "live"; paper_engine_enabled?: boolean }) =>
    request<ExecutionControl>("/api/execution/control", { method: "POST", body: JSON.stringify(payload) }),
  armExecution: (accountSuffix: string, confirmation: string) =>
    request<ExecutionControl>("/api/execution/arm", {
      method: "POST",
      body: JSON.stringify({ account_suffix: accountSuffix, confirmation })
    }),
  disarmExecution: () => request<ExecutionControl>("/api/execution/disarm", { method: "POST" }),
  platformCapabilities: () => request<PlatformCapabilities>("/api/platform/capabilities"),
  portfolioResearch: () => request<PortfolioResearchReport>("/api/research/portfolio-report"),
  researchAudit: (limit = 25) => request<ResearchAudit>(`/api/research/audit?limit=${encodeURIComponent(String(limit))}`),
  dataMode: () => request<DataModeState>("/api/data-mode"),
  setDataMode: (dataMode: DataMode) =>
    request<DataModeState>("/api/data-mode", { method: "POST", body: JSON.stringify({ data_mode: dataMode }) }),
  marketSnapshot: (symbol = "AAPL") => request<MarketEvent>(`/api/market/snapshot?symbol=${encodeURIComponent(symbol)}`),
  marketSessionStatus: () =>
    request<{
      now_utc: string;
      now_et: string;
      session: "rth" | "pre_market" | "after_hours" | "closed_overnight" | "closed_weekend" | "closed_holiday";
      session_label: string;
      is_holiday: boolean;
      is_weekend: boolean;
      next_session: string | null;
      next_session_at_et: string | null;
      next_session_at_utc: string | null;
      hint: string;
    }>("/api/market/session-status"),
  quotePermissions: (refresh = false) =>
    request<{
      permissions: { name: string; expire_at?: number }[];
      has_us_realtime: boolean;
      has_us_overnight: boolean;
      has_us_depth: boolean;
      delayed_fallback_available: boolean;
      hint: string;
    }>(`/api/market/quote-permissions${refresh ? "?refresh=true" : ""}`),
  liveMarketStatus: () => request<LiveMarketStatus>("/api/market/live/status"),
  marketRateLimits: () => request<RateLimitSnapshot>("/api/market/rate-limits"),
  setLiveMarketChannels: (subscribeQuotes: boolean, subscribeTrades: boolean) =>
    request<LiveMarketStatus>("/api/market/live/channels", {
      method: "POST",
      body: JSON.stringify({ subscribe_quotes: subscribeQuotes, subscribe_trades: subscribeTrades })
    }),
  subscribeLiveMarket: (symbols: string[]) =>
    request<LiveMarketStatus>("/api/market/live/subscribe", { method: "POST", body: JSON.stringify({ symbols }) }),
  unsubscribeLiveMarket: (symbols: string[]) =>
    request<LiveMarketStatus>("/api/market/live/unsubscribe", { method: "POST", body: JSON.stringify({ symbols }) }),
  dashboardSummary: () => request<DashboardSummary>("/api/dashboard/summary"),
  tigerAccountSnapshot: () => request<TigerAccountSnapshot>("/api/tiger/account/snapshot"),
  realAccountSummary: () => request<RealAccountSummary>("/api/tiger/account/summary"),
  realPositions: () => request<RealPosition[]>("/api/tiger/account/positions"),
  realPositionGroups: () => request<RealPositionGroup[]>("/api/tiger/account/position-groups"),
  realOrdersReadonly: () => request<RealOrderReadonly[]>("/api/tiger/account/orders"),
  realStreamStatus: () => request<RealStreamStatus>("/api/tiger/account/stream-status"),
  realStreamDetail: () => request<RealStreamDetail>("/api/tiger/account/stream-detail"),
  realAccountRisk: () => request<RealAccountRisk>("/api/tiger/account/risk"),
  brokerStreamStatus: () => request<BrokerStreamStatus>("/api/broker/stream/status"),
  monitorGroups: () => request<MonitorGroup[]>("/api/monitor-groups"),
  createMonitorGroup: (payload: MonitorGroupPayload) =>
    request<MonitorGroup>("/api/monitor-groups", { method: "POST", body: JSON.stringify(payload) }),
  updateMonitorGroup: (id: string, payload: MonitorGroupUpdatePayload) =>
    request<MonitorGroup>(`/api/monitor-groups/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteMonitorGroup: (id: string) => request<void>(`/api/monitor-groups/${id}`, { method: "DELETE" }),
  pauseGroup: (id: string) => request<MonitorGroup>(`/api/monitor-groups/${id}/pause`, { method: "POST" }),
  resumeGroup: (id: string) => request<MonitorGroup>(`/api/monitor-groups/${id}/resume`, { method: "POST" }),
  stopGroup: (id: string) => request<MonitorGroup>(`/api/monitor-groups/${id}/stop`, { method: "POST" }),
  registerMonitorGroupCtaStrategy: (
    monitorGroupId: string,
    payload: { class_name: string; symbol: string; exchange?: string; setting?: Record<string, unknown>; gateway_name?: string | null; start?: boolean }
  ) =>
    request<CtaLiveStrategyState>(`/api/monitor-groups/${encodeURIComponent(monitorGroupId)}/cta-strategy`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  coreStatus: () => request<CoreRuntimeStatus>("/api/core/status"),
  gateways: () => request<GatewayStatus[]>("/api/gateways"),
  coreOrders: () => request<CoreOrder[]>("/api/core/orders"),
  submitManualOrder: (payload: {
    symbol: string;
    exchange?: string;
    direction: "buy" | "sell" | "long" | "short";
    order_type?: "limit" | "market" | "stop";
    volume: number;
    price?: number;
    offset?: string;
    reference?: string;
    gateway_name?: string | null;
    outside_rth?: boolean;
    time_in_force?: "DAY" | "GTC";
  }) => request<{ vt_orderid: string; symbol: string; exchange: string; direction: string; type: string; volume: number; price: number; offset: string; gateway_name: string; outside_rth?: boolean; time_in_force?: string }>(
    "/api/core/orders/manual",
    { method: "POST", body: JSON.stringify(payload) }
  ),
  cancelCoreOrder: (orderid: string) =>
    request<{ cancel_requested: boolean; orderid: string; vt_orderid: string; broker_order_id: string | null; cancel_request_id: string; orders: CoreOrder[] }>(
      `/api/core/orders/${encodeURIComponent(orderid)}/cancel`,
      { method: "POST", body: JSON.stringify({}) }
    ),
  cancelAllCoreOrders: (symbol?: string) =>
    request<{ requested: number; cancelled: { orderid: string; symbol: string }[]; errors: { orderid: string; symbol: string; error: string }[]; remaining: CoreOrder[] }>(
      `/api/core/orders/cancel-all${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`,
      { method: "POST", body: JSON.stringify({}) }
    ),
  coreTrades: () => request<CoreTrade[]>("/api/core/trades"),
  coreAccounts: () => request<CoreAccount[]>("/api/core/accounts"),
  corePositions: () => request<CorePosition[]>("/api/core/positions"),
  coreContracts: () => request<CoreContract[]>("/api/core/contracts"),
  queryCoreContract: (symbol: string, product?: "equity" | "option" | "etf") => {
    const params = new URLSearchParams({ symbol });
    if (product) params.set("product", product);
    return request<{ gateway_name: string; contract: CoreContract | null }>(`/api/core/contracts/query?${params.toString()}`, {
      method: "POST",
      body: JSON.stringify({})
    });
  },
  queryTigerOpenSnapshot: () =>
    request<{ gateway_name: string; accounts: CoreAccount[]; positions: CorePosition[]; orders: CoreOrder[]; trades: CoreTrade[] }>(
      "/api/core/tigeropen/query",
      { method: "POST", body: JSON.stringify({}) }
    ),
  coreTick: (payload: CoreTickPayload) =>
    request<Record<string, unknown>>("/api/core/ticks", { method: "POST", body: JSON.stringify(payload) }),
  dataRecordings: () => request<DataRecordingStatus>("/api/data-recorder/recordings"),
  addTickRecording: (vtSymbol: string) =>
    request<DataRecordingStatus>("/api/data-recorder/tick-recordings", {
      method: "POST",
      body: JSON.stringify({ vt_symbol: vtSymbol })
    }),
  removeTickRecording: (vtSymbol: string) =>
    request<DataRecordingStatus>(`/api/data-recorder/tick-recordings/${encodeURIComponent(vtSymbol)}`, {
      method: "DELETE"
    }),
  addBarRecording: (vtSymbol: string) =>
    request<DataRecordingStatus>("/api/data-recorder/bar-recordings", {
      method: "POST",
      body: JSON.stringify({ vt_symbol: vtSymbol })
    }),
  removeBarRecording: (vtSymbol: string) =>
    request<DataRecordingStatus>(`/api/data-recorder/bar-recordings/${encodeURIComponent(vtSymbol)}`, {
      method: "DELETE"
    }),
  recordBar: (payload: {
    symbol: string;
    timestamp: string;
    open_price: number;
    high_price: number;
    low_price: number;
    close_price: number;
    volume?: number;
    interval?: string;
    exchange?: string;
    source?: string;
  }) => request<Record<string, unknown>>("/api/data-recorder/bars", { method: "POST", body: JSON.stringify(payload) }),
  dataOverview: () => request<DataOverviewRow[]>("/api/data-manager/overview?include_sources=true"),
  fetchBars: (vt_symbol: string, interval = "1d", start?: string | null, end?: string | null, limit = 5000) => {
    const params = new URLSearchParams({ vt_symbol, interval, limit: String(limit) });
    if (start) params.set("start", start);
    if (end) params.set("end", end);
    return request<Array<{ timestamp: string | null; open: number; high: number; low: number; close: number; volume: number }>>(
      `/api/data-manager/bars?${params.toString()}`
    );
  },
  aggregateTicksToBars: (payload: { vt_symbol: string; interval?: string; start?: string | null; end?: string | null }) =>
    request<TickBarAggregationResult>("/api/data-manager/aggregate-ticks", { method: "POST", body: JSON.stringify(payload) }),
  importBarsCsv: (payload: { csv_text: string; vt_symbol?: string | null; interval?: string; source?: string }) =>
    request<BarCsvImportResult>("/api/data-manager/bars/import", { method: "POST", body: JSON.stringify(payload) }),
  exportBarsCsv: (payload: { vt_symbol: string; interval?: string; start?: string | null; end?: string | null }) => {
    const params = new URLSearchParams({
      vt_symbol: payload.vt_symbol,
      interval: payload.interval ?? "1m"
    });
    if (payload.start) params.set("start", payload.start);
    if (payload.end) params.set("end", payload.end);
    return requestText(`/api/data-manager/bars/export?${params.toString()}`);
  },
  deleteSymbolData: (vtSymbol: string) =>
    request<{ ticks_deleted: number; bars_deleted: number }>(`/api/data-manager/symbols/${encodeURIComponent(vtSymbol)}`, { method: "DELETE" }),
  listTasks: () =>
    request<Array<{ task_id: string; kind: string; status: string; progress: number; stage: string; started_at: number | null; finished_at: number | null; error: string | null; meta: Record<string, unknown> }>>(
      "/api/tasks"
    ),
  getTask: (taskId: string) =>
    request<{ task_id: string; kind: string; status: string; progress: number; stage: string; started_at: number | null; finished_at: number | null; result: unknown; error: string | null; meta: Record<string, unknown> }>(
      `/api/tasks/${encodeURIComponent(taskId)}`
    ),
  cancelTask: (taskId: string) => request<{ cancelled: boolean; task_id: string }>(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" }),
  walkForwardCta: (payload: { vt_symbol: string; interval?: string; data_source?: "bar" | "tick"; strategy_class: string; strategy_setting?: Record<string, unknown>; start?: string | null; end?: string | null; capital?: number; rate?: number; slippage?: number; size?: number; fee_model?: "percent" | "tiger_stock" | "tiger_option" | "custom"; commission_per_unit?: number; commission_min_per_order?: number; platform_fee_per_unit?: number; platform_fee_min_per_order?: number; position_sizing_mode?: "strategy" | "full_capital" | "fixed_pct" | "fixed_dollars"; position_sizing_value?: number; is_fraction?: number; n_folds?: number }) =>
    request<{ algorithm: string; n_folds: number; is_fraction: number; results: Array<{ fold: number; is_start: string; is_end: string; oos_start: string; oos_end: string; is_total_return: number | null; is_sharpe: number | null; is_max_drawdown: number | null; is_trade_count: number; oos_total_return: number | null; oos_sharpe: number | null; oos_max_drawdown: number | null; oos_trade_count: number }> }>(
      "/api/cta-backtester/walk-forward",
      { method: "POST", body: JSON.stringify(payload) }
    ),
  monteCarloCta: (payload: { vt_symbol: string; interval?: string; data_source?: "bar" | "tick"; strategy_class: string; strategy_setting?: Record<string, unknown>; start?: string | null; end?: string | null; capital?: number; rate?: number; slippage?: number; size?: number; fee_model?: "percent" | "tiger_stock" | "tiger_option" | "custom"; commission_per_unit?: number; commission_min_per_order?: number; platform_fee_per_unit?: number; platform_fee_min_per_order?: number; position_sizing_mode?: "strategy" | "full_capital" | "fixed_pct" | "fixed_dollars"; position_sizing_value?: number; n_simulations?: number; method?: "trade_shuffle" | "bootstrap" }) =>
    request<{ algorithm: string; method: string; n_simulations: number; n_trades: number; base_final_equity: number; base_max_drawdown: number; final_equity_stats: { mean: number; median: number; stdev: number; p05: number; p25: number; p75: number; p95: number; min: number; max: number }; max_drawdown_stats: { mean: number; median: number; p05: number; p95: number; worst: number; best: number }; histogram: { final_equity: number[]; max_drawdown: number[] } }>(
      "/api/cta-backtester/monte-carlo",
      { method: "POST", body: JSON.stringify(payload) }
    ),
  optimizeCtaGenericAsync: (payload: Record<string, unknown>) =>
    request<{ task_id: string; kind: string; status: string }>(
      "/api/cta-backtester/optimize-generic-async",
      { method: "POST", body: JSON.stringify(payload) }
    ),
  downloadTigerHistoryAsync: (payload: { vt_symbol: string; interval?: string; start?: string | null; end?: string | null; product?: "equity" | "option"; limit?: number | null }) =>
    request<{ task_id: string; kind: string; status: string }>(
      "/api/data-manager/download-tigeropen-async",
      { method: "POST", body: JSON.stringify(payload) }
    ),
  downloadTigerHistory: (payload: { vt_symbol: string; interval?: string; start?: string | null; end?: string | null; product?: "equity" | "option"; limit?: number | null }) =>
    request<{ vt_symbol: string; interval: string; product: string; rows_received: number; bars_inserted: number; bars_skipped: number; start: string | null; end: string | null; source: string }>(
      "/api/data-manager/download-tigeropen",
      { method: "POST", body: JSON.stringify(payload) }
    ),
  runCtaBacktest: (payload: {
    vt_symbol: string;
    interval?: string;
    data_source?: "bar" | "tick";
    strategy_class?: string;
    strategy_setting?: Record<string, unknown>;
    start?: string | null;
    end?: string | null;
    fast_window?: number;
    slow_window?: number;
    capital?: number;
    rate?: number;
    slippage?: number;
    size?: number;
    fee_model?: "percent" | "tiger_stock" | "tiger_option" | "custom";
    commission_per_unit?: number;
    commission_min_per_order?: number;
    platform_fee_per_unit?: number;
    platform_fee_min_per_order?: number;
    position_sizing_mode?: "strategy" | "full_capital" | "fixed_pct" | "fixed_dollars";
    position_sizing_value?: number;
  }) => request<CtaBacktestResult>("/api/cta-backtester/run", { method: "POST", body: JSON.stringify(payload) }),
  feePresets: () =>
    request<{ presets: Record<string, Record<string, number>>; explainer: Record<string, string> }>("/api/cta-backtester/fee-presets"),
  ctaStrategies: () => request<CtaStrategyMetadata[]>("/api/cta-backtester/strategies"),
  ctaLiveTemplates: () => request<CtaStrategyMetadata[]>("/api/cta-strategy/templates"),
  ctaLiveStrategies: () => request<CtaLiveStrategyState[]>("/api/cta-strategy/strategies"),
  addCtaLiveStrategy: (payload: CtaLiveStrategyCreatePayload) =>
    request<CtaLiveStrategyState>("/api/cta-strategy/strategies", { method: "POST", body: JSON.stringify(payload) }),
  startCtaLiveStrategy: (strategyName: string) =>
    request<CtaLiveStrategyState>(`/api/cta-strategy/strategies/${encodeURIComponent(strategyName)}/start`, { method: "POST" }),
  stopCtaLiveStrategy: (strategyName: string) =>
    request<CtaLiveStrategyState>(`/api/cta-strategy/strategies/${encodeURIComponent(strategyName)}/stop`, { method: "POST" }),
  removeCtaLiveStrategy: (strategyName: string) =>
    request<{ removed: boolean; strategy_name: string }>(`/api/cta-strategy/strategies/${encodeURIComponent(strategyName)}`, { method: "DELETE" }),
  editCtaLiveStrategy: (strategyName: string, setting: Record<string, unknown>) =>
    request<CtaLiveStrategyState>(
      `/api/cta-strategy/strategies/${encodeURIComponent(strategyName)}`,
      { method: "PATCH", body: JSON.stringify({ setting }) }
    ),
  ctaStrategyFiles: () =>
    request<StrategyFileInfo[]>("/api/cta-strategy/files"),
  readCtaStrategyFile: (name: string) =>
    request<{ name: string; content: string }>(`/api/cta-strategy/files/${encodeURIComponent(name)}`),
  writeCtaStrategyFile: (name: string, content: string) =>
    request<StrategyFileInfo>(
      `/api/cta-strategy/files/${encodeURIComponent(name)}`,
      { method: "PUT", body: JSON.stringify({ content }) }
    ),
  deleteCtaStrategyFile: (name: string) =>
    request<{ deleted: boolean; name: string }>(`/api/cta-strategy/files/${encodeURIComponent(name)}`, { method: "DELETE" }),
  reloadCtaStrategyFiles: () =>
    request<StrategyFileInfo[]>(
      "/api/cta-strategy/files/reload",
      { method: "POST" }
    ),
  ctaStrategyBuiltins: () =>
    request<StrategyFileInfo[]>("/api/cta-strategy/builtins"),
  readCtaStrategyBuiltin: (className: string) =>
    request<{ name: string; content: string; readonly: boolean; source_type: string }>(
      `/api/cta-strategy/builtins/${encodeURIComponent(className)}`,
    ),
  optimizeCtaGeneric: (payload: {
    vt_symbol: string;
    interval?: string;
    data_source?: "bar" | "tick";
    strategy_class: string;
    strategy_setting?: Record<string, unknown>;
    start?: string | null;
    end?: string | null;
    capital?: number;
    rate?: number;
    slippage?: number;
    size?: number;
    algorithm: "brute_force" | "genetic_algorithm";
    target: "total_return" | "final_equity" | "net_pnl" | "sharpe_ratio" | "annual_return" | "return_drawdown_ratio" | "max_drawdown" | "trade_count" | "win_rate";
    parameter_values?: Record<string, Array<number | string>>;
    parameter_ranges?: Record<string, { low: number; high: number; step?: number; type?: "int" | "float" }>;
    population_size?: number;
    generations?: number;
    crossover_prob?: number;
    mutation_prob?: number;
  }) => request<{
    algorithm: string;
    target: string;
    tested_combinations: number;
    completed_runs?: number;
    population_size?: number;
    generations?: number;
    parameter_names?: string[];
    history?: Array<{ generation: number; best_parameters?: Record<string, number>; best_score: number }>;
    results: Array<{
      parameters: Record<string, number>;
      total_return?: number;
      final_equity?: number;
      max_drawdown?: number;
      trade_count?: number;
      net_pnl?: number;
      sharpe_ratio?: number;
      annual_return?: number;
      return_drawdown_ratio?: number;
      win_rate?: number;
      result: Record<string, unknown>;
    }>;
  }>("/api/cta-backtester/optimize-generic", { method: "POST", body: JSON.stringify(payload) }),
  optimizeCtaBacktest: (payload: {
    vt_symbol: string;
    interval?: string;
    data_source?: "bar" | "tick";
    strategy_class?: string;
    strategy_setting?: Record<string, unknown>;
    start?: string | null;
    end?: string | null;
    fast_values?: number[];
    slow_values?: number[];
    capital?: number;
    rate?: number;
    slippage?: number;
    size?: number;
    target?: "total_return" | "final_equity" | "max_drawdown" | "trade_count";
  }) => request<CtaOptimizationResult>("/api/cta-backtester/optimize", { method: "POST", body: JSON.stringify(payload) }),
  riskManagerStatus: () => request<RiskManagerStatus>("/api/risk-manager/status"),
  updateRiskManagerConfig: (payload: RiskManagerConfigUpdate) =>
    request<RiskManagerStatus>("/api/risk-manager/config", { method: "PATCH", body: JSON.stringify(payload) }),
  evaluateRiskOrder: (payload: { symbol: string; side: "buy" | "sell" | "long" | "short"; quantity: number; price: number; strategy_id?: string | null; now?: string | null }) =>
    request<RiskManagerEvaluateResult>("/api/risk-manager/evaluate", { method: "POST", body: JSON.stringify(payload) }),
  recordRiskCancel: (symbol: string) =>
    request<RiskManagerStatus>("/api/risk-manager/cancels", { method: "POST", body: JSON.stringify({ symbol }) }),
  algoTemplates: () => request<AlgoTemplate[]>("/api/algo/templates"),
  activeAlgos: () => request<AlgoState[]>("/api/algo/active"),
  finishedAlgos: () => request<AlgoState[]>("/api/algo/finished"),
  startTwap: (payload: TwapStartPayload) =>
    request<AlgoState>("/api/algo/twap", { method: "POST", body: JSON.stringify(payload) }),
  startIceberg: (payload: IcebergStartPayload) =>
    request<AlgoState>("/api/algo/iceberg", { method: "POST", body: JSON.stringify(payload) }),
  pauseAlgo: (algoid: string) => request<AlgoState>(`/api/algo/${encodeURIComponent(algoid)}/pause`, { method: "POST" }),
  resumeAlgo: (algoid: string) => request<AlgoState>(`/api/algo/${encodeURIComponent(algoid)}/resume`, { method: "POST" }),
  stopAlgo: (algoid: string) => request<AlgoState>(`/api/algo/${encodeURIComponent(algoid)}/stop`, { method: "POST" }),
  configureOptionPortfolio: (portfolio: string, payload: { risk_free_rate?: number; dividend_yield?: number } = {}) =>
    request<Record<string, unknown>>(`/api/option-master/portfolios/${encodeURIComponent(portfolio)}/configure`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  syncOptionPortfolioFromTiger: (portfolio: string, payload: { risk_free_rate?: number; dividend_yield?: number; days_forward?: number } = {}) =>
    request<OptionTigerSyncResult>(`/api/option-master/portfolios/${encodeURIComponent(portfolio)}/sync-tiger-positions`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  addOptionContract: (portfolio: string, symbol: string) =>
    request<OptionContractResponse>(`/api/option-master/portfolios/${encodeURIComponent(portfolio)}/contracts`, {
      method: "POST",
      body: JSON.stringify({ symbol })
    }),
  updateOptionUnderlying: (portfolio: string, underlying: string, price: number) =>
    request<Record<string, unknown>>(`/api/option-master/portfolios/${encodeURIComponent(portfolio)}/underlying-price`, {
      method: "POST",
      body: JSON.stringify({ underlying, price })
    }),
  updateOptionQuote: (
    portfolio: string,
    payload: { symbol: string; last_price?: number | null; bid_price?: number | null; ask_price?: number | null; volume?: number | null }
  ) =>
    request<OptionQuoteState>(`/api/option-master/portfolios/${encodeURIComponent(portfolio)}/quotes`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateOptionPosition: (
    portfolio: string,
    payload: { symbol: string; net_volume: number; market_value?: number; average_price?: number | null }
  ) =>
    request<Record<string, unknown>>(`/api/option-master/portfolios/${encodeURIComponent(portfolio)}/positions`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  optionChain: (portfolio: string, underlying: string, expiry: string) =>
    request<OptionChain>(
      `/api/option-master/portfolios/${encodeURIComponent(portfolio)}/chain?underlying=${encodeURIComponent(underlying)}&expiry=${encodeURIComponent(expiry)}`
    ),
  optionVolCurve: (portfolio: string, underlying: string, expiry: string) =>
    request<OptionVolCurve>(
      `/api/option-master/portfolios/${encodeURIComponent(portfolio)}/volatility-curve?underlying=${encodeURIComponent(underlying)}&expiry=${encodeURIComponent(expiry)}`
    ),
  optionGreeksExposure: (portfolio: string) =>
    request<OptionGreeksExposure>(`/api/option-master/portfolios/${encodeURIComponent(portfolio)}/greeks-exposure`),
  optionScenarioMatrix: (portfolio: string, underlying: string, daysForward = 0) =>
    request<OptionScenarioMatrix>(
      `/api/option-master/portfolios/${encodeURIComponent(portfolio)}/scenario-matrix?underlying=${encodeURIComponent(underlying)}&days_forward=${encodeURIComponent(String(daysForward))}`
    ),
  createSpread: (payload: { name: string; active_leg: string; legs: SpreadLegPayload[] }) =>
    request<SpreadData>("/api/spread-trading/spreads", { method: "POST", body: JSON.stringify(payload) }),
  sendSpreadOrder: (name: string, payload: { direction: "long" | "short"; volume: number; price_offset?: number; reference?: string; gateway_name?: string | null }) =>
    request<SpreadOrderResult>(`/api/spread-trading/spreads/${encodeURIComponent(name)}/orders`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  spreads: () => request<SpreadData[]>("/api/spread-trading/spreads"),
  spread: (name: string) => request<SpreadData>(`/api/spread-trading/spreads/${encodeURIComponent(name)}`)
};

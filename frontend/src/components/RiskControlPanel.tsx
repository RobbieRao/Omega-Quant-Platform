import { Activity, RefreshCw, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { IconButton, Info, Metric } from "./primitives";
import type { CoreOrder, CoreTrade, RiskManagerConfigUpdate, RiskManagerEvaluateResult, RiskManagerStatus } from "../types";
import { optionalDecimal, textValue } from "../utils/format";

export function RiskControlPanel({
  status,
  result,
  coreOrders,
  coreTrades,
  busy,
  onSeed,
  onEvaluateOrder,
  onUpdateConfig,
  onCancelOrder
}: {
  status: RiskManagerStatus | null;
  result: RiskManagerEvaluateResult | null;
  coreOrders: CoreOrder[];
  coreTrades: CoreTrade[];
  busy: boolean;
  onSeed: () => void;
  onEvaluateOrder: (payload: { symbol: string; side: "buy" | "sell" | "long" | "short"; quantity: number; price: number; strategy_id?: string | null; now?: string | null }) => void;
  onUpdateConfig: (payload: RiskManagerConfigUpdate) => void;
  onCancelOrder: (orderid: string) => void;
}) {
  const state = status?.state;
  const config = status?.config;
  const [draft, setDraft] = useState({
    order_flow_limit: "20",
    order_flow_window_seconds: "1",
    order_size_limit: "10000",
    trade_limit: "1000",
    active_order_limit: "100",
    cancel_limit_per_symbol: "50",
    price_band_pct: "0.1",
    fat_finger_notional_limit: "250000",
    symbol_exposure_limit: "",
    strategy_exposure_limit: "",
    account_exposure_limit: "",
    trading_timezone: "America/New_York",
    session_start: "09:30",
    session_end: "16:00",
    active: true
  });
  const [probe, setProbe] = useState({
    symbol: "",
    side: "buy" as "buy" | "sell" | "long" | "short",
    quantity: "1",
    price: "",
    strategy_id: "manual_risk_probe"
  });
  const recentOrders = coreOrders.slice(-12).reverse();
  const recentTrades = coreTrades.slice(-10).reverse();

  function loadCurrentConfig() {
    if (!config) return;
    setDraft({
      order_flow_limit: String(config.order_flow_limit),
      order_flow_window_seconds: String(config.order_flow_window_seconds),
      order_size_limit: String(config.order_size_limit),
      trade_limit: String(config.trade_limit),
      active_order_limit: String(config.active_order_limit),
      cancel_limit_per_symbol: String(config.cancel_limit_per_symbol),
      price_band_pct: String(config.price_band_pct),
      fat_finger_notional_limit: String(config.fat_finger_notional_limit),
      symbol_exposure_limit: config.symbol_exposure_limit == null ? "" : String(config.symbol_exposure_limit),
      strategy_exposure_limit: config.strategy_exposure_limit == null ? "" : String(config.strategy_exposure_limit),
      account_exposure_limit: config.account_exposure_limit == null ? "" : String(config.account_exposure_limit),
      trading_timezone: config.trading_timezone || "America/New_York",
      session_start: config.allowed_sessions[0]?.start ?? "09:30",
      session_end: config.allowed_sessions[0]?.end ?? "16:00",
      active: config.active
    });
  }

  function submitConfig() {
    onUpdateConfig({
      active: draft.active,
      order_flow_limit: Number(draft.order_flow_limit),
      order_flow_window_seconds: Number(draft.order_flow_window_seconds),
      order_size_limit: Number(draft.order_size_limit),
      trade_limit: Number(draft.trade_limit),
      active_order_limit: Number(draft.active_order_limit),
      cancel_limit_per_symbol: Number(draft.cancel_limit_per_symbol),
      price_band_pct: Number(draft.price_band_pct),
      fat_finger_notional_limit: Number(draft.fat_finger_notional_limit),
      symbol_exposure_limit: numericOrNull(draft.symbol_exposure_limit),
      strategy_exposure_limit: numericOrNull(draft.strategy_exposure_limit),
      account_exposure_limit: numericOrNull(draft.account_exposure_limit),
      trading_timezone: draft.trading_timezone,
      allowed_sessions: [[draft.session_start, draft.session_end]]
    });
  }

  return (
    <div className="space-y-4">
      <section className="panel p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">RiskManager</div>
            <h2 className="mt-1 flex items-center gap-2 text-xl font-semibold"><ShieldCheck size={20} /> 风控中心</h2>
            <p className="mt-1 max-w-3xl text-sm text-slate-500">
              这里是 TigerOpen gateway 之前的统一交易前风控。覆盖订单流、撤单频率、单笔数量、成交上限、标的/策略/账户暴露、价格带、fat finger 和交易时段。
            </p>
          </div>
          <IconButton label="运行风控示例" icon={<RefreshCw size={15} />} onClick={onSeed} disabled={busy} />
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Metric label="风控状态" value={config?.active ? "启用" : "关闭"} />
          <Metric label="Order Flow" value={String(state?.order_flow_count ?? 0)} />
          <Metric label="Active Orders" value={String(state?.active_order_count ?? 0)} />
          <Metric label="Trades Today" value={String(state?.trades_today ?? 0)} />
          <Metric label="最近检查" value={result ? (result.approved ? "通过" : "拒绝") : "-"} />
        </div>
      </section>

      <RiskRuleCoverage status={status} />

      <section className="grid grid-cols-1 gap-4 2xl:grid-cols-[0.95fr_1.05fr]">
        <RiskConfigEditor
          draft={draft}
          setDraft={setDraft}
          configAvailable={Boolean(config)}
          busy={busy}
          onLoadCurrent={loadCurrentConfig}
          onSubmit={submitConfig}
        />
        <RiskStatePanel status={status} result={result} />
      </section>

      <RiskProbePanel
        probe={probe}
        setProbe={setProbe}
        busy={busy}
        result={result}
        onEvaluate={() =>
          onEvaluateOrder({
            symbol: probe.symbol.trim().toUpperCase(),
            side: probe.side,
            quantity: Number(probe.quantity),
            price: Number(probe.price),
            strategy_id: probe.strategy_id.trim() || null
          })
        }
      />

      <section className="grid grid-cols-1 gap-4 2xl:grid-cols-[1.2fr_0.8fr]">
        <RiskOrdersTable orders={recentOrders} busy={busy} onCancelOrder={onCancelOrder} />
        <RiskTradesTable trades={recentTrades} />
      </section>
      <RiskExposureTables status={status} />
    </div>
  );
}

function RiskRuleCoverage({ status }: { status: RiskManagerStatus | null }) {
  const config = status?.config;
  const state = status?.state;
  const maxCancelCount = Math.max(0, ...Object.values(state?.cancel_counts_by_symbol ?? {}));
  const maxSymbolExposure = Math.max(0, ...Object.values(state?.symbol_exposure ?? {}).map((value) => Math.abs(value)));
  const maxStrategyExposure = Math.max(0, ...Object.values(state?.strategy_exposure ?? {}).map((value) => Math.abs(value)));
  const rows = [
    {
      name: "Order Flow",
      value: state?.order_flow_count ?? 0,
      limit: config?.order_flow_limit ?? null,
      unit: "orders",
      desc: `${config?.order_flow_window_seconds ?? "-"} 秒滚动窗口`
    },
    {
      name: "Active Orders",
      value: state?.active_order_count ?? 0,
      limit: config?.active_order_limit ?? null,
      unit: "orders",
      desc: "MainEngine 当前活动委托"
    },
    {
      name: "Trades Today",
      value: state?.trades_today ?? 0,
      limit: config?.trade_limit ?? null,
      unit: "trades",
      desc: "当天成交回报计数"
    },
    {
      name: "Cancel Rate",
      value: maxCancelCount,
      limit: config?.cancel_limit_per_symbol ?? null,
      unit: "cancels/symbol",
      desc: "单标的最大撤单数"
    },
    {
      name: "Symbol Exposure",
      value: maxSymbolExposure,
      limit: config?.symbol_exposure_limit ?? null,
      unit: "notional",
      desc: "单标的净暴露绝对值"
    },
    {
      name: "Strategy Exposure",
      value: maxStrategyExposure,
      limit: config?.strategy_exposure_limit ?? null,
      unit: "notional",
      desc: "单策略净暴露绝对值"
    },
    {
      name: "Account Exposure",
      value: state?.account_exposure ?? 0,
      limit: config?.account_exposure_limit ?? null,
      unit: "notional",
      desc: "账户总绝对暴露"
    },
    {
      name: "Fat Finger",
      value: 0,
      limit: config?.fat_finger_notional_limit ?? null,
      unit: "notional/order",
      desc: "单笔名义金额限制"
    },
    {
      name: "Price Band",
      value: 0,
      limit: config?.price_band_pct ?? null,
      unit: "pct from ref",
      desc: "订单价偏离最新参考价"
    },
    {
      name: "Trading Session",
      value: 0,
      limit: null,
      unit: config?.trading_timezone ?? "-",
      desc: sessionText(config)
    }
  ];
  return (
    <section className="panel p-4">
      <div className="mb-3">
        <h3 className="font-semibold">风控规则覆盖矩阵</h3>
        <p className="mt-1 text-sm text-slate-500">把 vn.py RiskManager 思路拆成可观察规则：订单流、活动委托、成交、撤单、暴露、价格带、fat finger 和交易时段。</p>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-5">
        {rows.map((row) => (
          <RiskLimitCard key={row.name} {...row} />
        ))}
      </div>
    </section>
  );
}

function RiskLimitCard({
  name,
  value,
  limit,
  unit,
  desc
}: {
  name: string;
  value: number;
  limit: number | null | undefined;
  unit: string;
  desc: string;
}) {
  const ratio = limit && limit > 0 ? Math.min(Math.abs(value) / limit, 1.25) : 0;
  const status = !limit ? "monitor" : ratio >= 1 ? "breach" : ratio >= 0.8 ? "watch" : "ok";
  const tone = {
    ok: "border-emerald-200 bg-emerald-50 text-emerald-900",
    watch: "border-amber-200 bg-amber-50 text-amber-900",
    breach: "border-rose-200 bg-rose-50 text-rose-900",
    monitor: "border-slate-200 bg-white text-slate-800"
  }[status];
  return (
    <div className={`rounded border p-3 ${tone}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-semibold">{name}</div>
          <div className="mt-1 text-xs opacity-80">{desc}</div>
        </div>
        <span className="rounded border border-current px-2 py-0.5 text-[11px] uppercase">
          {status === "breach" ? "limit" : status}
        </span>
      </div>
      <div className="mt-3 text-xl font-semibold">
        {formatRiskValue(value, unit)}
        <span className="ml-1 text-xs font-normal opacity-70">/ {limit ? formatRiskValue(limit, unit) : "monitor"}</span>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded bg-white/80">
        <div className="h-full rounded bg-current" style={{ width: `${Math.min(ratio, 1) * 100}%` }} />
      </div>
      <div className="mt-2 text-xs opacity-75">{unit}</div>
    </div>
  );
}

function RiskConfigEditor({
  draft,
  setDraft,
  configAvailable,
  busy,
  onLoadCurrent,
  onSubmit
}: {
  draft: RiskDraft;
  setDraft: (update: (draft: RiskDraft) => RiskDraft) => void;
  configAvailable: boolean;
  busy: boolean;
  onLoadCurrent: () => void;
  onSubmit: () => void;
}) {
  return (
    <section className="panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold">风控参数</h3>
          <p className="mt-1 text-sm text-slate-500">这些限制会在订单提交到 TigerOpen gateway 之前生效。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <IconButton label="载入当前配置" icon={<RefreshCw size={15} />} onClick={onLoadCurrent} disabled={!configAvailable || busy} />
          <IconButton label="保存风控参数" icon={<Activity size={15} />} onClick={onSubmit} disabled={busy} />
        </div>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-5">
        <RiskInput label="订单流上限" value={draft.order_flow_limit} onChange={(value) => setDraft((next) => ({ ...next, order_flow_limit: value }))} />
        <RiskInput label="流控窗口秒" value={draft.order_flow_window_seconds} onChange={(value) => setDraft((next) => ({ ...next, order_flow_window_seconds: value }))} />
        <RiskInput label="单笔数量上限" value={draft.order_size_limit} onChange={(value) => setDraft((next) => ({ ...next, order_size_limit: value }))} />
        <RiskInput label="成交次数上限" value={draft.trade_limit} onChange={(value) => setDraft((next) => ({ ...next, trade_limit: value }))} />
        <RiskInput label="活动订单上限" value={draft.active_order_limit} onChange={(value) => setDraft((next) => ({ ...next, active_order_limit: value }))} />
        <RiskInput label="单标的撤单上限" value={draft.cancel_limit_per_symbol} onChange={(value) => setDraft((next) => ({ ...next, cancel_limit_per_symbol: value }))} />
        <RiskInput label="价格带比例" value={draft.price_band_pct} onChange={(value) => setDraft((next) => ({ ...next, price_band_pct: value }))} />
        <RiskInput label="Fat Finger 金额" value={draft.fat_finger_notional_limit} onChange={(value) => setDraft((next) => ({ ...next, fat_finger_notional_limit: value }))} />
        <RiskInput label="标的暴露上限" value={draft.symbol_exposure_limit} onChange={(value) => setDraft((next) => ({ ...next, symbol_exposure_limit: value }))} placeholder="不限" />
        <RiskInput label="策略暴露上限" value={draft.strategy_exposure_limit} onChange={(value) => setDraft((next) => ({ ...next, strategy_exposure_limit: value }))} placeholder="不限" />
        <RiskInput label="账户暴露上限" value={draft.account_exposure_limit} onChange={(value) => setDraft((next) => ({ ...next, account_exposure_limit: value }))} placeholder="不限" />
        <RiskInput label="交易时区" value={draft.trading_timezone} onChange={(value) => setDraft((next) => ({ ...next, trading_timezone: value }))} />
        <RiskInput label="交易时段开始" value={draft.session_start} onChange={(value) => setDraft((next) => ({ ...next, session_start: value }))} />
        <RiskInput label="交易时段结束" value={draft.session_end} onChange={(value) => setDraft((next) => ({ ...next, session_end: value }))} />
        <label className="flex items-center gap-2 rounded border border-line bg-panel px-3 py-2 text-sm">
          <input type="checkbox" checked={draft.active} onChange={(event) => setDraft((next) => ({ ...next, active: event.target.checked }))} />
          启用风控
        </label>
      </div>
    </section>
  );
}

function RiskProbePanel({
  probe,
  setProbe,
  busy,
  result,
  onEvaluate
}: {
  probe: RiskProbeDraft;
  setProbe: (update: (draft: RiskProbeDraft) => RiskProbeDraft) => void;
  busy: boolean;
  result: RiskManagerEvaluateResult | null;
  onEvaluate: () => void;
}) {
  const valid = probe.symbol.trim() && Number(probe.quantity) > 0 && Number(probe.price) > 0;
  return (
    <section className="panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold">Pre-trade 风控试算</h3>
          <p className="mt-1 text-sm text-slate-500">手动构造一笔订单，只运行 RiskManager 评估，不提交 TigerOpen。用于调试价格带、fat finger、暴露和交易时段规则。</p>
        </div>
        <IconButton label="运行试算" icon={<ShieldCheck size={15} />} onClick={onEvaluate} disabled={busy || !valid} />
      </div>
      <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-5">
        <RiskInput label="标的" value={probe.symbol} onChange={(value) => setProbe((next) => ({ ...next, symbol: value.toUpperCase() }))} />
        <label className="min-w-0 text-xs font-medium text-slate-500">
          方向
          <select
            className="mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-sm text-ink"
            value={probe.side}
            onChange={(event) => setProbe((next) => ({ ...next, side: event.target.value as RiskProbeDraft["side"] }))}
          >
            <option value="buy">buy</option>
            <option value="sell">sell</option>
            <option value="long">long</option>
            <option value="short">short</option>
          </select>
        </label>
        <RiskInput label="数量" value={probe.quantity} onChange={(value) => setProbe((next) => ({ ...next, quantity: value }))} />
        <RiskInput label="价格" value={probe.price} onChange={(value) => setProbe((next) => ({ ...next, price: value }))} />
        <RiskInput label="策略/引用" value={probe.strategy_id} onChange={(value) => setProbe((next) => ({ ...next, strategy_id: value }))} />
      </div>
      {result ? (
        <div className={`mt-4 rounded border p-3 text-sm ${result.approved ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-rose-200 bg-rose-50 text-rose-900"}`}>
          <div className="font-semibold">{result.approved ? "试算通过" : "试算拒绝"}</div>
          <div className="mt-1">{result.reason}</div>
          <div className="mt-2 text-xs">
            {result.symbol} {result.side} {optionalDecimal(result.quantity, 2)} @ {optionalDecimal(result.price, 3)}
            {result.reference_price ? `；参考价 ${optionalDecimal(result.reference_price, 3)}` : "；无参考价，price_band 不参与"}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function RiskStatePanel({ status, result }: { status: RiskManagerStatus | null; result: RiskManagerEvaluateResult | null }) {
  const state = status?.state;
  return (
    <section className="panel p-4">
      <h3 className="font-semibold">实时风控状态</h3>
      <div className="mt-4 grid grid-cols-2 gap-3 text-sm md:grid-cols-3">
        <Info label="账户暴露" value={optionalDecimal(state?.account_exposure, 2)} />
        <Info label="撤单标的数" value={String(Object.keys(state?.cancel_counts_by_symbol ?? {}).length)} />
        <Info label="标的暴露数" value={String(Object.keys(state?.symbol_exposure ?? {}).length)} />
        <Info label="策略暴露数" value={String(Object.keys(state?.strategy_exposure ?? {}).length)} />
        <Info label="引擎" value={status?.engine ?? "-"} />
        <Info label="最后结果" value={result ? result.reason : "-"} />
      </div>
      {result ? (
        <div className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2">
          {result.checks.map((check) => (
            <div key={check.name} className={`rounded border p-2 text-xs ${check.passed ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-rose-200 bg-rose-50 text-rose-800"}`}>
              <div className="font-medium">{check.name}</div>
              <div className="mt-1">{check.message}</div>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-4 rounded border border-line bg-white p-3 text-sm text-slate-500">暂无最近风控检查结果。策略发单或运行示例后会显示每条规则的通过/拒绝原因。</p>
      )}
    </section>
  );
}

function RiskExposureTables({ status }: { status: RiskManagerStatus | null }) {
  const state = status?.state;
  const symbolRows = Object.entries(state?.symbol_exposure ?? {});
  const strategyRows = Object.entries(state?.strategy_exposure ?? {});
  const cancelRows = Object.entries(state?.cancel_counts_by_symbol ?? {});
  return (
    <section className="grid grid-cols-1 gap-4 xl:grid-cols-3">
      <RiskKeyValueTable title="标的暴露" empty="暂无标的暴露" rows={symbolRows} valueLabel="净暴露" />
      <RiskKeyValueTable title="策略暴露" empty="暂无策略暴露" rows={strategyRows} valueLabel="净暴露" />
      <RiskKeyValueTable title="撤单计数" empty="暂无撤单记录" rows={cancelRows} valueLabel="今日撤单" />
    </section>
  );
}

function RiskKeyValueTable({
  title,
  rows,
  valueLabel,
  empty
}: {
  title: string;
  rows: Array<[string, number]>;
  valueLabel: string;
  empty: string;
}) {
  return (
    <section className="panel overflow-hidden p-0">
      <div className="border-b border-line px-4 py-3">
        <h3 className="font-semibold">{title}</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[360px] text-left text-sm">
          <thead className="bg-panel text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2">Key</th>
              <th className="px-3 py-2">{valueLabel}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([key, value]) => (
              <tr key={key} className="border-t border-line">
                <td className="px-3 py-2 font-medium">{key || "-"}</td>
                <td className="px-3 py-2">{optionalDecimal(value, 2)}</td>
              </tr>
            ))}
            {!rows.length ? <tr><td colSpan={2} className="px-3 py-6 text-center text-slate-500">{empty}</td></tr> : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RiskOrdersTable({ orders, busy, onCancelOrder }: { orders: CoreOrder[]; busy: boolean; onCancelOrder: (orderid: string) => void }) {
  return (
    <section className="panel overflow-hidden p-0">
      <div className="border-b border-line px-4 py-3">
        <h3 className="font-semibold">风控订单流</h3>
        <p className="text-sm text-slate-500">MainEngine/OMS 订单、RiskManager 审批结果和 TigerOpen gateway 订单号。</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[980px] text-left text-sm">
          <thead className="bg-panel text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2">OMS Order</th>
              <th className="px-3 py-2">Gateway</th>
              <th className="px-3 py-2">标的</th>
              <th className="px-3 py-2">方向</th>
              <th className="px-3 py-2">数量</th>
              <th className="px-3 py-2">价格</th>
              <th className="px-3 py-2">状态</th>
              <th className="px-3 py-2">券商单号</th>
              <th className="px-3 py-2">风控</th>
              <th className="px-3 py-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((order) => {
              const failed = order.risk_result?.checks.find((check) => !check.passed);
              const active = order.status === "submitting" || order.status === "not_traded" || order.status === "partial_traded";
              return (
                <tr key={order.vt_orderid} className="border-t border-line align-top">
                  <td className="px-3 py-2 font-medium">{order.vt_orderid}</td>
                  <td className="px-3 py-2">{order.gateway_name}</td>
                  <td className="px-3 py-2">{order.vt_symbol}</td>
                  <td className="px-3 py-2">{order.direction}</td>
                  <td className="px-3 py-2">{optionalDecimal(order.volume, 2)}</td>
                  <td className="px-3 py-2">{optionalDecimal(order.price, 3)}</td>
                  <td className="px-3 py-2">
                    <span className={`rounded px-2 py-0.5 text-xs ${order.status === "rejected" ? "bg-rose-50 text-rose-700" : "bg-emerald-50 text-emerald-700"}`}>
                      {order.status}
                    </span>
                  </td>
                  <td className="px-3 py-2">{textValue(order.broker_order_id)}</td>
                  <td className="max-w-[320px] px-3 py-2">
                    {order.risk_result ? (
                      <div className={order.risk_result.approved ? "text-emerald-700" : "text-rose-700"}>
                        <div className="font-medium">{order.risk_result.approved ? "通过" : "拒绝"}</div>
                        <div className="mt-1 text-xs text-slate-600">{failed?.message ?? order.risk_result.reason}</div>
                      </div>
                    ) : (
                      <span className="text-slate-400">-</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <button
                      className="focus-ring rounded border border-line px-2 py-1 text-xs hover:bg-panel disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={busy || !active}
                      onClick={() => onCancelOrder(order.vt_orderid)}
                    >
                      撤单
                    </button>
                  </td>
                </tr>
              );
            })}
            {!orders.length ? (
              <tr><td colSpan={10} className="px-3 py-8 text-center text-slate-500">暂无 OMS 订单。策略或算法发单后会在这里显示风控结果。</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RiskTradesTable({ trades }: { trades: CoreTrade[] }) {
  return (
    <section className="panel overflow-hidden p-0">
      <div className="border-b border-line px-4 py-3">
        <h3 className="font-semibold">成交计数</h3>
        <p className="text-sm text-slate-500">Tiger 成交回报会驱动 RiskManager 的成交次数和暴露统计。</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[740px] text-left text-sm">
          <thead className="bg-panel text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2">OMS Trade</th>
              <th className="px-3 py-2">标的</th>
              <th className="px-3 py-2">方向</th>
              <th className="px-3 py-2">数量</th>
              <th className="px-3 py-2">价格</th>
              <th className="px-3 py-2">订单号</th>
              <th className="px-3 py-2">时间</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((trade) => (
              <tr key={trade.vt_tradeid} className="border-t border-line">
                <td className="px-3 py-2 font-medium">{trade.vt_tradeid}</td>
                <td className="px-3 py-2">{trade.vt_symbol}</td>
                <td className="px-3 py-2">{trade.direction}</td>
                <td className="px-3 py-2">{optionalDecimal(trade.volume, 2)}</td>
                <td className="px-3 py-2">{optionalDecimal(trade.price, 3)}</td>
                <td className="px-3 py-2">{textValue(trade.orderid)}</td>
                <td className="px-3 py-2">{new Date(trade.datetime).toLocaleString()}</td>
              </tr>
            ))}
            {!trades.length ? (
              <tr><td colSpan={7} className="px-3 py-8 text-center text-slate-500">暂无 OMS 成交。</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

type RiskDraft = {
  order_flow_limit: string;
  order_flow_window_seconds: string;
  order_size_limit: string;
  trade_limit: string;
  active_order_limit: string;
  cancel_limit_per_symbol: string;
  price_band_pct: string;
  fat_finger_notional_limit: string;
  symbol_exposure_limit: string;
  strategy_exposure_limit: string;
  account_exposure_limit: string;
  trading_timezone: string;
  session_start: string;
  session_end: string;
  active: boolean;
};

type RiskProbeDraft = {
  symbol: string;
  side: "buy" | "sell" | "long" | "short";
  quantity: string;
  price: string;
  strategy_id: string;
};

function RiskInput({
  label,
  value,
  onChange,
  placeholder
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="min-w-0 text-xs font-medium text-slate-500">
      {label}
      <input
        className="mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-sm text-ink"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
    </label>
  );
}

function numericOrNull(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatRiskValue(value: number, unit: string): string {
  if (unit.includes("pct")) {
    return `${(value * 100).toFixed(2)}%`;
  }
  if (unit.includes("notional")) {
    return `$${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function sessionText(config: RiskManagerStatus["config"] | undefined): string {
  const sessions = config?.allowed_sessions ?? [];
  if (!sessions.length) return "全天允许";
  return sessions.map((item) => `${item.start}-${item.end}`).join(", ");
}

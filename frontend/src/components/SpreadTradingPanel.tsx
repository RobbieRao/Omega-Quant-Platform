import { AlertTriangle, GitBranch, Layers3, Plus, RefreshCw, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { IconButton, Info, Metric } from "./primitives";
import type { SpreadData, SpreadLegPayload } from "../types";
import { optionalDecimal } from "../utils/format";

type SpreadOrderPayload = { direction: "long" | "short"; volume: number; price_offset?: number; reference?: string };

export function SpreadTradingPanel({
  spreads,
  busy,
  onSeed,
  onCreateSpread,
  onSendOrder
}: {
  spreads: SpreadData[];
  busy: boolean;
  onSeed: () => void;
  onCreateSpread: (payload: { name: string; active_leg: string; legs: SpreadLegPayload[] }) => void;
  onSendOrder: (name: string, payload: SpreadOrderPayload) => void;
}) {
  const activeOrderCount = spreads.reduce((sum, spread) => sum + spread.child_orderids.length, 0);
  const pricedSpreadCount = spreads.filter((spread) => spread.tick?.last_price !== null && spread.tick?.last_price !== undefined).length;
  return (
    <div className="space-y-4">
      <section className="panel p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">SpreadTrading</div>
            <h2 className="mt-1 text-xl font-semibold">组合价差工作台</h2>
            <p className="mt-1 max-w-3xl text-sm text-slate-500">
              用 vn.py 风格 SpreadTrading 管理美股股票/期权组合。这里重点展示合成价格、腿行情、拆腿路径和风险状态，不把组合单当成黑箱。
            </p>
          </div>
          <IconButton label="创建 covered call 模板" icon={<RefreshCw size={15} />} onClick={onSeed} disabled={busy} />
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Metric label="价差组合" value={String(spreads.length)} />
          <Metric label="有行情组合" value={`${pricedSpreadCount} / ${spreads.length}`} />
          <Metric label="腿订单" value={String(activeOrderCount)} />
          <Metric label="净仓组合" value={String(spreads.filter((spread) => spread.net_position !== 0).length)} />
          <Metric label="主用途" value="股票/期权组合" />
        </div>
        <div className="mt-4 grid grid-cols-1 gap-2 text-sm md:grid-cols-3">
          <RouteCard icon={<Layers3 size={15} />} label="组合定义" value="股票腿 + 期权腿" />
          <RouteCard icon={<ShieldCheck size={15} />} label="执行路径" value="Spread -> MainEngine -> RiskManager" />
          <RouteCard icon={<AlertTriangle size={15} />} label="关键检查" value="腿行情完整后才可发送" />
        </div>
      </section>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <SpreadCreateForm busy={busy} onCreate={onCreateSpread} />
        <SpreadOrderTicket spreads={spreads} busy={busy} onSendOrder={onSendOrder} />
      </section>

      <SpreadCards spreads={spreads} />
    </div>
  );
}

function SpreadCreateForm({ busy, onCreate }: { busy: boolean; onCreate: (payload: { name: string; active_leg: string; legs: SpreadLegPayload[] }) => void }) {
  const [draft, setDraft] = useState({
    name: "",
    active_leg: "",
    stock_symbol: "",
    stock_multiplier: "100",
    option_symbol: "",
    option_price_multiplier: "-1",
    option_trading_multiplier: "-1"
  });
  function submit() {
    onCreate({
      name: draft.name.trim().toUpperCase(),
      active_leg: draft.active_leg.trim().toUpperCase(),
      legs: [
        {
          symbol: draft.stock_symbol.trim().toUpperCase(),
          product: "equity",
          price_multiplier: 1,
          trading_multiplier: Number(draft.stock_multiplier),
          variable_name: "stock"
        },
        {
          symbol: draft.option_symbol.trim().toUpperCase(),
          product: "option",
          price_multiplier: Number(draft.option_price_multiplier),
          trading_multiplier: Number(draft.option_trading_multiplier),
          variable_name: "option"
        }
      ]
    });
  }
  return (
    <section className="panel p-4">
      <h3 className="font-semibold">创建股票/期权价差</h3>
      <p className="mt-1 text-sm text-slate-500">输入真实标的和期权合约，系统用腿价格合成 bid / ask / last，并按 multiplier 生成腿订单。</p>
      <div className="mt-4 grid grid-cols-2 gap-3">
        <TextInput label="组合名" value={draft.name} onChange={(value) => setDraft((next) => ({ ...next, name: value }))} placeholder="NVDA_COVERED_CALL" />
        <TextInput label="主动腿" value={draft.active_leg} onChange={(value) => setDraft((next) => ({ ...next, active_leg: value }))} placeholder="NVDA" />
        <TextInput label="股票腿" value={draft.stock_symbol} onChange={(value) => setDraft((next) => ({ ...next, stock_symbol: value }))} placeholder="NVDA" />
        <TextInput label="股票交易乘数" value={draft.stock_multiplier} onChange={(value) => setDraft((next) => ({ ...next, stock_multiplier: value }))} />
        <TextInput label="期权腿" value={draft.option_symbol} onChange={(value) => setDraft((next) => ({ ...next, option_symbol: value }))} placeholder="O:NVDA260618C00245000" className="col-span-2" />
        <TextInput label="期权价格乘数" value={draft.option_price_multiplier} onChange={(value) => setDraft((next) => ({ ...next, option_price_multiplier: value }))} />
        <TextInput label="期权交易乘数" value={draft.option_trading_multiplier} onChange={(value) => setDraft((next) => ({ ...next, option_trading_multiplier: value }))} />
      </div>
      <div className="mt-3 rounded border border-line bg-panel p-3 text-xs text-slate-600">
        Covered call 常见配置：股票腿交易乘数 100，期权腿价格乘数 -1，期权腿交易乘数 -1。负号表示卖出期权腿。
      </div>
      <div className="mt-4 flex justify-end">
        <IconButton label="创建价差" icon={<Plus size={15} />} onClick={submit} disabled={busy || !draft.name.trim() || !draft.stock_symbol.trim() || !draft.option_symbol.trim()} />
      </div>
    </section>
  );
}

function SpreadOrderTicket({
  spreads,
  busy,
  onSendOrder
}: {
  spreads: SpreadData[];
  busy: boolean;
  onSendOrder: (name: string, payload: SpreadOrderPayload) => void;
}) {
  const [draft, setDraft] = useState({
    spread_name: "",
    direction: "long" as "long" | "short",
    volume: "1",
    price_offset: "0.01",
    reference: "OmegaSpread:manual"
  });
  const selectedName = draft.spread_name || spreads[0]?.name || "";
  const selectedSpread = spreads.find((spread) => spread.name === selectedName);
  return (
    <section className="panel p-4">
      <h3 className="font-semibold">价差发单</h3>
      <p className="mt-1 text-sm text-slate-500">先确认合成价格和每条腿 multiplier。发送后会拆成腿订单，再进入 MainEngine / RiskManager。</p>
      <div className="mt-4 grid grid-cols-2 gap-3">
        <label className="text-xs font-medium text-slate-500">
          组合
          <select className="mt-1 w-full rounded border border-line px-2 py-1.5 text-sm text-ink" value={selectedName} onChange={(event) => setDraft((next) => ({ ...next, spread_name: event.target.value }))}>
            {spreads.map((spread) => (
              <option key={spread.name} value={spread.name}>{spread.name}</option>
            ))}
            {!spreads.length ? <option value="">暂无价差</option> : null}
          </select>
        </label>
        <label className="text-xs font-medium text-slate-500">
          方向
          <select className="mt-1 w-full rounded border border-line px-2 py-1.5 text-sm text-ink" value={draft.direction} onChange={(event) => setDraft((next) => ({ ...next, direction: event.target.value as "long" | "short" }))}>
            <option value="long">买入价差</option>
            <option value="short">卖出价差</option>
          </select>
        </label>
        <TextInput label="数量" value={draft.volume} onChange={(value) => setDraft((next) => ({ ...next, volume: value }))} />
        <TextInput label="价格偏移" value={draft.price_offset} onChange={(value) => setDraft((next) => ({ ...next, price_offset: value }))} />
        <TextInput label="审计备注" value={draft.reference} onChange={(value) => setDraft((next) => ({ ...next, reference: value }))} className="col-span-2" />
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
        <Info label="合成 Bid" value={optionalDecimal(selectedSpread?.tick?.bid_price, 3)} />
        <Info label="合成 Ask" value={optionalDecimal(selectedSpread?.tick?.ask_price, 3)} />
        <Info label="合成 Last" value={optionalDecimal(selectedSpread?.tick?.last_price, 3)} />
        <Info label="净仓" value={optionalDecimal(selectedSpread?.net_position, 2)} />
      </div>
      <div className="mt-4 flex justify-end">
        <IconButton
          label="发送价差腿订单"
          icon={<GitBranch size={15} />}
          onClick={() => onSendOrder(selectedName, {
            direction: draft.direction,
            volume: Number(draft.volume),
            price_offset: Number(draft.price_offset || 0),
            reference: draft.reference
          })}
          disabled={busy || !selectedName || !selectedSpread?.tick?.last_price}
        />
      </div>
      {!selectedSpread?.tick?.last_price && selectedName ? (
        <div className="mt-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          当前组合腿行情不完整，不能发送价差腿订单。请先确认股票腿和期权腿都有实时 bid / ask / last。
        </div>
      ) : null}
    </section>
  );
}

function SpreadCards({ spreads }: { spreads: SpreadData[] }) {
  return (
    <section className="grid grid-cols-1 gap-4">
      {spreads.map((spread) => (
        <article key={spread.name} className="panel p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="font-semibold">{spread.name}</h3>
              <p className="mt-1 text-sm text-slate-500">主动腿 {spread.active_leg}；更新时间 {new Date(spread.updated_at).toLocaleString()}</p>
            </div>
            <div className="grid grid-cols-3 gap-2 text-right text-sm">
              <Info label="Bid" value={optionalDecimal(spread.tick?.bid_price, 3)} />
              <Info label="Ask" value={optionalDecimal(spread.tick?.ask_price, 3)} />
              <Info label="Last" value={optionalDecimal(spread.tick?.last_price, 3)} />
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Metric label="净仓" value={optionalDecimal(spread.net_position, 2)} />
            <Metric label="腿数量" value={String(spread.legs.length)} />
            <Metric label="子订单" value={String(spread.child_orderids.length)} />
            <Metric label="主动腿" value={spread.active_leg} />
            <Metric label="价格状态" value={spread.tick?.last_price !== null && spread.tick?.last_price !== undefined ? "有行情" : "等待行情"} />
          </div>
          <div className="mt-4 grid grid-cols-1 gap-3 text-sm lg:grid-cols-3">
            <Info label="合成价格公式" value={spread.legs.map((leg) => `${leg.price_multiplier} * ${leg.variable_name || leg.symbol}`).join("  +  ")} />
            <Info label="交易腿公式" value={spread.legs.map((leg) => `${leg.trading_multiplier} * ${leg.variable_name || leg.symbol}`).join("  +  ")} />
            <Info label="最近腿订单" value={spread.child_orderids.slice(-3).join(", ") || "-"} />
          </div>
          <div className="mt-4 overflow-x-auto rounded border border-line bg-white">
            <table className="w-full min-w-[920px] text-left text-sm">
              <thead className="bg-panel text-xs text-slate-500">
                <tr>
                  <th className="px-3 py-2">腿</th>
                  <th className="px-3 py-2">类型</th>
                  <th className="px-3 py-2">VT Symbol</th>
                  <th className="px-3 py-2">Price Mult</th>
                  <th className="px-3 py-2">Trade Mult</th>
                  <th className="px-3 py-2">持仓</th>
                  <th className="px-3 py-2">Bid</th>
                  <th className="px-3 py-2">Ask</th>
                  <th className="px-3 py-2">Last</th>
                </tr>
              </thead>
              <tbody>
                {spread.legs.map((leg) => {
                  const prices = spread.tick?.leg_prices[leg.symbol] ?? spread.tick?.leg_prices[leg.vt_symbol] ?? {};
                  return (
                    <tr key={leg.symbol} className="border-t border-line">
                      <td className="break-all px-3 py-2 font-medium">{leg.symbol}</td>
                      <td className="px-3 py-2">{productText(leg.product)}</td>
                      <td className="break-all px-3 py-2">{leg.vt_symbol}</td>
                      <td className="px-3 py-2">{optionalDecimal(leg.price_multiplier, 3)}</td>
                      <td className="px-3 py-2">{optionalDecimal(leg.trading_multiplier, 3)}</td>
                      <td className="px-3 py-2">{optionalDecimal(spread.leg_positions[leg.symbol] ?? spread.leg_positions[leg.vt_symbol], 2)}</td>
                      <td className="px-3 py-2">{optionalDecimal(priceField(prices, "bid"), 3)}</td>
                      <td className="px-3 py-2">{optionalDecimal(priceField(prices, "ask"), 3)}</td>
                      <td className="px-3 py-2">{optionalDecimal(priceField(prices, "last"), 3)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </article>
      ))}
      {!spreads.length ? <section className="panel p-6 text-center text-sm text-slate-500">暂无组合价差。可以创建一个股票/期权两腿组合，或载入 covered call 示例。</section> : null}
    </section>
  );
}

function RouteCard({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded border border-line bg-white px-3 py-2">
      <div className="flex items-center gap-1 text-xs text-slate-500">{icon}{label}</div>
      <div className="mt-1 font-semibold">{value}</div>
    </div>
  );
}

function priceField(prices: Record<string, number | null | undefined>, field: "bid" | "ask" | "last"): number | null | undefined {
  return prices[field] ?? prices[`${field}_price`];
}

function productText(product: string): string {
  if (product === "option") return "期权";
  if (product === "etf") return "ETF";
  if (product === "equity") return "股票";
  return product;
}

function TextInput({
  label,
  value,
  onChange,
  placeholder,
  className = ""
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <label className={`min-w-0 text-xs font-medium text-slate-500 ${className}`}>
      {label}
      <input className="mt-1 w-full rounded border border-line px-2 py-1.5 text-sm text-ink" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
    </label>
  );
}

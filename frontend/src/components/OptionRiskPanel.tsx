import { BarChart3, Database, RefreshCw, ShieldCheck, Workflow } from "lucide-react";
import { useMemo, useState } from "react";
import { IconButton, Info, Metric } from "./primitives";
import type {
  OptionChain,
  OptionGreeksExposure,
  OptionScenarioMatrix,
  OptionTigerSyncResult,
  OptionVolCurve,
  RealAccountRisk,
  RealPosition,
  RealPositionGroup
} from "../types";
import { money, optionalDecimal, optionalMoney, optionalPct, textValue } from "../utils/format";

type OptionConfigPayload = {
  portfolio: string;
  underlying: string;
  expiry: string;
  contractSymbol: string;
  underlyingPrice: number;
  lastPrice: number;
  bidPrice: number;
  askPrice: number;
  volume: number;
  netVolume: number;
  marketValue: number;
  averagePrice: number;
  riskFreeRate: number;
  dividendYield: number;
  daysForward: number;
};

export function OptionRiskPanel({
  realPositions,
  positionGroups,
  realRisk,
  chain,
  curve,
  exposure,
  scenario,
  syncResult,
  busy,
  onConfigure,
  onSyncTiger,
  onSeedDemo
}: {
  realPositions: RealPosition[];
  positionGroups: RealPositionGroup[];
  realRisk: RealAccountRisk;
  chain: OptionChain | null;
  curve: OptionVolCurve | null;
  exposure: OptionGreeksExposure | null;
  scenario: OptionScenarioMatrix | null;
  syncResult: OptionTigerSyncResult | null;
  busy: boolean;
  onConfigure: (payload: OptionConfigPayload) => void;
  onSyncTiger: () => void;
  onSeedDemo: () => void;
}) {
  const optionPositions = useMemo(() => realPositions.filter(isOptionPosition), [realPositions]);
  const exposureByUnderlying = Object.entries(exposure?.by_underlying ?? {});

  return (
    <div className="space-y-4">
      <section className="panel p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">Option Risk</div>
            <h2 className="mt-1 text-xl font-semibold">期权风险</h2>
            <p className="mt-1 max-w-3xl text-sm text-slate-500">
              把 Tiger 期权持仓、实时行情、fast-vollib Greeks、OptionMaster 定价链、IV 曲线和情景压力测试放在同一个操作页里。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <IconButton label="同步 Tiger 持仓" icon={<Database size={15} />} onClick={onSyncTiger} disabled={busy} />
            <IconButton label="载入研究示例" icon={<RefreshCw size={15} />} onClick={onSeedDemo} disabled={busy} />
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Metric label="Tiger 期权持仓" value={String(optionPositions.length)} />
          <Metric label="期权市值暴露" value={optionalMoney(realRisk.option_market_value_abs)} />
          <Metric label="期权占账户比例" value={optionalPct(realRisk.option_weight)} />
          <Metric label="OptionMaster 合约" value={String(exposure?.contracts ?? 0)} />
          <Metric label="Weighted IV" value={optionalPct(exposure?.weighted_iv)} />
        </div>
      </section>

      <OptionWorkflowStrip syncResult={syncResult} />

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[0.85fr_1.15fr]">
        <AccountGreekSummary risk={realRisk} groups={positionGroups} />
        <OptionMasterSummary exposure={exposure} exposureByUnderlying={exposureByUnderlying} />
      </section>

      <RealOptionPositionsTable positions={optionPositions} allPositionsCount={realPositions.length} />

      <OptionSyncResultPanel result={syncResult} />

      <OptionMasterEditor busy={busy} onConfigure={onConfigure} />

      <section className="grid grid-cols-1 gap-4 2xl:grid-cols-[1.15fr_0.85fr]">
        <OptionChainTable chain={chain} curve={curve} />
        <ScenarioTable scenario={scenario} />
      </section>
    </div>
  );
}

function OptionWorkflowStrip({ syncResult }: { syncResult: OptionTigerSyncResult | null }) {
  const items = [
    { title: "1. Tiger 持仓", desc: "读取当前账户股票与期权腿", value: syncResult ? `${syncResult.synced_positions} 条已同步` : "等待同步" },
    { title: "2. OptionMaster", desc: "标准化合约、报价、持仓", value: syncResult?.portfolio ?? "未选择组合" },
    { title: "3. Greeks / IV", desc: "fast-vollib 估算敏感度", value: syncResult?.exposure?.contracts ? `${syncResult.exposure.contracts} 个合约` : "暂无暴露" },
    { title: "4. 风控与组合", desc: "用于风险面板、价差和回测研究", value: "只做建模监督" }
  ];
  return (
    <section className="panel p-4">
      <div className="mb-3 flex items-center gap-2">
        <Workflow size={16} />
        <h3 className="font-semibold">期权工作流</h3>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
        {items.map((item) => (
          <div key={item.title} className="rounded border border-line bg-white p-3">
            <div className="text-sm font-semibold">{item.title}</div>
            <div className="mt-1 text-xs text-slate-500">{item.desc}</div>
            <div className="mt-3 text-sm font-medium text-ink">{item.value}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

function AccountGreekSummary({ risk, groups }: { risk: RealAccountRisk; groups: RealPositionGroup[] }) {
  const greeks = risk.option_greeks_exposure ?? {};
  const grouped = groups.filter((group) => group.option_positions.length > 0);
  return (
    <section className="panel p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="flex items-center gap-2 font-semibold"><ShieldCheck size={16} /> 账户期权 Greeks</h3>
          <p className="mt-1 text-sm text-slate-500">来自 TigerOpen 持仓快照和已吸收的 Greeks 估算链路。</p>
        </div>
        <span className={`rounded border px-2 py-1 text-xs ${greeks.available === true ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-slate-200 bg-slate-100 text-slate-700"}`}>
          {greeks.available === true ? "已估算" : "暂无完整 Greeks"}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
        <Info label="合约数" value={optionalDecimal(greeks.total_contracts_abs, 0)} />
        <Info label="Delta 等效股数" value={optionalDecimal(greeks.delta_shares, 2)} />
        <Info label="Gamma 等效" value={optionalDecimal(greeks.gamma_shares, 2)} />
        <Info label="Theta" value={optionalDecimal(greeks.theta, 2)} />
        <Info label="Vega" value={optionalDecimal(greeks.vega, 2)} />
        <Info label="加权 IV" value={optionalPct(greeks.weighted_iv)} />
        <Info label="数据来源" value={Array.isArray(greeks.sources) && greeks.sources.length ? greeks.sources.join(", ") : "-"} />
        <Info label="分组标的" value={String(grouped.length)} />
      </div>
      <div className="mt-4 overflow-x-auto rounded border border-line bg-white">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="bg-panel text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2">标的</th>
              <th className="px-3 py-2">股票腿</th>
              <th className="px-3 py-2">期权腿</th>
              <th className="px-3 py-2">总市值</th>
              <th className="px-3 py-2">未实现盈亏</th>
            </tr>
          </thead>
          <tbody>
            {grouped.map((group) => (
              <tr key={group.underlying} className="border-t border-line">
                <td className="px-3 py-2 font-medium">{group.underlying}</td>
                <td className="px-3 py-2">{group.stock_positions.length}</td>
                <td className="px-3 py-2">{group.option_positions.length}</td>
                <td className="px-3 py-2">{money(group.total_market_value)}</td>
                <td className="px-3 py-2">{money(group.total_unrealized_pnl)}</td>
              </tr>
            ))}
            {!grouped.length ? (
              <tr><td colSpan={5} className="px-3 py-6 text-center text-slate-500">当前真实账户快照里没有可归组的期权持仓。</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-slate-500">{textValue(greeks.message, "Greeks 会随持仓快照和行情可用性更新。")}</p>
    </section>
  );
}

function OptionMasterSummary({
  exposure,
  exposureByUnderlying
}: {
  exposure: OptionGreeksExposure | null;
  exposureByUnderlying: Array<[string, Record<string, number>]>;
}) {
  return (
    <section className="panel p-4">
      <h3 className="flex items-center gap-2 font-semibold"><BarChart3 size={16} /> OptionMaster 组合风险</h3>
      <p className="mt-1 text-sm text-slate-500">用于研究组合 Greeks、IV 和压力测试；数据由 OptionMaster 引擎统一计算。</p>
      <div className="mt-4 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
        <Info label="组合" value={exposure?.portfolio ?? "-"} />
        <Info label="净张数" value={optionalDecimal(exposure?.net_contracts, 2)} />
        <Info label="Delta Shares" value={optionalDecimal(exposure?.delta_shares, 2)} />
        <Info label="Gamma Shares" value={optionalDecimal(exposure?.gamma_shares, 2)} />
        <Info label="Theta" value={optionalDecimal(exposure?.theta, 2)} />
        <Info label="Vega" value={optionalDecimal(exposure?.vega, 2)} />
        <Info label="合约数" value={String(exposure?.contracts ?? 0)} />
        <Info label="Weighted IV" value={optionalPct(exposure?.weighted_iv)} />
      </div>
      <div className="mt-4 overflow-x-auto rounded border border-line bg-white">
        <table className="w-full min-w-[620px] text-left text-sm">
          <thead className="bg-panel text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2">标的</th>
              <th className="px-3 py-2">净张数</th>
              <th className="px-3 py-2">Delta</th>
              <th className="px-3 py-2">Gamma</th>
              <th className="px-3 py-2">Theta</th>
              <th className="px-3 py-2">Vega</th>
            </tr>
          </thead>
          <tbody>
            {exposureByUnderlying.map(([underlying, row]) => (
              <tr key={underlying} className="border-t border-line">
                <td className="px-3 py-2 font-medium">{underlying}</td>
                <td className="px-3 py-2">{optionalDecimal(row.net_contracts, 2)}</td>
                <td className="px-3 py-2">{optionalDecimal(row.delta_shares, 2)}</td>
                <td className="px-3 py-2">{optionalDecimal(row.gamma_shares, 2)}</td>
                <td className="px-3 py-2">{optionalDecimal(row.theta, 2)}</td>
                <td className="px-3 py-2">{optionalDecimal(row.vega, 2)}</td>
              </tr>
            ))}
            {!exposureByUnderlying.length ? (
              <tr><td colSpan={6} className="px-3 py-6 text-center text-slate-500">更新 OptionMaster 后显示按标的 Greeks 暴露。</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function OptionSyncResultPanel({ result }: { result: OptionTigerSyncResult | null }) {
  if (!result) {
    return null;
  }
  return (
    <section className="panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold">Tiger 持仓同步结果</h3>
          <p className="mt-1 text-sm text-slate-500">{result.message}</p>
        </div>
        <span className="rounded border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs text-emerald-800">
          {result.source}
        </span>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 text-sm md:grid-cols-5">
        <Info label="组合" value={result.portfolio} />
        <Info label="同步持仓" value={String(result.synced_positions)} />
        <Info label="标的" value={result.underlyings.length ? result.underlyings.join(", ") : "-"} />
        <Info label="到期日" value={result.expiries.length ? result.expiries.join(", ") : "-"} />
        <Info label="跳过项" value={String(result.skipped_positions.length)} />
      </div>
      {result.skipped_positions.length ? (
        <div className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
          {result.skipped_positions.map((item) => `${item.symbol}: ${item.reason}`).join("；")}
        </div>
      ) : null}
    </section>
  );
}

function OptionMasterEditor({ busy, onConfigure }: { busy: boolean; onConfigure: (payload: OptionConfigPayload) => void }) {
  const [draft, setDraft] = useState({
    portfolio: "NVDA",
    underlying: "NVDA",
    expiry: "20260618",
    contractSymbol: "O:NVDA260618C00245000",
    underlyingPrice: "250",
    lastPrice: "10",
    bidPrice: "9.9",
    askPrice: "10.1",
    volume: "120",
    netVolume: "2",
    marketValue: "2000",
    averagePrice: "10",
    riskFreeRate: "0.04",
    dividendYield: "0",
    daysForward: "0"
  });

  function configure() {
    onConfigure({
      portfolio: draft.portfolio.trim().toUpperCase(),
      underlying: draft.underlying.trim().toUpperCase(),
      expiry: draft.expiry.trim(),
      contractSymbol: draft.contractSymbol.trim().toUpperCase(),
      underlyingPrice: Number(draft.underlyingPrice),
      lastPrice: Number(draft.lastPrice),
      bidPrice: Number(draft.bidPrice),
      askPrice: Number(draft.askPrice),
      volume: Number(draft.volume),
      netVolume: Number(draft.netVolume),
      marketValue: Number(draft.marketValue),
      averagePrice: Number(draft.averagePrice),
      riskFreeRate: Number(draft.riskFreeRate),
      dividendYield: Number(draft.dividendYield),
      daysForward: Number(draft.daysForward)
    });
  }

  return (
    <section className="panel p-4">
      <div className="mb-3">
        <h3 className="font-semibold">手动建模输入</h3>
        <p className="mt-1 text-sm text-slate-500">用于临时研究单个合约。正常流程优先点击“同步 Tiger 持仓”，让账户持仓自动进入 OptionMaster。</p>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <TextInput label="组合" value={draft.portfolio} onChange={(value) => setDraft((next) => ({ ...next, portfolio: value }))} />
        <TextInput label="标的" value={draft.underlying} onChange={(value) => setDraft((next) => ({ ...next, underlying: value }))} />
        <TextInput label="到期" value={draft.expiry} onChange={(value) => setDraft((next) => ({ ...next, expiry: value }))} />
        <TextInput label="期权合约" value={draft.contractSymbol} onChange={(value) => setDraft((next) => ({ ...next, contractSymbol: value }))} className="md:col-span-2 xl:col-span-2" />
        <TextInput label="标的价" value={draft.underlyingPrice} onChange={(value) => setDraft((next) => ({ ...next, underlyingPrice: value }))} />
        <TextInput label="Last" value={draft.lastPrice} onChange={(value) => setDraft((next) => ({ ...next, lastPrice: value }))} />
        <TextInput label="Bid" value={draft.bidPrice} onChange={(value) => setDraft((next) => ({ ...next, bidPrice: value }))} />
        <TextInput label="Ask" value={draft.askPrice} onChange={(value) => setDraft((next) => ({ ...next, askPrice: value }))} />
        <TextInput label="成交量" value={draft.volume} onChange={(value) => setDraft((next) => ({ ...next, volume: value }))} />
        <TextInput label="持仓张数" value={draft.netVolume} onChange={(value) => setDraft((next) => ({ ...next, netVolume: value }))} />
        <TextInput label="市值" value={draft.marketValue} onChange={(value) => setDraft((next) => ({ ...next, marketValue: value }))} />
        <TextInput label="均价" value={draft.averagePrice} onChange={(value) => setDraft((next) => ({ ...next, averagePrice: value }))} />
        <TextInput label="无风险利率" value={draft.riskFreeRate} onChange={(value) => setDraft((next) => ({ ...next, riskFreeRate: value }))} />
        <TextInput label="股息率" value={draft.dividendYield} onChange={(value) => setDraft((next) => ({ ...next, dividendYield: value }))} />
        <TextInput label="情景天数" value={draft.daysForward} onChange={(value) => setDraft((next) => ({ ...next, daysForward: value }))} />
      </div>
      <div className="mt-4 flex justify-end">
        <IconButton label="更新 OptionMaster" icon={<BarChart3 size={15} />} onClick={configure} disabled={busy || !draft.contractSymbol.trim()} />
      </div>
    </section>
  );
}

function RealOptionPositionsTable({ positions, allPositionsCount }: { positions: RealPosition[]; allPositionsCount: number }) {
  return (
    <section className="panel overflow-hidden p-0">
      <div className="border-b border-line px-4 py-3">
        <h3 className="font-semibold">真实期权持仓 Greeks 明细</h3>
        <p className="text-sm text-slate-500">共 {allPositionsCount} 条真实持仓，其中 {positions.length} 条识别为期权。表格可横向滚动，避免 Greeks 字段被截断。</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1580px] text-left text-sm">
          <thead className="bg-panel text-xs text-slate-500">
            <tr>
              <th className="sticky left-0 z-10 bg-panel px-3 py-2">合约</th>
              <th className="px-3 py-2">标的</th>
              <th className="px-3 py-2">到期</th>
              <th className="px-3 py-2">C/P</th>
              <th className="px-3 py-2">行权价</th>
              <th className="px-3 py-2">数量</th>
              <th className="px-3 py-2">成本</th>
              <th className="px-3 py-2">Mark/市价</th>
              <th className="px-3 py-2">Bid</th>
              <th className="px-3 py-2">Ask</th>
              <th className="px-3 py-2">市值</th>
              <th className="px-3 py-2">未实现盈亏</th>
              <th className="px-3 py-2">IV</th>
              <th className="px-3 py-2">Delta</th>
              <th className="px-3 py-2">Gamma</th>
              <th className="px-3 py-2">Theta</th>
              <th className="px-3 py-2">Vega</th>
              <th className="px-3 py-2">DTE</th>
              <th className="px-3 py-2">价内/价外</th>
              <th className="px-3 py-2">Greeks 来源</th>
              <th className="px-3 py-2">状态</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position, index) => (
              <tr key={`${position.display}-${index}`} className="border-t border-line">
                <td className="sticky left-0 z-10 max-w-[240px] break-all border-r border-line bg-white px-3 py-2 font-medium">{position.display ?? position.canonical_option_symbol ?? position.symbol ?? "-"}</td>
                <td className="px-3 py-2">{position.option_underlying ?? "-"}</td>
                <td className="px-3 py-2">{position.option_expiry ?? "-"}</td>
                <td className="px-3 py-2">{position.option_put_call ?? "-"}</td>
                <td className="px-3 py-2">{optionalDecimal(position.option_strike, 2)}</td>
                <td className="px-3 py-2">{optionalDecimal(position.quantity, 2)}</td>
                <td className="px-3 py-2">{optionalMoney(position.average_cost)}</td>
                <td className="px-3 py-2">{optionalMoney(position.mark_price ?? position.market_price)}</td>
                <td className="px-3 py-2">{optionalMoney(position.option_bid)}</td>
                <td className="px-3 py-2">{optionalMoney(position.option_ask)}</td>
                <td className="px-3 py-2">{optionalMoney(position.market_value)}</td>
                <td className="px-3 py-2">{optionalMoney(position.unrealized_pnl)}</td>
                <td className="px-3 py-2">{optionalPct(position.iv)}</td>
                <td className="px-3 py-2">{optionalDecimal(position.delta, 4)}</td>
                <td className="px-3 py-2">{optionalDecimal(position.gamma, 4)}</td>
                <td className="px-3 py-2">{optionalDecimal(position.theta, 4)}</td>
                <td className="px-3 py-2">{optionalDecimal(position.vega, 4)}</td>
                <td className="px-3 py-2">{optionalDecimal(position.days_to_expiry, 0)}</td>
                <td className="px-3 py-2">{optionalPct(position.option_moneyness)}</td>
                <td className="px-3 py-2">{position.greeks_source ?? position.greeks_engine ?? "-"}</td>
                <td className="px-3 py-2">{position.greeks_status ?? position.greeks_error ?? "-"}</td>
              </tr>
            ))}
            {!positions.length ? (
              <tr><td colSpan={21} className="px-3 py-8 text-center text-slate-500">当前真实账户快照没有期权持仓，或 Greeks 数据暂未同步。</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function OptionChainTable({ chain, curve }: { chain: OptionChain | null; curve: OptionVolCurve | null }) {
  return (
    <section className="panel overflow-hidden p-0">
      <div className="border-b border-line px-4 py-3">
        <h3 className="font-semibold">OptionMaster 期权链 / IV 曲线</h3>
        <p className="text-sm text-slate-500">
          {chain ? `${chain.underlying} · ${chain.expiry} · 标的价 ${optionalDecimal(chain.underlying_price, 2)}` : "更新 OptionMaster 后显示。"}
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[980px] text-left text-sm">
          <thead className="bg-panel text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2">Call</th>
              <th className="px-3 py-2">Call IV</th>
              <th className="px-3 py-2">Call Delta</th>
              <th className="px-3 py-2">行权价</th>
              <th className="px-3 py-2">Put</th>
              <th className="px-3 py-2">Put IV</th>
              <th className="px-3 py-2">Put Delta</th>
              <th className="px-3 py-2">Curve Mid IV</th>
            </tr>
          </thead>
          <tbody>
            {(chain?.rows ?? []).map((row) => {
              const curvePoint = curve?.points.find((point) => point.strike === row.strike);
              return (
                <tr key={row.strike} className="border-t border-line">
                  <td className="max-w-[220px] break-all px-3 py-2">{textValue(row.call?.symbol)}</td>
                  <td className="px-3 py-2">{optionalPct(row.call?.iv)}</td>
                  <td className="px-3 py-2">{optionalDecimal(row.call?.delta, 4)}</td>
                  <td className="px-3 py-2 font-semibold">{optionalDecimal(row.strike, 2)}</td>
                  <td className="max-w-[220px] break-all px-3 py-2">{textValue(row.put?.symbol)}</td>
                  <td className="px-3 py-2">{optionalPct(row.put?.iv)}</td>
                  <td className="px-3 py-2">{optionalDecimal(row.put?.delta, 4)}</td>
                  <td className="px-3 py-2">{optionalPct(curvePoint?.mid_iv)}</td>
                </tr>
              );
            })}
            {!chain?.rows?.length ? (
              <tr><td colSpan={8} className="px-3 py-8 text-center text-slate-500">暂无期权链。请先更新 OptionMaster。</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ScenarioTable({ scenario }: { scenario: OptionScenarioMatrix | null }) {
  return (
    <section className="panel overflow-hidden p-0">
      <div className="border-b border-line px-4 py-3">
        <h3 className="font-semibold">情景压力测试</h3>
        <p className="text-sm text-slate-500">
          Base {optionalDecimal(scenario?.base_underlying_price, 2)} / Value {optionalMoney(scenario?.base_market_value)} / Contracts {scenario?.contracts_used ?? 0}
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[860px] text-left text-sm">
          <thead className="bg-panel text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2">标的变动</th>
              <th className="px-3 py-2">IV 变动</th>
              <th className="px-3 py-2">标的价</th>
              <th className="px-3 py-2">Delta PnL</th>
              <th className="px-3 py-2">Gamma PnL</th>
              <th className="px-3 py-2">Vega PnL</th>
              <th className="px-3 py-2">Theta PnL</th>
              <th className="px-3 py-2">Total PnL</th>
            </tr>
          </thead>
          <tbody>
            {(scenario?.rows ?? []).map((row) => (
              <tr key={`${row.price_move}-${row.iv_shift}-${row.days_forward}`} className="border-t border-line">
                <td className="px-3 py-2">{optionalPct(row.price_move)}</td>
                <td className="px-3 py-2">{optionalDecimal(row.iv_shift * 100, 1)} vol</td>
                <td className="px-3 py-2">{optionalDecimal(row.underlying_price, 2)}</td>
                <td className="px-3 py-2">{optionalMoney(row.delta_pnl)}</td>
                <td className="px-3 py-2">{optionalMoney(row.gamma_pnl)}</td>
                <td className="px-3 py-2">{optionalMoney(row.vega_pnl)}</td>
                <td className="px-3 py-2">{optionalMoney(row.theta_pnl)}</td>
                <td className={`px-3 py-2 font-semibold ${row.total_pnl >= 0 ? "text-accent" : "text-danger"}`}>{optionalMoney(row.total_pnl)}</td>
              </tr>
            ))}
            {!scenario?.rows?.length ? (
              <tr><td colSpan={8} className="px-3 py-8 text-center text-slate-500">暂无情景矩阵。请先更新 OptionMaster。</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TextInput({
  label,
  value,
  onChange,
  className = ""
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  className?: string;
}) {
  return (
    <label className={`min-w-0 text-xs font-medium text-slate-500 ${className}`}>
      {label}
      <input className="mt-1 w-full rounded border border-line px-2 py-1.5 text-sm text-ink" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function isOptionPosition(position: RealPosition) {
  const secType = (position.sec_type ?? "").toUpperCase();
  return (
    secType.includes("OPT") ||
    secType.includes("OPTION") ||
    Boolean(position.canonical_option_symbol) ||
    Boolean(position.option_underlying) ||
    position.iv !== null && position.iv !== undefined ||
    position.delta !== null && position.delta !== undefined
  );
}

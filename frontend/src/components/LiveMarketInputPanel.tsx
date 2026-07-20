import { Info } from "./primitives";
import type { DataMode, DataModeState, MarketEvent, RateLimitSnapshot } from "../types";
import { money } from "../utils/format";

export function LiveMarketInputPanel({
  dataMode,
  marketSnapshot,
  tradeSymbol,
  rateLimits,
  onTradeSymbolChange,
  onRefresh,
  onChannelChange
}: {
  dataMode: DataModeState;
  marketSnapshot: MarketEvent | null;
  tradeSymbol: string;
  rateLimits: RateLimitSnapshot;
  onTradeSymbolChange: (symbol: string) => void;
  onRefresh: () => void;
  onChannelChange: (subscribeQuotes: boolean, subscribeTrades: boolean) => void;
}) {
  const liveStatus = dataMode.live_readonly_status;
  const liveConnected = Boolean(liveStatus.connected);
  const subscribeQuotes = Boolean(liveStatus.subscribe_quotes);
  const subscribeTrades = Boolean(liveStatus.subscribe_trades);
  return (
    <section className="panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">行情来源：{dataModeLabel(dataMode.data_mode)}</h2>
          <p className="mt-1 text-sm text-slate-500">{dataMode.message}</p>
          <p className="mt-2 rounded border border-cyan-200 bg-cyan-50 px-3 py-2 text-sm text-cyan-900">
            当前只接受实时行情输入。行情不可用时，策略运行会停止，不会用模拟数据补位。
          </p>
        </div>
        <span className="rounded border border-line bg-white px-3 py-2 text-sm font-medium text-slate-700">Massive 实时行情</span>
      </div>
      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        <span className="rounded border border-line bg-panel px-2 py-1">执行模式：Live Gateway</span>
        <span className="rounded border border-line bg-panel px-2 py-1">执行目标：当前 TigerOpen 账户</span>
        <span className={`rounded border px-2 py-1 ${liveConnected ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
          Massive 行情流：{String(liveStatus.status ?? "disconnected")}
        </span>
        <span className="rounded border border-line bg-panel px-2 py-1">Q 报价：{subscribeQuotes ? "开" : "关"}</span>
        <span className="rounded border border-line bg-panel px-2 py-1">T 逐笔：{subscribeTrades ? "开" : "关"}</span>
      </div>
      <div className="mt-3 rounded border border-line bg-white p-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-semibold">当前行情快照</div>
            <div className="mt-1 text-xs text-slate-500">策略运行只使用实时 MarketEvent。没有快照时不会生成订单。</div>
          </div>
          <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:items-end">
            <label className="block text-xs text-slate-500">
              策略运行标的
              <input
                className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink sm:w-44"
                value={tradeSymbol}
                onChange={(event) => onTradeSymbolChange(event.target.value.trim().toUpperCase())}
                placeholder="输入标的，如 NVDA / GLD / O:..."
              />
            </label>
            <button className="focus-ring w-full rounded border border-line px-3 py-1.5 text-sm hover:bg-panel disabled:opacity-50 sm:w-auto" disabled={!tradeSymbol.trim()} onClick={onRefresh}>
              刷新快照
            </button>
          </div>
        </div>
        {marketSnapshot ? (
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs md:grid-cols-6">
            <Info label="来源" value={dataModeLabel(marketSnapshot.source)} />
            <Info label="标的" value={marketSnapshot.symbol} />
            <Info label="价格" value={money(marketSnapshot.price)} />
            <Info label="成交量" value={String(marketSnapshot.volume ?? "-")} />
            <Info label="质量" value={marketSnapshot.data_quality} />
            <Info label="收到时间" value={new Date(marketSnapshot.received_at).toLocaleString()} />
          </div>
        ) : (
          <p className="mt-3 text-sm text-slate-500">暂无行情快照。输入标的后刷新快照，或运行一次策略动作生成 lineage。</p>
        )}
      </div>
      <div className="mt-3 flex flex-col gap-3 rounded border border-line bg-white px-3 py-2 text-sm sm:flex-row sm:flex-wrap sm:items-center">
        <span className="font-medium">实时频道</span>
        <label className="inline-flex items-center gap-2">
          <input type="checkbox" checked={subscribeQuotes} onChange={(event) => onChannelChange(event.target.checked, subscribeTrades)} />
          Q 报价/盘口
        </label>
        <label className="inline-flex items-center gap-2">
          <input type="checkbox" checked={subscribeTrades} onChange={(event) => onChannelChange(subscribeQuotes, event.target.checked)} />
          T 逐笔成交
        </label>
        <span className="text-xs text-slate-500">默认只开 Q；T 事件量更大，适合期权流研究。</span>
      </div>
      <RateLimitStrip rateLimits={rateLimits} />
    </section>
  );
}

function RateLimitStrip({ rateLimits }: { rateLimits: RateLimitSnapshot }) {
  const channels = Object.values(rateLimits.channels ?? {});
  if (!channels.length) {
    return null;
  }
  return (
    <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-3">
      {channels.map((channel) => {
        const usage = channel.usagePct === null ? 0 : channel.usagePct;
        const width = `${Math.min(100, Math.max(0, usage * 100)).toFixed(0)}%`;
        const tone = channel.status === "danger" ? "bg-danger" : channel.status === "warning" ? "bg-warn" : "bg-accent";
        return (
          <div key={channel.key} className="rounded border border-line bg-white p-3">
            <div className="flex items-center justify-between gap-3 text-xs">
              <span className="font-medium">{channel.label}</span>
              <span className="text-slate-500">{channel.rpm}/{channel.limitRpm} rpm</span>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded bg-panel">
              <div className={`h-full ${tone}`} style={{ width }} />
            </div>
            <div className="mt-1 text-[11px] text-slate-500">剩余 {channel.remainingRpm} / {rateLimits.windowSeconds}s</div>
          </div>
        );
      })}
    </div>
  );
}

export function dataModeLabel(mode: DataMode): string {
  void mode;
  return "Massive 实时行情";
}

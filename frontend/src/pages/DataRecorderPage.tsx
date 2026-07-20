import { useCallback, useEffect, useState } from "react";
import { LiveMarketInputPanel } from "../components/LiveMarketInputPanel";
import { api } from "../api";
import type { DataModeState, DataOverviewRow, DataRecordingStatus, MarketEvent, RateLimitSnapshot } from "../types";
import { defaultDataMode, defaultRateLimits } from "../state/workbenchDefaults";

export default function DataRecorderPage() {
  const [dataMode, setDataMode] = useState<DataModeState>(defaultDataMode);
  const [rateLimits, setRateLimits] = useState<RateLimitSnapshot>(defaultRateLimits);
  const [recordings, setRecordings] = useState<DataRecordingStatus | null>(null);
  const [overview, setOverview] = useState<DataOverviewRow[]>([]);
  const [snapshot, setSnapshot] = useState<MarketEvent | null>(null);
  const [tradeSymbol, setTradeSymbol] = useState("AAPL");
  const [newSymbol, setNewSymbol] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const [dm, rl, rec, ov] = await Promise.allSettled([
      api.dataMode(),
      api.marketRateLimits(),
      api.dataRecordings(),
      api.dataOverview(),
    ]);
    if (dm.status === "fulfilled") setDataMode(dm.value);
    if (rl.status === "fulfilled") setRateLimits(rl.value);
    if (rec.status === "fulfilled") setRecordings(rec.value);
    if (ov.status === "fulfilled") setOverview(ov.value);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const handleRefreshSnapshot = useCallback(async () => {
    if (!tradeSymbol) return;
    setBusy(true);
    try { const s = await api.marketSnapshot(tradeSymbol); setSnapshot(s); } finally { setBusy(false); }
  }, [tradeSymbol]);

  const handleChannelChange = useCallback(async (subscribeQuotes: boolean, subscribeTrades: boolean) => {
    setBusy(true);
    try {
      await api.setLiveMarketChannels(subscribeQuotes, subscribeTrades);
      const dm = await api.dataMode();
      setDataMode(dm);
    } finally {
      setBusy(false);
    }
  }, []);

  const handleAddTick = useCallback(async (symbol: string) => {
    setBusy(true);
    try { await api.addTickRecording(symbol); await refresh(); } finally { setBusy(false); }
  }, [refresh]);

  const handleRemoveTick = useCallback(async (symbol: string) => {
    setBusy(true);
    try { await api.removeTickRecording(symbol); await refresh(); } finally { setBusy(false); }
  }, [refresh]);

  const handleAddBar = useCallback(async (symbol: string) => {
    setBusy(true);
    try { await api.addBarRecording(symbol); await refresh(); } finally { setBusy(false); }
  }, [refresh]);

  const handleRemoveBar = useCallback(async (symbol: string) => {
    setBusy(true);
    try { await api.removeBarRecording(symbol); await refresh(); } finally { setBusy(false); }
  }, [refresh]);

  const handleDeleteSymbol = useCallback(async (symbol: string) => {
    if (!confirm(`确认删除 ${symbol} 的全部 tick 与 bar 历史数据？此操作不可恢复。`)) return;
    setBusy(true);
    try { await api.deleteSymbolData(symbol); await refresh(); } finally { setBusy(false); }
  }, [refresh]);

  const tickSymbols: string[] = recordings?.tick_recordings ?? [];
  const barSymbols: string[] = recordings?.bar_recordings ?? [];

  return (
    <div className="space-y-5">
      <LiveMarketInputPanel
        dataMode={dataMode}
        marketSnapshot={snapshot}
        tradeSymbol={tradeSymbol}
        rateLimits={rateLimits}
        onTradeSymbolChange={setTradeSymbol}
        onRefresh={handleRefreshSnapshot}
        onChannelChange={handleChannelChange}
      />

      <section className="panel p-4">
        <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">行情录制</div>
        <h2 className="mt-1 text-xl font-semibold">订阅管理</h2>
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <label className="block text-xs text-slate-500">
            添加品种 (vt_symbol)
            <input
              className="focus-ring mt-1 rounded border border-line px-3 py-1.5 text-sm text-ink"
              value={newSymbol}
              onChange={(e) => setNewSymbol(e.target.value.trim().toUpperCase())}
              placeholder="AAPL.SMART"
            />
          </label>
          <button className="focus-ring rounded border border-line px-3 py-1.5 text-sm hover:bg-panel disabled:opacity-50" disabled={busy || !newSymbol}
            onClick={() => handleAddTick(newSymbol)}>添加 Tick 录制</button>
          <button className="focus-ring rounded border border-line px-3 py-1.5 text-sm hover:bg-panel disabled:opacity-50" disabled={busy || !newSymbol}
            onClick={() => handleAddBar(newSymbol)}>添加 Bar 录制</button>
        </div>

        <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div>
            <div className="mb-2 text-sm font-medium">Tick 录制 ({tickSymbols.length})</div>
            {tickSymbols.length === 0 ? (
              <p className="text-sm text-slate-500">无</p>
            ) : (
              <div className="space-y-1">
                {tickSymbols.map((sym) => (
                  <div key={sym} className="flex items-center justify-between rounded border border-line bg-white px-3 py-2 text-sm">
                    <span className="font-medium">{sym}</span>
                    <button className="text-xs text-red-600 hover:underline disabled:opacity-50" disabled={busy} onClick={() => handleRemoveTick(sym)}>移除</button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div>
            <div className="mb-2 text-sm font-medium">Bar 录制 ({barSymbols.length})</div>
            {barSymbols.length === 0 ? (
              <p className="text-sm text-slate-500">无</p>
            ) : (
              <div className="space-y-1">
                {barSymbols.map((sym) => (
                  <div key={sym} className="flex items-center justify-between rounded border border-line bg-white px-3 py-2 text-sm">
                    <span className="font-medium">{sym}</span>
                    <button className="text-xs text-red-600 hover:underline disabled:opacity-50" disabled={busy} onClick={() => handleRemoveBar(sym)}>移除</button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="panel p-4">
        <div className="mb-3">
          <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">数据总览</div>
          <h2 className="mt-1 text-lg font-semibold">本地存储 ({overview.length} 个品种)</h2>
        </div>
        {overview.length === 0 ? (
          <p className="py-4 text-center text-sm text-slate-500">暂无录制数据</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-slate-500">
                  <th className="pb-2 pr-4">品种</th>
                  <th className="pb-2 pr-4">类型</th>
                  <th className="pb-2 pr-4">周期</th>
                  <th className="pb-2 pr-4">条数</th>
                  <th className="pb-2 pr-4">起始</th>
                  <th className="pb-2 pr-4">结束</th>
                  <th className="pb-2 pr-4">来源</th>
                  <th className="pb-2">操作</th>
                </tr>
              </thead>
              <tbody>
                {overview.map((row, i) => (
                  <tr key={i} className="border-b border-line/50">
                    <td className="py-2 pr-4 font-medium">{row.vt_symbol}</td>
                    <td className="py-2 pr-4">{row.data_type}</td>
                    <td className="py-2 pr-4">{row.interval ?? "-"}</td>
                    <td className="py-2 pr-4">{row.count}</td>
                    <td className="py-2 pr-4 text-xs text-slate-500">{row.start ? new Date(row.start).toLocaleString() : "-"}</td>
                    <td className="py-2 pr-4 text-xs text-slate-500">{row.end ? new Date(row.end).toLocaleString() : "-"}</td>
                    <td className="py-2 pr-4 text-xs text-slate-500">{(row.sources ?? []).join(", ") || "-"}</td>
                    <td className="py-2">
                      <button className="text-xs text-red-600 hover:underline disabled:opacity-50" disabled={busy} onClick={() => handleDeleteSymbol(row.vt_symbol)}>删除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

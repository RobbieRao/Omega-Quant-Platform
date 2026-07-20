import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Modal } from "../components/Modal";
import { api } from "../api";
import type {
  DataOverviewRow,
  DataRecordingStatus,
  LiveMarketStatus,
  MarketEvent,
} from "../types";
import { optionalDecimal } from "../utils/format";

const HISTORY_INTERVALS = ["1m", "5m", "15m", "30m", "1h", "1d"];

function defaultHistoryStart(): string {
  const d = new Date();
  d.setMonth(d.getMonth() - 6);
  return d.toISOString().slice(0, 10);
}

function defaultHistoryEnd(): string {
  return new Date().toISOString().slice(0, 10);
}

const DEFAULT_WATCHLIST = ["AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ"];
const WATCHLIST_STORAGE_KEY = "omega:market-watch:symbols";

function loadStoredWatchlist(): string[] {
  if (typeof window === "undefined") return DEFAULT_WATCHLIST;
  try {
    const raw = window.localStorage.getItem(WATCHLIST_STORAGE_KEY);
    if (!raw) return DEFAULT_WATCHLIST;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return DEFAULT_WATCHLIST;
    return Array.from(new Set(parsed.map((x) => String(x).trim().toUpperCase()).filter(Boolean)));
  } catch {
    return DEFAULT_WATCHLIST;
  }
}

function saveWatchlist(symbols: string[]): void {
  if (typeof window === "undefined") return;
  try { window.localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(symbols)); } catch { /* ignore */ }
}

function symbolToVtSymbol(symbol: string): string {
  return symbol.includes(".") ? symbol.toUpperCase() : `${symbol.toUpperCase()}.SMART`;
}

function vtSymbolToSymbol(vt: string): string {
  return vt.split(".")[0].toUpperCase();
}

type SessionStatus = Awaited<ReturnType<typeof api.marketSessionStatus>>;

export default function MarketWatchPage() {
  const [symbols, setSymbols] = useState<string[]>(() => loadStoredWatchlist());
  const [snapshots, setSnapshots] = useState<Record<string, MarketEvent | null>>({});
  const [status, setStatus] = useState<LiveMarketStatus | null>(null);
  const [session, setSession] = useState<SessionStatus | null>(null);
  const [recordings, setRecordings] = useState<DataRecordingStatus | null>(null);
  const [overview, setOverview] = useState<DataOverviewRow[]>([]);
  const [newSymbol, setNewSymbol] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollMs, setPollMs] = useState(2000);
  const symbolsRef = useRef(symbols);
  symbolsRef.current = symbols;

  // History download modal state
  const [historyTarget, setHistoryTarget] = useState<string | null>(null);
  const [historyForm, setHistoryForm] = useState({
    interval: "1h",
    start: defaultHistoryStart(),
    end: defaultHistoryEnd(),
    limit: "3000",
  });
  const [historyMsg, setHistoryMsg] = useState<string | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false);

  const tickSet = useMemo(
    () => new Set((recordings?.tick_recordings ?? []).map(vtSymbolToSymbol)),
    [recordings],
  );
  const barSet = useMemo(
    () => new Set((recordings?.bar_recordings ?? []).map(vtSymbolToSymbol)),
    [recordings],
  );

  const refreshStatus = useCallback(async () => {
    try { setStatus(await api.liveMarketStatus()); } catch { /* ignore */ }
    try { setSession(await api.marketSessionStatus()); } catch { /* ignore */ }
  }, []);

  const refreshRecordings = useCallback(async () => {
    try { setRecordings(await api.dataRecordings()); } catch { /* ignore */ }
    try { setOverview(await api.dataOverview()); } catch { /* ignore */ }
  }, []);

  const fetchAllSnapshots = useCallback(async () => {
    const list = symbolsRef.current;
    if (list.length === 0) return;
    const results = await Promise.allSettled(list.map((s) => api.marketSnapshot(s)));
    const next: Record<string, MarketEvent | null> = {};
    list.forEach((s, i) => {
      const r = results[i];
      next[s] = r.status === "fulfilled" ? r.value : null;
    });
    setSnapshots(next);
  }, []);

  const handleToggleChannels = useCallback(async (quotes: boolean, trades: boolean) => {
    setBusy(true); setError(null);
    try {
      const next = await api.setLiveMarketChannels(quotes, trades);
      setStatus(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const handleSubscribe = useCallback(async (list: string[]) => {
    setBusy(true); setError(null);
    try {
      await api.subscribeLiveMarket(list);
      await refreshStatus();
      await fetchAllSnapshots();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [refreshStatus, fetchAllSnapshots]);

  useEffect(() => {
    const syncBackend = async () => {
      try {
        const current = await api.liveMarketStatus();
        setStatus(current);
        const backendSet = new Set((current.subscribed_symbols || []).map((s) => s.toUpperCase()));
        const localSet = new Set(symbols.map((s) => s.toUpperCase()));
        const toRemove = [...backendSet].filter((s) => !localSet.has(s));
        const toAdd = [...localSet].filter((s) => !backendSet.has(s));
        if (toRemove.length > 0) await api.unsubscribeLiveMarket(toRemove);
        if (toAdd.length > 0) await api.subscribeLiveMarket(toAdd);
        await refreshStatus();
        await refreshRecordings();
        await fetchAllSnapshots();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    };
    syncBackend();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState === "visible") {
        fetchAllSnapshots();
      }
    }, pollMs);
    return () => clearInterval(id);
  }, [pollMs, fetchAllSnapshots]);

  const handleAdd = useCallback(async () => {
    const s = newSymbol.trim().toUpperCase();
    if (!s || symbols.includes(s)) { setNewSymbol(""); return; }
    const next = [...symbols, s];
    setSymbols(next);
    saveWatchlist(next);
    setNewSymbol("");
    await handleSubscribe(next);
  }, [newSymbol, symbols, handleSubscribe]);

  const handleRemove = useCallback(async (s: string) => {
    const next = symbols.filter((x) => x !== s);
    setSymbols(next);
    saveWatchlist(next);
    setSnapshots((cur) => {
      const out = { ...cur };
      delete out[s];
      return out;
    });
    setBusy(true); setError(null);
    try {
      await api.unsubscribeLiveMarket([s]);
      await refreshStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [symbols, refreshStatus]);

  const handleToggleTickRecording = useCallback(async (symbol: string, enabled: boolean) => {
    const vt = symbolToVtSymbol(symbol);
    setBusy(true); setError(null);
    try {
      if (enabled) await api.addTickRecording(vt);
      else await api.removeTickRecording(vt);
      await refreshRecordings();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [refreshRecordings]);

  const handleToggleBarRecording = useCallback(async (symbol: string, enabled: boolean) => {
    const vt = symbolToVtSymbol(symbol);
    setBusy(true); setError(null);
    try {
      if (enabled) await api.addBarRecording(vt);
      else await api.removeBarRecording(vt);
      await refreshRecordings();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [refreshRecordings]);

  const openHistoryModal = useCallback((symbol: string) => {
    setHistoryTarget(symbol);
    setHistoryMsg(null);
    setHistoryForm({
      interval: "1h",
      start: defaultHistoryStart(),
      end: defaultHistoryEnd(),
      limit: "3000",
    });
  }, []);

  const handleDownloadHistory = useCallback(async () => {
    if (!historyTarget) return;
    setHistoryBusy(true);
    setHistoryMsg(null);
    try {
      const limit = Number(historyForm.limit) || null;
      const res = await api.downloadTigerHistory({
        vt_symbol: symbolToVtSymbol(historyTarget),
        interval: historyForm.interval,
        start: historyForm.start ? new Date(historyForm.start).toISOString() : null,
        end: historyForm.end ? new Date(historyForm.end).toISOString() : null,
        product: "equity",
        limit,
      });
      const inserted = res?.bars_inserted ?? res?.rows_received ?? 0;
      const skipped = res?.bars_skipped ?? 0;
      setHistoryMsg(`已拉取 ${inserted} 条 (重复跳过 ${skipped})`);
      await refreshRecordings();
    } catch (e) {
      setHistoryMsg(`下载失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setHistoryBusy(false);
    }
  }, [historyTarget, historyForm, refreshRecordings]);

  const handleDeleteSymbolData = useCallback(async (vtSymbol: string) => {
    if (!confirm(`确认删除 ${vtSymbol} 的全部 tick/bar 历史数据？此操作不可恢复。`)) return;
    setBusy(true); setError(null);
    try {
      await api.deleteSymbolData(vtSymbol);
      await refreshRecordings();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [refreshRecordings]);

  const connected = Boolean(status?.connected);
  const sessionClosed = session ? session.session.startsWith("closed") : false;
  const sessionTint = sessionClosed ? "border-slate-300 bg-slate-50/60" : "border-emerald-300 bg-emerald-50/40";

  return (
    <div className="space-y-5">
      {error && <p className="rounded border border-danger/30 bg-red-50 px-3 py-2 text-sm text-danger">{error}</p>}

      {session && (
        <div className={`panel ${sessionTint} p-3 text-xs`}>
          <div className="flex flex-wrap items-center gap-2 font-semibold">
            <span className={sessionClosed ? "text-slate-700" : "text-emerald-900"}>
              {sessionClosed ? "🌙" : "🟢"} 美股当前会话: {session.session_label}
            </span>
            <span className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[10px] font-normal text-slate-600">
              ET {new Date(session.now_et).toLocaleTimeString("en-US", { hour12: false })}
            </span>
            {session.next_session_at_utc && (
              <span className="rounded border border-blue-200 bg-blue-50 px-2 py-0.5 text-[10px] font-normal text-blue-800">
                下次 {session.next_session}: {new Date(session.next_session_at_utc).toLocaleString(undefined, { hour12: false })}
              </span>
            )}
          </div>
          <p className="mt-1 text-[11px] text-slate-600">{session.hint}</p>
        </div>
      )}

      <section className="panel p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">实时行情 + 录制</div>
            <h2 className="mt-1 text-xl font-semibold">Massive 行情盘口</h2>
            <div className="mt-1 flex flex-wrap gap-2 text-xs">
              <span className={`rounded border px-2 py-0.5 ${connected ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
                {connected ? "● Massive 已连接" : "○ 未连接"}
              </span>
              <span className="rounded border border-line bg-panel px-2 py-0.5 text-slate-600">已订阅 {status?.subscribed_symbols?.length ?? 0} 个</span>
              <button
                type="button"
                disabled={busy}
                onClick={() => handleToggleChannels(!status?.subscribe_quotes, !!status?.subscribe_trades)}
                className={`rounded border px-2 py-0.5 transition ${status?.subscribe_quotes ? "border-emerald-300 bg-emerald-50 text-emerald-800" : "border-slate-300 bg-white text-slate-500"}`}
                title="切换 quotes (bid/ask) 通道。关闭可显著降低 WS 流量"
              >
                Q 报价 {status?.subscribe_quotes ? "● 开" : "○ 关"}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => handleToggleChannels(!!status?.subscribe_quotes, !status?.subscribe_trades)}
                className={`rounded border px-2 py-0.5 transition ${status?.subscribe_trades ? "border-emerald-300 bg-emerald-50 text-emerald-800" : "border-slate-300 bg-white text-slate-500"}`}
                title="切换 trades (逐笔成交) 通道。关闭可显著降低 WS 流量"
              >
                T 逐笔 {status?.subscribe_trades ? "● 开" : "○ 关"}
              </button>
              {status?.throttle_ms !== undefined && status.throttle_ms > 0 && (
                <span className="rounded border border-line bg-panel px-2 py-0.5 text-slate-600" title="下游每标的最低推送间隔，UI 快照不受影响">
                  节流 {Math.round(status.throttle_ms)}ms
                  {status.events_throttled !== undefined && status.events_received !== undefined && status.events_received > 0
                    ? ` · 抑制 ${Math.round((status.events_throttled / status.events_received) * 100)}%`
                    : ""}
                </span>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="text-xs text-slate-500">
              刷新间隔 (ms):
              <input type="number" className="focus-ring ml-2 w-20 rounded border border-line px-2 py-1 text-sm" value={pollMs} min={500} step={500}
                onChange={(e) => setPollMs(Math.max(500, Number(e.target.value) || 2000))} />
            </label>
            <button className="focus-ring rounded border border-line px-3 py-1.5 text-sm hover:bg-panel disabled:opacity-50" disabled={busy} onClick={() => handleSubscribe(symbols)}>重新订阅</button>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="block text-xs text-slate-500">
            添加标的
            <input className="focus-ring mt-1 w-44 rounded border border-line px-3 py-1.5 text-sm" value={newSymbol}
              onChange={(e) => setNewSymbol(e.target.value.trim().toUpperCase())}
              onKeyDown={(e) => { if (e.key === "Enter") handleAdd(); }}
              placeholder="例如 NVDA" />
          </label>
          <button className="focus-ring rounded border border-accent bg-accent px-3 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-50"
            disabled={busy || !newSymbol} onClick={handleAdd}>+ 添加</button>
        </div>
      </section>

      <section className="panel p-4">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-slate-500">
                <th className="pb-2 pr-4">标的</th>
                <th className="pb-2 pr-4">最新价</th>
                <th className="pb-2 pr-4">买一 / 卖一</th>
                <th className="pb-2 pr-4">买卖价差</th>
                <th className="pb-2 pr-4">数据质量</th>
                <th className="pb-2 pr-4">最近更新</th>
                <th className="pb-2 pr-4 text-center" title="录制 Tick 到本地数据库">Tick 录</th>
                <th className="pb-2 pr-4 text-center" title="录制 K 线 Bar 到本地数据库">Bar 录</th>
                <th className="pb-2">操作</th>
              </tr>
            </thead>
            <tbody>
              {symbols.length === 0 ? (
                <tr><td colSpan={9} className="py-6 text-center text-slate-500">添加标的开始订阅</td></tr>
              ) : (
                symbols.map((s) => {
                  const snap = snapshots[s];
                  const spread = snap?.bid && snap?.ask ? snap.ask - snap.bid : null;
                  const stale = !snap || (snap.received_at && Date.now() - new Date(snap.received_at).getTime() > 30_000);
                  const tickOn = tickSet.has(s);
                  const barOn = barSet.has(s);
                  return (
                    <tr key={s} className="border-b border-line/50">
                      <td className="py-2 pr-4 font-medium">{s}</td>
                      <td className={`py-2 pr-4 font-mono ${stale ? "text-slate-400" : "text-ink"}`}>{snap?.price !== null && snap?.price !== undefined ? optionalDecimal(snap.price, 2) : "—"}</td>
                      <td className="py-2 pr-4 font-mono text-xs">
                        {snap?.bid ? <span className="text-emerald-700">{optionalDecimal(snap.bid, 2)}</span> : "—"}
                        <span className="mx-1 text-slate-300">/</span>
                        {snap?.ask ? <span className="text-red-700">{optionalDecimal(snap.ask, 2)}</span> : "—"}
                      </td>
                      <td className="py-2 pr-4 font-mono text-xs">{spread !== null ? optionalDecimal(spread, 3) : "—"}</td>
                      <td className="py-2 pr-4 text-xs">
                        {snap?.data_quality ? (
                          snap.data_quality.startsWith("delayed_")
                            ? <span className="rounded border border-amber-200 bg-amber-50 px-2 py-0.5 text-amber-800">Tiger 延时 15min</span>
                            : snap.data_quality === "massive_rest_snapshot"
                              ? <span className="rounded border border-sky-200 bg-sky-50 px-2 py-0.5 text-sky-800">Massive REST</span>
                              : <span className="rounded border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-emerald-800">实时 WS</span>
                        ) : <span className="text-slate-400">—</span>}
                      </td>
                      <td className="py-2 pr-4 text-xs text-slate-500">{snap?.received_at ? new Date(snap.received_at).toLocaleTimeString() : "—"}</td>
                      <td className="py-2 pr-4 text-center">
                        <input
                          type="checkbox"
                          className="focus-ring cursor-pointer accent-emerald-600 disabled:opacity-50"
                          checked={tickOn}
                          disabled={busy}
                          onChange={(e) => handleToggleTickRecording(s, e.target.checked)}
                        />
                      </td>
                      <td className="py-2 pr-4 text-center">
                        <input
                          type="checkbox"
                          className="focus-ring cursor-pointer accent-sky-600 disabled:opacity-50"
                          checked={barOn}
                          disabled={busy}
                          onChange={(e) => handleToggleBarRecording(s, e.target.checked)}
                        />
                      </td>
                      <td className="py-2">
                        <button className="text-xs text-sky-700 hover:underline disabled:opacity-50 mr-2" disabled={busy} onClick={() => openHistoryModal(s)}>拉历史</button>
                        <button className="text-xs text-red-600 hover:underline disabled:opacity-50" disabled={busy} onClick={() => handleRemove(s)}>移除</button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">本地历史数据</div>
            <h2 className="mt-1 text-lg font-semibold">已落盘 ({overview.length} 个品种)</h2>
          </div>
          <button className="focus-ring rounded border border-line px-3 py-1.5 text-xs hover:bg-panel disabled:opacity-50"
            disabled={busy} onClick={refreshRecordings}>刷新</button>
        </div>
        {overview.length === 0 ? (
          <p className="py-4 text-center text-sm text-slate-500">暂无录制数据。勾选上方表格的 Tick/Bar 复选框开始录制。</p>
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
                      <button className="text-xs text-red-600 hover:underline disabled:opacity-50" disabled={busy} onClick={() => handleDeleteSymbolData(row.vt_symbol)}>删除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <Modal
        open={!!historyTarget}
        onClose={() => setHistoryTarget(null)}
        title={historyTarget ? `从 TigerOpen 拉历史 bars — ${historyTarget}` : ""}
        footer={
          <>
            <button className="rounded border border-line bg-white px-4 py-1.5 text-sm hover:bg-panel" onClick={() => setHistoryTarget(null)}>关闭</button>
            <button className="rounded border border-accent bg-accent px-4 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-50"
              disabled={historyBusy} onClick={handleDownloadHistory}>{historyBusy ? "下载中…" : "下载"}</button>
          </>
        }
      >
        {historyTarget && (
          <div className="space-y-3">
            <p className="text-xs text-slate-500">
              通过 Tiger SDK <code>get_bars_by_page</code> 拉取历史 bars 写入本地 RecordedBar 表。
              策略 init 时会自动 warm-up 这些数据，避免冷启动等待。
            </p>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-xs text-slate-500">
                interval
                <select className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm"
                  value={historyForm.interval}
                  onChange={(e) => setHistoryForm((f) => ({ ...f, interval: e.target.value }))}>
                  {HISTORY_INTERVALS.map((i) => <option key={i} value={i}>{i}</option>)}
                </select>
              </label>
              <label className="block text-xs text-slate-500">
                limit (条数上限)
                <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm"
                  value={historyForm.limit}
                  onChange={(e) => setHistoryForm((f) => ({ ...f, limit: e.target.value }))} />
              </label>
              <label className="block text-xs text-slate-500">
                start (UTC date)
                <input type="date" className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm"
                  value={historyForm.start}
                  onChange={(e) => setHistoryForm((f) => ({ ...f, start: e.target.value }))} />
              </label>
              <label className="block text-xs text-slate-500">
                end (UTC date)
                <input type="date" className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm"
                  value={historyForm.end}
                  onChange={(e) => setHistoryForm((f) => ({ ...f, end: e.target.value }))} />
              </label>
            </div>
            {historyMsg && (
              <p className={`rounded border px-3 py-2 text-xs ${historyMsg.startsWith("下载失败") ? "border-danger/30 bg-red-50 text-danger" : "border-emerald-200 bg-emerald-50 text-emerald-800"}`}>
                {historyMsg}
              </p>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}

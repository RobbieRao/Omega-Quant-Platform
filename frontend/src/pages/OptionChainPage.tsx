import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { OptionChain } from "../types";
import { optionalDecimal } from "../utils/format";

export default function OptionChainPage() {
  const [portfolio, setPortfolio] = useState("omega_default");
  const [underlying, setUnderlying] = useState("AAPL");
  const [expiry, setExpiry] = useState("");
  const [chain, setChain] = useState<OptionChain | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchChain = useCallback(async () => {
    setError(null);
    if (!underlying || !expiry) { setError("请填 underlying 与 expiry"); return; }
    setBusy(true);
    try {
      const c = await api.optionChain(portfolio, underlying, expiry);
      setChain(c);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [portfolio, underlying, expiry]);

  useEffect(() => {
    // try defaults but don't error on first mount
    if (expiry) fetchChain();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const underlyingPrice = chain?.underlying_price ?? null;
  const rows = chain?.rows ?? [];

  return (
    <div className="space-y-5">
      {error && <p className="rounded border border-danger/30 bg-red-50 px-3 py-2 text-sm text-danger">{error}</p>}

      <section className="panel p-4">
        <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">期权 T 型报价表</div>
        <h2 className="mt-1 text-xl font-semibold">Option Chain（call 在左 / strike 中 / put 在右）</h2>
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-4">
          <label className="block text-xs text-slate-500">
            组合 portfolio
            <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm" value={portfolio}
              onChange={(e) => setPortfolio(e.target.value.trim())} />
          </label>
          <label className="block text-xs text-slate-500">
            标的 underlying
            <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm" value={underlying}
              onChange={(e) => setUnderlying(e.target.value.trim().toUpperCase())} placeholder="AAPL" />
          </label>
          <label className="block text-xs text-slate-500">
            到期日 expiry
            <input type="date" className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm" value={expiry}
              onChange={(e) => setExpiry(e.target.value)} />
          </label>
          <div className="flex items-end">
            <button className="focus-ring rounded border border-accent bg-accent px-4 py-2 text-sm text-white hover:opacity-90 disabled:opacity-50"
              disabled={busy || !underlying || !expiry} onClick={fetchChain}>
              {busy ? "加载中…" : "拉取报价表"}
            </button>
          </div>
        </div>
        <p className="mt-2 text-xs text-slate-500">
          提示：T 型表数据来自 OptionMaster 已配置的合约。若空，需先在「期权风险」页通过同步 Tiger 持仓或手工添加合约触发组合初始化。
        </p>
      </section>

      {chain && (
        <section className="panel p-4">
          <div className="mb-3 flex flex-wrap items-baseline gap-4">
            <div>
              <span className="text-sm text-slate-500">underlying:</span>{" "}
              <span className="text-lg font-semibold">{chain.underlying}</span>
            </div>
            <div>
              <span className="text-sm text-slate-500">expiry:</span>{" "}
              <span className="font-mono">{chain.expiry}</span>
            </div>
            <div>
              <span className="text-sm text-slate-500">标的现价:</span>{" "}
              <span className="font-mono text-base font-semibold">{underlyingPrice !== null ? `$${optionalDecimal(underlyingPrice, 2)}` : "—"}</span>
            </div>
            <div>
              <span className="text-sm text-slate-500">行权价档数:</span>{" "}
              <span className="font-mono">{rows.length}</span>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b-2 border-line">
                  <th colSpan={6} className="bg-emerald-50 pb-2 text-center text-emerald-900">CALL（看涨）</th>
                  <th className="px-2 pb-2 text-center text-slate-700">行权价</th>
                  <th colSpan={6} className="bg-red-50 pb-2 text-center text-red-900">PUT（看跌）</th>
                </tr>
                <tr className="border-b border-line text-left text-slate-500">
                  <th className="px-2 pb-1">IV</th>
                  <th className="px-2 pb-1">Δ</th>
                  <th className="px-2 pb-1">买</th>
                  <th className="px-2 pb-1">卖</th>
                  <th className="px-2 pb-1">中价</th>
                  <th className="px-2 pb-1">最新</th>
                  <th className="px-2 pb-1 text-center font-semibold text-slate-700">Strike</th>
                  <th className="px-2 pb-1">最新</th>
                  <th className="px-2 pb-1">中价</th>
                  <th className="px-2 pb-1">买</th>
                  <th className="px-2 pb-1">卖</th>
                  <th className="px-2 pb-1">Δ</th>
                  <th className="px-2 pb-1">IV</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr><td colSpan={13} className="py-6 text-center text-slate-500">报价表为空。请先在「期权风险」页同步 Tiger 持仓或添加合约。</td></tr>
                ) : (
                  rows.map((row) => {
                    const itmCall = underlyingPrice !== null && row.strike < underlyingPrice;
                    const itmPut = underlyingPrice !== null && row.strike > underlyingPrice;
                    return (
                      <tr key={row.strike} className="border-b border-line/40 hover:bg-slate-50">
                        <td className={`px-2 py-1 font-mono ${itmCall ? "bg-emerald-50/60" : ""}`}>{optionalDecimal(row.call?.iv, 3)}</td>
                        <td className={`px-2 py-1 font-mono ${itmCall ? "bg-emerald-50/60" : ""}`}>{optionalDecimal(row.call?.delta, 3)}</td>
                        <td className={`px-2 py-1 font-mono ${itmCall ? "bg-emerald-50/60" : ""}`}>{optionalDecimal(row.call?.bid_price, 2)}</td>
                        <td className={`px-2 py-1 font-mono ${itmCall ? "bg-emerald-50/60" : ""}`}>{optionalDecimal(row.call?.ask_price, 2)}</td>
                        <td className={`px-2 py-1 font-mono ${itmCall ? "bg-emerald-50/60" : ""}`}>{optionalDecimal(row.call?.mark_price, 2)}</td>
                        <td className={`px-2 py-1 font-mono ${itmCall ? "bg-emerald-50/60" : ""}`}>{optionalDecimal(row.call?.last_price, 2)}</td>
                        <td className="px-2 py-1 text-center font-mono font-semibold text-slate-800">${optionalDecimal(row.strike, 2)}</td>
                        <td className={`px-2 py-1 font-mono ${itmPut ? "bg-red-50/60" : ""}`}>{optionalDecimal(row.put?.last_price, 2)}</td>
                        <td className={`px-2 py-1 font-mono ${itmPut ? "bg-red-50/60" : ""}`}>{optionalDecimal(row.put?.mark_price, 2)}</td>
                        <td className={`px-2 py-1 font-mono ${itmPut ? "bg-red-50/60" : ""}`}>{optionalDecimal(row.put?.bid_price, 2)}</td>
                        <td className={`px-2 py-1 font-mono ${itmPut ? "bg-red-50/60" : ""}`}>{optionalDecimal(row.put?.ask_price, 2)}</td>
                        <td className={`px-2 py-1 font-mono ${itmPut ? "bg-red-50/60" : ""}`}>{optionalDecimal(row.put?.delta, 3)}</td>
                        <td className={`px-2 py-1 font-mono ${itmPut ? "bg-red-50/60" : ""}`}>{optionalDecimal(row.put?.iv, 3)}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          <div className="mt-2 flex gap-4 text-xs text-slate-500">
            <span><span className="inline-block h-3 w-3 rounded bg-emerald-50/60 align-middle"></span> 实值 Call (strike &lt; spot)</span>
            <span><span className="inline-block h-3 w-3 rounded bg-red-50/60 align-middle"></span> 实值 Put (strike &gt; spot)</span>
          </div>
        </section>
      )}
    </div>
  );
}

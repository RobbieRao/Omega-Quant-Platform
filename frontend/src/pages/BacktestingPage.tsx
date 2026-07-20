import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const PROMOTE_STORAGE_KEY = "omega:cta-promote-from-backtest";
import { CandlestickChart } from "../components/CandlestickChart";
import { Heatmap } from "../components/Heatmap";
import { api } from "../api";
import type { CtaBacktestResult, CtaStrategyMetadata, DataOverviewRow } from "../types";
import { optionalDecimal, optionalMoney, optionalPct } from "../utils/format";

const INTERVALS = ["1m", "5m", "15m", "30m", "1h", "1d"];

export default function BacktestingPage() {
  const navigate = useNavigate();
  const [templates, setTemplates] = useState<CtaStrategyMetadata[]>([]);
  const [overview, setOverview] = useState<DataOverviewRow[]>([]);
  const [result, setResult] = useState<CtaBacktestResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloadInfo, setDownloadInfo] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [optResult, setOptResult] = useState<Awaited<ReturnType<typeof api.optimizeCtaGeneric>> | null>(null);
  const [wfResult, setWfResult] = useState<Awaited<ReturnType<typeof api.walkForwardCta>> | null>(null);
  const [wfBusy, setWfBusy] = useState(false);
  const [wfForm, setWfForm] = useState({ n_folds: 4, is_fraction: 0.6 });
  const [mcResult, setMcResult] = useState<Awaited<ReturnType<typeof api.monteCarloCta>> | null>(null);
  const [mcBusy, setMcBusy] = useState(false);
  const [mcForm, setMcForm] = useState<{ n_simulations: number; method: "trade_shuffle" | "bootstrap" }>({ n_simulations: 500, method: "trade_shuffle" });
  const [optimizing, setOptimizing] = useState(false);
  const [optAlgorithm, setOptAlgorithm] = useState<"brute_force" | "genetic_algorithm">("brute_force");
  const [optTarget, setOptTarget] = useState<"total_return" | "final_equity" | "net_pnl" | "sharpe_ratio" | "annual_return" | "return_drawdown_ratio" | "max_drawdown" | "trade_count" | "win_rate">("sharpe_ratio");
  const [optGen, setOptGen] = useState({ population_size: 20, generations: 10, crossover_prob: 0.7, mutation_prob: 0.3 });
  type ParamConfig = { mode: "fixed" | "values" | "range"; fixed?: string; values?: string; low?: string; high?: string; step?: string; type?: "int" | "float" };
  const [paramConfigs, setParamConfigs] = useState<Record<string, ParamConfig>>({});

  const [form, setForm] = useState({
    vt_symbol: "AAPL.SMART",
    strategy_class: "",
    interval: "1d",
    data_source: "bar" as "bar" | "tick",
    start: "",
    end: "",
    capital: "100000",
    rate: "0.0005",
    slippage: "0.01",
    size: "1",
    fee_model: "tiger_stock" as "percent" | "tiger_stock" | "tiger_option" | "custom",
    commission_per_unit: "0.0049",
    commission_min_per_order: "0.99",
    platform_fee_per_unit: "0.005",
    platform_fee_min_per_order: "1.0",
    position_sizing_mode: "full_capital" as "strategy" | "full_capital" | "fixed_pct" | "fixed_dollars",
    position_sizing_value: "1.0",
  });
  // Per-strategy parameter overrides (string form, parsed at submit). Keyed by parameter name.
  const [paramOverrides, setParamOverrides] = useState<Record<string, string>>({});

  const [download, setDownload] = useState({
    vt_symbol: "AAPL.SMART",
    interval: "1d",
    product: "equity" as "equity" | "option",
    start: "",
    end: "",
    limit: "5000",
  });

  const refreshOverview = useCallback(async () => {
    const o = await api.dataOverview();
    setOverview(o);
  }, []);

  useEffect(() => {
    Promise.allSettled([api.ctaStrategies(), api.dataOverview()]).then(([t, o]) => {
      if (t.status === "fulfilled") {
        setTemplates(t.value);
        if (t.value.length > 0) setForm((f) => (f.strategy_class ? f : { ...f, strategy_class: t.value[0].class_name }));
      }
      if (o.status === "fulfilled") setOverview(o.value);
    });
  }, []);

  const buildBaseSetting = useCallback((): Record<string, unknown> => {
    const setting: Record<string, unknown> = {};
    for (const [name, raw] of Object.entries(paramOverrides)) {
      if (raw === "" || raw === undefined) continue;
      const n = Number(raw);
      setting[name] = Number.isFinite(n) ? n : raw;
    }
    return setting;
  }, [paramOverrides]);

  // Batch backtest (cross-symbol)
  const [batchSymbols, setBatchSymbols] = useState("SPY.SMART, QQQ.SMART, NVDA.SMART, MU.SMART, AAPL.SMART");
  type BatchRow = { vt_symbol: string; sharpe: number | null; total_return: number | null; max_drawdown: number | null; trade_count: number | null; win_rate: number | null; net_pnl: number | null; bh_return: number | null; error: string | null };
  const [batchResults, setBatchResults] = useState<BatchRow[]>([]);
  const [batchRunning, setBatchRunning] = useState(false);

  const handleBatchRun = useCallback(async () => {
    setError(null);
    const symbols = batchSymbols.split(/[,;\n\s]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
    if (symbols.length === 0) return;
    setBatchRunning(true);
    setBatchResults([]);
    try {
      const setting = buildBaseSetting();
      const out: BatchRow[] = [];
      for (const sym of symbols) {
        const vt = sym.includes(".") ? sym : `${sym}.SMART`;
        try {
          const r = await api.runCtaBacktest({
            vt_symbol: vt,
            interval: form.interval,
            data_source: form.data_source,
            strategy_class: form.strategy_class || undefined,
            strategy_setting: setting,
            start: form.start || null,
            end: form.end || null,
            capital: Number(form.capital) || 100_000,
            rate: Number(form.rate),
            slippage: Number(form.slippage),
            size: Number(form.size) || 1,
            fee_model: form.fee_model,
            commission_per_unit: Number(form.commission_per_unit) || 0,
            commission_min_per_order: Number(form.commission_min_per_order) || 0,
            platform_fee_per_unit: Number(form.platform_fee_per_unit) || 0,
            platform_fee_min_per_order: Number(form.platform_fee_min_per_order) || 0,
            position_sizing_mode: form.position_sizing_mode,
            position_sizing_value: Number(form.position_sizing_value) || 1.0,
          });
          out.push({
            vt_symbol: vt,
            sharpe: r.sharpe_ratio ?? null,
            total_return: r.total_return ?? null,
            max_drawdown: r.max_drawdown ?? null,
            trade_count: r.trade_count ?? null,
            win_rate: r.win_rate ?? null,
            net_pnl: r.net_pnl ?? null,
            bh_return: r.buy_and_hold?.total_return ?? null,
            error: null,
          });
        } catch (e) {
          out.push({
            vt_symbol: vt, sharpe: null, total_return: null, max_drawdown: null,
            trade_count: null, win_rate: null, net_pnl: null, bh_return: null,
            error: e instanceof Error ? e.message : String(e),
          });
        }
        // Update incrementally so user sees progress
        setBatchResults([...out].sort((a, b) => (b.sharpe ?? -99) - (a.sharpe ?? -99)));
      }
    } finally {
      setBatchRunning(false);
    }
  }, [batchSymbols, form, buildBaseSetting]);

  const handleRun = useCallback(async () => {
    setError(null);
    setRunning(true);
    try {
      const setting = buildBaseSetting();
      const r = await api.runCtaBacktest({
        vt_symbol: form.vt_symbol,
        interval: form.interval,
        data_source: form.data_source,
        strategy_class: form.strategy_class || undefined,
        strategy_setting: setting,
        start: form.start || null,
        end: form.end || null,
        capital: Number(form.capital) || 100_000,
        rate: Number(form.rate),
        slippage: Number(form.slippage),
        size: Number(form.size) || 1,
        fee_model: form.fee_model,
        commission_per_unit: Number(form.commission_per_unit) || 0,
        commission_min_per_order: Number(form.commission_min_per_order) || 0,
        platform_fee_per_unit: Number(form.platform_fee_per_unit) || 0,
        platform_fee_min_per_order: Number(form.platform_fee_min_per_order) || 0,
        position_sizing_mode: form.position_sizing_mode,
        position_sizing_value: Number(form.position_sizing_value) || 1.0,
      });
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }, [form, buildBaseSetting]);

  const handleDownload = useCallback(async () => {
    setDownloadInfo(null);
    setError(null);
    setDownloading(true);
    try {
      const r = await api.downloadTigerHistory({
        vt_symbol: download.vt_symbol,
        interval: download.interval,
        product: download.product,
        start: download.start || null,
        end: download.end || null,
        limit: download.limit ? Number(download.limit) : null,
      });
      setDownloadInfo(`✓ ${r.vt_symbol} ${r.interval} ${r.product}: 收到 ${r.rows_received} 条, 新增 ${r.bars_inserted}, 跳过 ${r.bars_skipped}`);
      await refreshOverview();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDownloading(false);
    }
  }, [download, refreshOverview]);

  const handleOptimize = useCallback(async () => {
    setError(null);
    setOptResult(null);
    setOptimizing(true);
    try {
      // Use per-parameter overrides from the run form as the "fixed background" for params
      // not present in the optimization sweep. The optimizer's paramConfigs override these.
      const setting: Record<string, unknown> = {};
      for (const [name, raw] of Object.entries(paramOverrides)) {
        if (raw === "" || raw === undefined) continue;
        const n = Number(raw);
        setting[name] = Number.isFinite(n) ? n : raw;
      }
      const parseList = (s: string): number[] =>
        s.split(/[\s,，;]+/).map((x) => Number(x.trim())).filter((x) => Number.isFinite(x));
      const parameter_values: Record<string, Array<number | string>> = {};
      const parameter_ranges: Record<string, { low: number; high: number; step?: number; type?: "int" | "float" }> = {};
      for (const [name, cfg] of Object.entries(paramConfigs)) {
        if (cfg.mode === "fixed") {
          const v = Number(cfg.fixed);
          if (Number.isFinite(v)) parameter_values[name] = [v];
        } else if (cfg.mode === "values") {
          const arr = parseList(cfg.values || "");
          if (arr.length > 0) parameter_values[name] = arr;
        } else if (cfg.mode === "range") {
          const low = Number(cfg.low), high = Number(cfg.high), step = Number(cfg.step) || 1;
          if (Number.isFinite(low) && Number.isFinite(high) && low < high) {
            if (optAlgorithm === "genetic_algorithm") {
              parameter_ranges[name] = { low, high, step, type: cfg.type ?? "int" };
            } else {
              const arr: number[] = [];
              for (let v = low; v <= high + 1e-9; v += step) arr.push(cfg.type === "int" ? Math.round(v) : Number(v.toFixed(6)));
              if (arr.length > 0) parameter_values[name] = arr;
            }
          }
        }
      }
      // 至少需要 1 个 varied 参数
      const hasAny = Object.keys(parameter_values).length > 0 || Object.keys(parameter_ranges).length > 0;
      if (optAlgorithm === "brute_force" && !hasAny) {
        throw new Error("至少配置一个参数为「值列表」或「范围」");
      }
      if (optAlgorithm === "genetic_algorithm" && !hasAny) {
        throw new Error("遗传算法至少需要一个参数配置为「范围」或「值列表」");
      }
      const r = await api.optimizeCtaGeneric({
        vt_symbol: form.vt_symbol,
        interval: form.interval,
        data_source: form.data_source,
        strategy_class: form.strategy_class,
        strategy_setting: setting,
        start: form.start || null,
        end: form.end || null,
        capital: Number(form.capital) || 100_000,
        rate: Number(form.rate),
        slippage: Number(form.slippage),
        size: Number(form.size) || 1,
        ...{
          fee_model: form.fee_model,
          commission_per_unit: Number(form.commission_per_unit) || 0,
          commission_min_per_order: Number(form.commission_min_per_order) || 0,
          platform_fee_per_unit: Number(form.platform_fee_per_unit) || 0,
          platform_fee_min_per_order: Number(form.platform_fee_min_per_order) || 0,
          position_sizing_mode: form.position_sizing_mode,
          position_sizing_value: Number(form.position_sizing_value) || 1.0,
        },
        algorithm: optAlgorithm,
        target: optTarget,
        // GA 现在也接受 parameter_values (作为离散候选集合)
        ...(optAlgorithm === "brute_force"
          ? { parameter_values }
          : { parameter_ranges, parameter_values }),
        ...(optAlgorithm === "genetic_algorithm" ? optGen : {}),
      });
      setOptResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setOptimizing(false);
    }
  }, [form, optAlgorithm, optTarget, optGen, paramConfigs, paramOverrides]);

  const handleWalkForward = useCallback(async () => {
    if (!form.vt_symbol || !form.strategy_class) { setError("请先选合约和策略"); return; }
    setWfBusy(true); setError(null);
    try {
      const setting: Record<string, unknown> = {};
      for (const [k, raw] of Object.entries(paramOverrides)) {
        if (raw === "" || raw === undefined) continue;
        const n = Number(raw);
        setting[k] = Number.isFinite(n) ? n : raw;
      }
      const r = await api.walkForwardCta({
        vt_symbol: form.vt_symbol, interval: form.interval, data_source: form.data_source,
        strategy_class: form.strategy_class, strategy_setting: setting,
        start: form.start || null, end: form.end || null, capital: Number(form.capital) || 100_000,
        rate: Number(form.rate), slippage: Number(form.slippage), size: Number(form.size) || 1,
        fee_model: form.fee_model,
        commission_per_unit: Number(form.commission_per_unit) || 0,
        commission_min_per_order: Number(form.commission_min_per_order) || 0,
        platform_fee_per_unit: Number(form.platform_fee_per_unit) || 0,
        platform_fee_min_per_order: Number(form.platform_fee_min_per_order) || 0,
        position_sizing_mode: form.position_sizing_mode,
        position_sizing_value: Number(form.position_sizing_value) || 1.0,
        n_folds: wfForm.n_folds, is_fraction: wfForm.is_fraction,
      });
      setWfResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setWfBusy(false);
    }
  }, [form, paramOverrides, wfForm]);

  // 在所有回测调用里复用：cost model + position sizing 字段
  const costSizingFields = () => ({
    fee_model: form.fee_model,
    commission_per_unit: Number(form.commission_per_unit) || 0,
    commission_min_per_order: Number(form.commission_min_per_order) || 0,
    platform_fee_per_unit: Number(form.platform_fee_per_unit) || 0,
    platform_fee_min_per_order: Number(form.platform_fee_min_per_order) || 0,
    position_sizing_mode: form.position_sizing_mode,
    position_sizing_value: Number(form.position_sizing_value) || 1.0,
  });

  const handleMonteCarlo = useCallback(async () => {
    if (!form.vt_symbol || !form.strategy_class) { setError("请先选合约和策略"); return; }
    setMcBusy(true); setError(null);
    try {
      const setting: Record<string, unknown> = {};
      for (const [k, raw] of Object.entries(paramOverrides)) {
        if (raw === "" || raw === undefined) continue;
        const n = Number(raw);
        setting[k] = Number.isFinite(n) ? n : raw;
      }
      const r = await api.monteCarloCta({
        vt_symbol: form.vt_symbol, interval: form.interval, data_source: form.data_source,
        strategy_class: form.strategy_class, strategy_setting: setting,
        start: form.start || null, end: form.end || null, capital: Number(form.capital) || 100_000,
        rate: Number(form.rate), slippage: Number(form.slippage), size: Number(form.size) || 1,
        ...costSizingFields(),
        n_simulations: mcForm.n_simulations, method: mcForm.method,
      });
      setMcResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setMcBusy(false);
    }
  }, [form, paramOverrides, mcForm]);

  const handleSaveRun = useCallback(() => {
    if (!result) return;
    const label = `${result.vt_symbol} · ${result.strategy_class} · ${new Date().toLocaleTimeString()}`;
    setSavedRuns((rs) => [...rs.slice(-9), { label, result }]); // cap at 10
  }, [result]);

  const handleExportTradesCsv = useCallback(() => {
    if (!result?.trades || result.trades.length === 0) return;
    const headers = ["datetime", "direction", "price", "volume"];
    const lines = [headers.join(",")];
    for (const t of result.trades) {
      lines.push([t["datetime"] ?? "", t["direction"] ?? "", t["price"] ?? "", t["volume"] ?? ""].map((x) => `"${String(x).replace(/"/g, '""')}"`).join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `backtest_${result.vt_symbol}_${result.strategy_class}_trades.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [result]);

  const handleExportEquityCsv = useCallback(() => {
    if (!result?.daily_results || result.daily_results.length === 0) return;
    const headers = ["date", "close_price", "trades", "net_pnl", "balance", "drawdown", "ddpercent"];
    const lines = [headers.join(",")];
    for (const d of result.daily_results) {
      lines.push([d.date, d.close_price, d.trade_count, d.net_pnl, d.balance, d.drawdown, d.ddpercent].join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `backtest_${result.vt_symbol}_${result.strategy_class}_equity.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [result]);

  const handleDelete = useCallback(async (vtSymbol: string) => {
    if (!confirm(`确认删除 ${vtSymbol} 的全部历史数据？`)) return;
    await api.deleteSymbolData(vtSymbol);
    await refreshOverview();
  }, [refreshOverview]);

  const equityCurve = useMemo(() => {
    if (!result?.daily_results) return [];
    return result.daily_results.map((d) => ({
      date: d.date,
      balance: d.balance,
      drawdown: d.drawdown,
    }));
  }, [result]);

  const [chartIndicators, setChartIndicators] = useState<{ ma5: boolean; ma20: boolean; ma60: boolean; boll: boolean }>({ ma5: true, ma20: true, ma60: false, boll: false });
  const [savedRuns, setSavedRuns] = useState<Array<{ label: string; result: CtaBacktestResult }>>([]);
  const [compareSelected, setCompareSelected] = useState<[number, number]>([0, 1]);
  const [priceBars, setPriceBars] = useState<Array<{ timestamp: string | null; open: number; high: number; low: number; close: number; volume: number }>>([]);
  useEffect(() => {
    if (!result?.vt_symbol || !result?.interval) { setPriceBars([]); return; }
    api.fetchBars(result.vt_symbol, result.interval, result.data_range?.start, result.data_range?.end, 5000)
      .then(setPriceBars)
      .catch(() => setPriceBars([]));
  }, [result]);

  const tradeMarkers = useMemo(() => {
    if (!result?.trades) return [];
    return result.trades.slice(0, 500)
      .map((t) => {
        const dt = String(t["datetime"] ?? "");
        const price = Number(t["price"] ?? 0);
        const direction = String(t["direction"] ?? "");
        return { datetime: dt, price, direction };
      })
      .filter((m) => m.datetime && m.price > 0);
  }, [result]);

  const selectedTemplate = templates.find((t) => t.class_name === form.strategy_class);

  return (
    <div className="space-y-5">
      {error && <p className="rounded border border-danger/30 bg-red-50 px-3 py-2 text-sm text-danger">{error}</p>}

      <section className="panel p-4">
        <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">数据下载</div>
        <h2 className="mt-1 text-xl font-semibold">从 TigerOpen 下载历史 K 线</h2>
        <p className="mt-1 text-sm text-slate-500">
          股票走官方 SDK 的 <code className="rounded bg-panel px-1 font-mono">QuoteClient.get_bars_by_page</code> 分页接口（page_size=1000 自动循环到 total）；
          期权走 <code className="rounded bg-panel px-1 font-mono">get_option_bars</code>。请设置「条数上限」为你期望的最大条数 (默认 5000)。
        </p>
        {download.product === "option" && (
          <p className="mt-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            ⚠ 期权合约代码必须是 <span className="font-mono">OCC 格式</span>：
            <span className="font-mono"> 根代码 + 2个空格 + YYMMDD + C/P + 8位行权价</span>。例：
            <span className="font-mono">AAPL  240119C00150000</span> = AAPL 2024-01-19 到期 $150 Call。
            只填 underlying（如 <span className="font-mono">AAPL</span>）会被 Tiger 拒绝。
          </p>
        )}
        {downloadInfo && <p className="mt-2 rounded border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">{downloadInfo}</p>}
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <label className="block text-xs text-slate-500">
            合约代码<span className="ml-1 text-slate-400">
              {download.product === "option" ? "(OCC 期权代码, 例 AAPL  240119C00150000)" : "(品种.交易所)"}
            </span>
            <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={download.vt_symbol}
              onChange={(e) => setDownload((f) => ({ ...f, vt_symbol: e.target.value.toUpperCase() }))}
              placeholder={download.product === "option" ? "AAPL  240119C00150000.SMART" : "例如 AAPL.SMART"} />
          </label>
          <label className="block text-xs text-slate-500">
            类型
            <select className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={download.product}
              onChange={(e) => setDownload((f) => ({ ...f, product: e.target.value as "equity" | "option" }))}>
              <option value="equity">美股股票</option>
              <option value="option">美股期权</option>
            </select>
          </label>
          <label className="block text-xs text-slate-500">
            周期
            <select className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={download.interval}
              onChange={(e) => setDownload((f) => ({ ...f, interval: e.target.value }))}>
              {INTERVALS.map((i) => <option key={i} value={i}>{i}</option>)}
            </select>
          </label>
          <label className="block text-xs text-slate-500">
            开始
            <input type="date" className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={download.start}
              onChange={(e) => setDownload((f) => ({ ...f, start: e.target.value }))} />
          </label>
          <label className="block text-xs text-slate-500">
            结束
            <input type="date" className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={download.end}
              onChange={(e) => setDownload((f) => ({ ...f, end: e.target.value }))} />
          </label>
          <label className="block text-xs text-slate-500">
            条数上限 (total)
            <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={download.limit}
              onChange={(e) => setDownload((f) => ({ ...f, limit: e.target.value }))} placeholder="5000" />
          </label>
        </div>
        <div className="mt-3">
          <button className="focus-ring rounded border border-accent bg-accent px-4 py-2 text-sm text-white hover:opacity-90 disabled:opacity-50"
            onClick={handleDownload} disabled={downloading || !download.vt_symbol}>
            {downloading ? "下载中…" : "下载历史数据"}
          </button>
        </div>
      </section>

      <section className="panel p-4">
        <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">CTA 回测</div>
        <h2 className="mt-1 text-xl font-semibold">回测参数</h2>
        <div className="mt-3">
          <label className="block text-xs text-slate-500">
            <span className="font-medium text-ink">选择本地数据集</span>
            <span className="ml-2 text-slate-400">从下方「本地历史数据」表里挑一行，自动填充合约/类型/周期</span>
            <select
              className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink"
              value=""
              onChange={(e) => {
                const idx = Number(e.target.value);
                if (Number.isNaN(idx) || idx < 0 || idx >= overview.length) return;
                const row = overview[idx];
                setForm((f) => ({
                  ...f,
                  vt_symbol: row.vt_symbol,
                  data_source: row.data_type,
                  interval: row.interval || f.interval,
                  start: row.start ? row.start.slice(0, 10) : f.start,
                  end: row.end ? row.end.slice(0, 10) : f.end,
                }));
              }}
            >
              <option value="">-- 点击选择本地数据集 --</option>
              {overview.map((row, i) => (
                <option key={i} value={i}>
                  {row.vt_symbol} · {row.data_type} · {row.interval ?? "-"} · {row.count} 条 · {row.start?.slice(0, 10)} → {row.end?.slice(0, 10)}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <label className="block text-xs text-slate-500">
            合约代码<span className="ml-1 text-slate-400">(品种.交易所)</span>
            <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.vt_symbol}
              onChange={(e) => setForm((f) => ({ ...f, vt_symbol: e.target.value.trim().toUpperCase() }))} placeholder="例如 AAPL.SMART" />
          </label>
          <label className="block text-xs text-slate-500">
            策略类
            <select className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.strategy_class}
              onChange={(e) => setForm((f) => ({ ...f, strategy_class: e.target.value }))}>
              <option value="">默认</option>
              {templates.map((t) => <option key={t.class_name} value={t.class_name}>{t.display_name || t.class_name}</option>)}
            </select>
          </label>
          <label className="block text-xs text-slate-500">
            K 线周期<span className="ml-1 text-slate-400">(bar 模式生效)</span>
            <select className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.interval}
              onChange={(e) => setForm((f) => ({ ...f, interval: e.target.value }))}>
              {INTERVALS.map((i) => <option key={i} value={i}>{i}</option>)}
            </select>
          </label>
          <label className="block text-xs text-slate-500">
            数据类型
            <select className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.data_source}
              onChange={(e) => setForm((f) => ({ ...f, data_source: e.target.value as "bar" | "tick" }))}>
              <option value="bar">Bar (K 线)</option>
              <option value="tick">Tick (逐笔)</option>
            </select>
          </label>
          <label className="block text-xs text-slate-500">
            初始资金 ($)
            <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.capital}
              onChange={(e) => setForm((f) => ({ ...f, capital: e.target.value }))} placeholder="100000" />
          </label>
          <label className="block text-xs text-slate-500" title="每股成交价格相对委托价的滑动。0.01 表示模拟实际成交价比目标价差 $0.01/股（模拟挂单 vs market spread / queue 损失）。提高这个值模拟差流动性。">
            滑点<span className="ml-1 text-slate-400">($/股, hover 看说明)</span>
            <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.slippage}
              onChange={(e) => setForm((f) => ({ ...f, slippage: e.target.value }))} placeholder="0.01" />
          </label>
          <label className="block text-xs text-slate-500">
            合约乘数<span className="ml-1 text-slate-400">(股票=1, 期权=100)</span>
            <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.size}
              onChange={(e) => setForm((f) => ({ ...f, size: e.target.value }))} placeholder="1" />
          </label>
          <label className="block text-xs text-slate-500">
            开始日期
            <input type="date" className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.start}
              onChange={(e) => setForm((f) => ({ ...f, start: e.target.value }))} />
          </label>
          <label className="block text-xs text-slate-500">
            结束日期
            <input type="date" className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.end}
              onChange={(e) => setForm((f) => ({ ...f, end: e.target.value }))} />
          </label>
        </div>

        {/* 成本模型 + 仓位规则 — 真实费率 + 全仓选项 */}
        <div className="mt-4 rounded border border-line bg-panel/40 p-3">
          <div className="mb-2 flex items-baseline gap-3">
            <span className="text-sm font-medium">💰 手续费模型</span>
            <span className="text-xs text-slate-500">Tiger 真实费率: 股票每单最低 $1.99 / 期权每单最低 $0.99</span>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <label className="block text-xs text-slate-500">
              费率模型
              <select className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink"
                value={form.fee_model}
                onChange={(e) => {
                  const v = e.target.value as typeof form.fee_model;
                  setForm((f) => {
                    if (v === "tiger_stock") return { ...f, fee_model: v, commission_per_unit: "0.0049", commission_min_per_order: "0.99", platform_fee_per_unit: "0.005", platform_fee_min_per_order: "1.0" };
                    if (v === "tiger_option") return { ...f, fee_model: v, commission_per_unit: "0", commission_min_per_order: "0", platform_fee_per_unit: "0.3", platform_fee_min_per_order: "0.99" };
                    return { ...f, fee_model: v };
                  });
                }}>
                <option value="tiger_stock">Tiger 美股股票</option>
                <option value="tiger_option">Tiger 美股期权</option>
                <option value="custom">自定义 (per-unit + min)</option>
                <option value="percent">老式 (rate × notional)</option>
              </select>
            </label>
            {form.fee_model !== "percent" && (
              <>
                <label className="block text-xs text-slate-500">
                  佣金 / 单位
                  <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.commission_per_unit}
                    onChange={(e) => setForm((f) => ({ ...f, commission_per_unit: e.target.value }))} disabled={form.fee_model !== "custom"} />
                </label>
                <label className="block text-xs text-slate-500">
                  佣金 / 单 最低
                  <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.commission_min_per_order}
                    onChange={(e) => setForm((f) => ({ ...f, commission_min_per_order: e.target.value }))} disabled={form.fee_model !== "custom"} />
                </label>
                <label className="block text-xs text-slate-500">
                  平台费 / 单位
                  <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.platform_fee_per_unit}
                    onChange={(e) => setForm((f) => ({ ...f, platform_fee_per_unit: e.target.value }))} disabled={form.fee_model !== "custom"} />
                </label>
                <label className="block text-xs text-slate-500">
                  平台费 / 单 最低
                  <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.platform_fee_min_per_order}
                    onChange={(e) => setForm((f) => ({ ...f, platform_fee_min_per_order: e.target.value }))} disabled={form.fee_model !== "custom"} />
                </label>
              </>
            )}
            {form.fee_model === "percent" && (
              <label className="col-span-2 block text-xs text-slate-500">
                费率<span className="ml-1 text-slate-400">(成交额比例, 0.0005=5bps)</span>
                <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.rate}
                  onChange={(e) => setForm((f) => ({ ...f, rate: e.target.value }))} placeholder="0.0005" />
              </label>
            )}
          </div>

          <div className="mt-4 mb-2 flex items-baseline gap-3">
            <span className="text-sm font-medium">📊 仓位规则</span>
            <span className="text-xs text-slate-500">策略默认每次只买 1 股 = 0.2% 仓位；用「满仓」让回测真实反映策略潜力</span>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <label className="block text-xs text-slate-500">
              仓位模式
              <select className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink"
                value={form.position_sizing_mode}
                onChange={(e) => setForm((f) => ({ ...f, position_sizing_mode: e.target.value as typeof form.position_sizing_mode }))}>
                <option value="full_capital">满仓 (现金 ÷ 价格)</option>
                <option value="fixed_pct">固定比例 (X% × 现金)</option>
                <option value="fixed_dollars">固定美元金额</option>
                <option value="strategy">尊重策略 (用策略写死的 volume)</option>
              </select>
            </label>
            {form.position_sizing_mode === "fixed_pct" && (
              <label className="block text-xs text-slate-500">
                比例 (0–1)
                <input type="number" step="0.01" min="0" max="1" className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink"
                  value={form.position_sizing_value}
                  onChange={(e) => setForm((f) => ({ ...f, position_sizing_value: e.target.value }))} placeholder="0.5" />
              </label>
            )}
            {form.position_sizing_mode === "fixed_dollars" && (
              <label className="block text-xs text-slate-500">
                美元金额
                <input type="number" min="0" className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink"
                  value={form.position_sizing_value}
                  onChange={(e) => setForm((f) => ({ ...f, position_sizing_value: e.target.value }))} placeholder="10000" />
              </label>
            )}
            <div className="col-span-2 text-[11px] leading-relaxed text-slate-500 sm:col-span-1">
              <p>📚 <span className="font-semibold">滑点</span>: $0.01 = 模拟实际成交比委托价偏离 $0.01/股 (买高一分卖低一分)。</p>
              <p>📚 <span className="font-semibold">满仓</span>: 现金 ÷ 价格 取整后全买。短线策略最常用。</p>
            </div>
          </div>
        </div>
        {selectedTemplate ? (
          <div className="mt-4">
            <div className="mb-2 flex items-baseline gap-3">
              <span className="text-sm font-medium">策略参数：{selectedTemplate.display_name || selectedTemplate.class_name}</span>
              <span className="text-xs text-slate-500">空白 = 使用策略类默认值</span>
              <button
                className="ml-auto focus-ring rounded border border-line px-2 py-0.5 text-xs hover:bg-panel"
                onClick={() => setParamOverrides({})}
              >清空覆盖</button>
            </div>
            {selectedTemplate.parameters.length === 0 ? (
              <p className="rounded border border-line bg-panel px-3 py-2 text-xs text-slate-500">该策略未声明可调参数</p>
            ) : (
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {selectedTemplate.parameters.map((pname) => {
                  const defaultVal = selectedTemplate.parameter_defaults?.[pname];
                  return (
                    <label key={pname} className="block text-xs text-slate-500">
                      <span className="font-mono">{pname}</span>
                      <span className="ml-1 text-slate-400">默认 {String(defaultVal ?? "?")}</span>
                      <input
                        className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm font-mono text-ink"
                        value={paramOverrides[pname] ?? ""}
                        onChange={(e) => setParamOverrides((m) => ({ ...m, [pname]: e.target.value }))}
                        placeholder={String(defaultVal ?? "")}
                      />
                    </label>
                  );
                })}
              </div>
            )}
            {selectedTemplate.description && (
              <p className="mt-2 rounded border border-line bg-panel px-3 py-2 text-xs text-slate-600">{selectedTemplate.description}</p>
            )}
          </div>
        ) : (
          <p className="mt-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">请先选择策略类，才会显示其参数表单</p>
        )}
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            className="focus-ring rounded border border-accent bg-accent px-4 py-2 text-sm text-white hover:opacity-90 disabled:opacity-50"
            onClick={handleRun} disabled={running || !form.vt_symbol}
          >
            {running ? "运行中…" : "运行回测"}
          </button>
        </div>
      </section>

      <section className="panel p-4">
        <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">批量回测</div>
        <h2 className="mt-1 text-xl font-semibold">跨标的同参回测（验证稳健性）</h2>
        <p className="mt-1 text-sm text-slate-500">
          用上面单回测的同一组参数（策略类 + interval + 参数 + 资本/费率），并行/串行跑多个 vt_symbol，
          按 Sharpe 排序展示。**单参数在多标的都正 Sharpe** 比"单标的最佳"更可信，避免过拟合。
        </p>
        <div className="mt-3">
          <label className="block text-xs text-slate-500">
            标的列表（逗号 / 空格 / 换行分隔；裸 ticker 自动补 .SMART）
            <textarea
              className="focus-ring mt-1 h-16 w-full rounded border border-line px-3 py-2 text-sm font-mono"
              value={batchSymbols}
              onChange={(e) => setBatchSymbols(e.target.value)}
            />
          </label>
          <div className="mt-2">
            <button
              className="focus-ring rounded border border-accent bg-accent px-4 py-2 text-sm text-white hover:opacity-90 disabled:opacity-50"
              onClick={handleBatchRun}
              disabled={batchRunning || !form.strategy_class}
            >
              {batchRunning ? `运行中… (${batchResults.length} 已完成)` : "运行批量回测"}
            </button>
          </div>
        </div>
        {batchResults.length > 0 && (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-slate-500">
                  <th className="pb-2 pr-4">标的</th>
                  <th className="pb-2 pr-4">夏普</th>
                  <th className="pb-2 pr-4">总收益</th>
                  <th className="pb-2 pr-4">最大回撤</th>
                  <th className="pb-2 pr-4">胜率</th>
                  <th className="pb-2 pr-4">成交</th>
                  <th className="pb-2 pr-4">净盈亏</th>
                  <th className="pb-2 pr-4">B&H 收益</th>
                  <th className="pb-2 pr-4">vs B&H</th>
                  <th className="pb-2">异常</th>
                </tr>
              </thead>
              <tbody>
                {batchResults.map((r) => {
                  const positive = (r.sharpe ?? -99) > 0;
                  return (
                    <tr key={r.vt_symbol} className={`border-b border-line/50 ${r.error ? "bg-red-50/40" : positive ? "bg-emerald-50/30" : ""}`}>
                      <td className="py-2 pr-4 font-mono">{r.vt_symbol}</td>
                      <td className={`py-2 pr-4 font-medium ${positive ? "text-emerald-700" : "text-slate-600"}`}>{r.sharpe !== null ? r.sharpe.toFixed(2) : "—"}</td>
                      <td className="py-2 pr-4">{r.total_return !== null ? optionalPct(r.total_return) : "—"}</td>
                      <td className="py-2 pr-4">{r.max_drawdown !== null ? optionalPct(r.max_drawdown) : "—"}</td>
                      <td className="py-2 pr-4">{r.win_rate !== null ? optionalPct(r.win_rate) : "—"}</td>
                      <td className="py-2 pr-4">{r.trade_count ?? "—"}</td>
                      <td className="py-2 pr-4">{r.net_pnl !== null ? optionalMoney(r.net_pnl) : "—"}</td>
                      <td className="py-2 pr-4">{r.bh_return !== null ? optionalPct(r.bh_return) : "—"}</td>
                      <td className="py-2 pr-4">{r.total_return !== null && r.bh_return !== null ? optionalPct(r.total_return - r.bh_return) : "—"}</td>
                      <td className="py-2 text-xs text-red-700">{r.error ?? ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {(() => {
              const ok = batchResults.filter((r) => !r.error && r.sharpe !== null);
              const pos = ok.filter((r) => (r.sharpe ?? 0) > 0).length;
              const avg = ok.length ? ok.reduce((s, r) => s + (r.sharpe ?? 0), 0) / ok.length : 0;
              return (
                <p className="mt-2 text-xs text-slate-500">
                  跨 {ok.length} 个标的 · 正 Sharpe {pos}/{ok.length} · 平均 Sharpe {avg.toFixed(2)}
                  {pos === ok.length && ok.length >= 3 && " ✓ 所有标的 Sharpe 为正（稳健性 OK）"}
                </p>
              );
            })()}
          </div>
        )}
      </section>

      <section className="panel p-4">
        <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">参数优化</div>
        <h2 className="mt-1 text-xl font-semibold">通用参数优化（穷举 / 遗传算法）</h2>
        <p className="mt-1 text-sm text-slate-500">
          根据所选策略的参数列表动态生成配置。每个参数可设：
          <span className="ml-1 rounded bg-panel px-1 font-mono">固定值</span>
          <span className="mx-1 rounded bg-panel px-1 font-mono">值列表</span>
          <span className="mr-1 rounded bg-panel px-1 font-mono">范围 (low/high/step)</span>。
          穷举枚举所有组合，遗传算法用 deap NSGA tournament 进化（支持非凸目标）。
        </p>

        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="block text-xs text-slate-500">
            算法
            <select className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm" value={optAlgorithm}
              onChange={(e) => setOptAlgorithm(e.target.value as "brute_force" | "genetic_algorithm")}>
              <option value="brute_force">穷举 (brute force)</option>
              <option value="genetic_algorithm">遗传算法 (GA, deap)</option>
            </select>
          </label>
          <label className="block text-xs text-slate-500">
            优化目标
            <select className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm" value={optTarget}
              onChange={(e) => setOptTarget(e.target.value as typeof optTarget)}>
              <optgroup label="风险调整后收益">
                <option value="sharpe_ratio">夏普比率 (越大越好)</option>
                <option value="return_drawdown_ratio">收益/回撤比 (越大越好)</option>
              </optgroup>
              <optgroup label="收益">
                <option value="total_return">总收益率 (越大越好)</option>
                <option value="annual_return">年化收益率 (越大越好)</option>
                <option value="final_equity">最终权益 (越大越好)</option>
                <option value="net_pnl">净盈亏 (越大越好)</option>
              </optgroup>
              <optgroup label="风险">
                <option value="max_drawdown">最大回撤 (越接近 0 越好)</option>
              </optgroup>
              <optgroup label="交易">
                <option value="win_rate">胜率 (越大越好)</option>
                <option value="trade_count">成交笔数</option>
              </optgroup>
            </select>
          </label>
          {optAlgorithm === "genetic_algorithm" && (
            <>
              <label className="block text-xs text-slate-500">
                种群大小
                <input type="number" className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm" value={optGen.population_size}
                  onChange={(e) => setOptGen((g) => ({ ...g, population_size: Math.max(4, Number(e.target.value) || 20) }))} />
              </label>
              <label className="block text-xs text-slate-500">
                进化代数
                <input type="number" className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm" value={optGen.generations}
                  onChange={(e) => setOptGen((g) => ({ ...g, generations: Math.max(1, Number(e.target.value) || 10) }))} />
              </label>
            </>
          )}
        </div>

        {!selectedTemplate ? (
          <p className="mt-4 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">先在上方「策略类」下拉里选一个策略，才能配置其参数</p>
        ) : (
          <div className="mt-4 space-y-2">
            <div className="text-sm font-medium">{selectedTemplate.display_name || selectedTemplate.class_name} 参数（{selectedTemplate.parameters.length} 个）</div>
            {selectedTemplate.parameters.map((paramName) => {
              const cfg = paramConfigs[paramName] ?? { mode: "fixed", fixed: String(selectedTemplate.parameter_defaults?.[paramName] ?? "") };
              const update = (patch: Partial<typeof cfg>) => setParamConfigs((m) => ({ ...m, [paramName]: { ...cfg, ...patch } }));
              return (
                <div key={paramName} className="grid grid-cols-1 gap-2 rounded border border-line bg-white p-3 sm:grid-cols-[140px_120px_1fr]">
                  <div className="font-mono text-sm">
                    {paramName}
                    <span className="ml-2 text-xs text-slate-400">默认 {String(selectedTemplate.parameter_defaults?.[paramName] ?? "?")}</span>
                  </div>
                  <select className="focus-ring rounded border border-line px-2 py-1 text-sm" value={cfg.mode}
                    onChange={(e) => update({ mode: e.target.value as "fixed" | "values" | "range" })}>
                    <option value="fixed">固定值</option>
                    <option value="values">值列表</option>
                    <option value="range">范围</option>
                  </select>
                  {cfg.mode === "fixed" && (
                    <input className="focus-ring rounded border border-line px-3 py-1 text-sm font-mono" value={cfg.fixed ?? ""}
                      onChange={(e) => update({ fixed: e.target.value })} placeholder="数值" />
                  )}
                  {cfg.mode === "values" && (
                    <input className="focus-ring rounded border border-line px-3 py-1 text-sm font-mono" value={cfg.values ?? ""}
                      onChange={(e) => update({ values: e.target.value })} placeholder="逗号分隔，例 3, 5, 8, 13" />
                  )}
                  {cfg.mode === "range" && (
                    <div className="flex flex-wrap gap-2">
                      <input className="focus-ring w-20 rounded border border-line px-2 py-1 text-sm font-mono" value={cfg.low ?? ""}
                        onChange={(e) => update({ low: e.target.value })} placeholder="low" />
                      <input className="focus-ring w-20 rounded border border-line px-2 py-1 text-sm font-mono" value={cfg.high ?? ""}
                        onChange={(e) => update({ high: e.target.value })} placeholder="high" />
                      <input className="focus-ring w-20 rounded border border-line px-2 py-1 text-sm font-mono" value={cfg.step ?? ""}
                        onChange={(e) => update({ step: e.target.value })} placeholder="step" />
                      <select className="focus-ring rounded border border-line px-2 py-1 text-sm" value={cfg.type ?? "int"}
                        onChange={(e) => update({ type: e.target.value as "int" | "float" })}>
                        <option value="int">int</option>
                        <option value="float">float</option>
                      </select>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <div className="mt-3">
          <button className="focus-ring rounded border border-accent bg-accent px-4 py-2 text-sm text-white hover:opacity-90 disabled:opacity-50"
            onClick={handleOptimize} disabled={optimizing || !form.vt_symbol || !selectedTemplate}>
            {optimizing ? "优化中…" : `运行${optAlgorithm === "brute_force" ? "穷举" : "遗传算法"}优化`}
          </button>
        </div>

        {optResult && (
          <div className="mt-4">
            <div className="mb-2 text-sm font-medium">
              结果（{optResult.algorithm}，评估 {optResult.tested_combinations} 个组合，按 {optResult.target} 排序，显示 Top {optResult.results.length}）
            </div>
            {optResult.parameter_names && optResult.parameter_names.length === 2 && (
              <div className="mb-3">
                <div className="mb-1 text-xs font-medium text-slate-600">{optTarget} 热力图</div>
                <Heatmap
                  data={optResult.results.map((r) => ({
                    row: r.parameters[optResult.parameter_names![0]] as number,
                    col: r.parameters[optResult.parameter_names![1]] as number,
                    value: (r as Record<string, unknown>)[optTarget] as number | null ?? null,
                  }))}
                  rowLabel={optResult.parameter_names[0]}
                  colLabel={optResult.parameter_names[1]}
                  valueLabel={optTarget}
                  formatValue={(v) => optTarget === "max_drawdown" || optTarget === "total_return" || optTarget === "annual_return" || optTarget === "win_rate" ? `${(v * 100).toFixed(2)}%` : v.toFixed(3)}
                  height={280}
                />
              </div>
            )}
            {optResult.history && optResult.history.length > 0 && (
              <details className="mb-2">
                <summary className="cursor-pointer text-xs text-slate-600">GA 进化历史（每代最优 score）</summary>
                <div className="mt-1 grid grid-cols-2 gap-1 text-xs sm:grid-cols-5">
                  {optResult.history.map((h, i) => (
                    <div key={i} className="rounded bg-panel px-2 py-1">G{h.generation}: {h.best_score.toFixed(6)}</div>
                  ))}
                </div>
              </details>
            )}
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-slate-500">
                    <th className="pb-2 pr-4">#</th>
                    {optResult.parameter_names?.map((n) => <th key={n} className="pb-2 pr-4 font-mono">{n}</th>)}
                    <th className="pb-2 pr-4">夏普</th>
                    <th className="pb-2 pr-4">总收益率</th>
                    <th className="pb-2 pr-4">年化收益</th>
                    <th className="pb-2 pr-4">最大回撤</th>
                    <th className="pb-2 pr-4">收益/回撤</th>
                    <th className="pb-2 pr-4">胜率</th>
                    <th className="pb-2 pr-4">成交</th>
                    <th className="pb-2">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {optResult.results.map((r, i) => {
                    const promote = () => {
                      // Merge optimized values with current fixed paramOverrides (size/holding etc.)
                      const fixedSetting: Record<string, number | string | boolean> = {};
                      for (const [name, raw] of Object.entries(paramOverrides)) {
                        const trimmed = raw.trim();
                        if (!trimmed) continue;
                        const n = Number(trimmed);
                        if (!Number.isNaN(n) && /^-?\d+(\.\d+)?$/.test(trimmed)) fixedSetting[name] = n;
                        else fixedSetting[name] = trimmed;
                      }
                      const payload = {
                        class_name: form.strategy_class,
                        vt_symbol: form.vt_symbol,
                        setting: { ...fixedSetting, ...r.parameters } as Record<string, number | string | boolean>,
                        suggested_name: `${form.strategy_class.toLowerCase().replace(/strategy$/i, "")}_${form.vt_symbol.split(".")[0].toLowerCase()}_${i + 1}`,
                      };
                      try { window.sessionStorage.setItem(PROMOTE_STORAGE_KEY, JSON.stringify(payload)); } catch { /* ignore */ }
                      navigate("/strategies");
                    };
                    return (
                      <tr key={i} className={`border-b border-line/50 ${i === 0 ? "bg-emerald-50 font-medium" : ""}`}>
                        <td className="py-2 pr-4">{i + 1}</td>
                        {optResult.parameter_names?.map((n) => (
                          <td key={n} className="py-2 pr-4 font-mono">{String(r.parameters[n] ?? "-")}</td>
                        ))}
                        <td className="py-2 pr-4">{optionalDecimal(r.sharpe_ratio, 2)}</td>
                        <td className="py-2 pr-4">{optionalPct(r.total_return)}</td>
                        <td className="py-2 pr-4">{optionalPct(r.annual_return)}</td>
                        <td className="py-2 pr-4">{optionalPct(r.max_drawdown)}</td>
                        <td className="py-2 pr-4">{optionalDecimal(r.return_drawdown_ratio, 2)}</td>
                        <td className="py-2 pr-4">{optionalPct(r.win_rate)}</td>
                        <td className="py-2 pr-4">{r.trade_count ?? "-"}</td>
                        <td className="py-2">
                          <button
                            type="button"
                            className="focus-ring rounded border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[11px] text-emerald-800 hover:bg-emerald-100"
                            onClick={promote}
                            title="用此参数预填实盘策略注册表单"
                          >
                            实盘 →
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>

      {result && (
        <section className="panel p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">回测结果</div>
              <h2 className="mt-1 text-xl font-semibold">{result.vt_symbol} · {result.strategy_class}</h2>
              {result.message && <p className="mt-1 text-sm text-slate-500">{result.message}</p>}
            </div>
            <div className="flex flex-wrap gap-2">
              <button className="focus-ring rounded border border-line bg-white px-3 py-1.5 text-sm hover:bg-panel" onClick={handleSaveRun}>💾 保存到对比池</button>
              <button className="focus-ring rounded border border-line bg-white px-3 py-1.5 text-sm hover:bg-panel" onClick={handleExportTradesCsv}>导出交易 CSV</button>
              <button className="focus-ring rounded border border-line bg-white px-3 py-1.5 text-sm hover:bg-panel" onClick={handleExportEquityCsv}>导出权益 CSV</button>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {([
              ["总收益率", optionalPct(result.total_return)],
              ["最大回撤", `${optionalPct(result.max_drawdown)} (${optionalMoney(result.max_drawdown_value)})`],
              ["夏普比率", optionalDecimal(result.vnpy_statistics?.sharpe_ratio, 2)],
              ["净盈亏", optionalMoney(result.net_pnl)],
              ["手续费", optionalMoney(result.total_commission)],
              ["成交笔数", String(result.trade_count ?? result.trades?.length ?? "-")],
            ] as [string, string][]).map(([label, value]) => (
              <div key={label} className="rounded border border-line bg-panel p-3">
                <div className="text-xs text-slate-500">{label}</div>
                <div className="mt-1 text-lg font-semibold">{value}</div>
              </div>
            ))}
          </div>

          {/* Buy-and-hold 对比 — 帮助判断策略是否打赢"啥都不做" */}
          {result.buy_and_hold && (
            <div className="mt-4 rounded border border-line bg-panel/60 p-3">
              <div className="mb-2 flex items-baseline gap-3">
                <span className="text-sm font-medium">📊 Buy-and-Hold 同期基准</span>
                <span className="text-xs text-slate-500">期初满仓买入 → 期末平仓, 同样 fee + slip</span>
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
                {([
                  ["持有总收益", optionalPct(result.buy_and_hold.total_return)],
                  ["持有最大回撤", optionalPct(result.buy_and_hold.max_drawdown)],
                  ["持有期末权益", optionalMoney(result.buy_and_hold.final_equity)],
                  ["入场价 → 出场价", `$${result.buy_and_hold.entry_price.toFixed(2)} → $${result.buy_and_hold.exit_price.toFixed(2)}`],
                  ["持有股数", String(result.buy_and_hold.shares.toFixed(0))],
                  [
                    "策略 vs 持有 α",
                    (() => {
                      const alpha = result.alpha_vs_buy_and_hold ?? 0;
                      const sign = alpha >= 0 ? "+" : "";
                      return `${sign}${(alpha * 100).toFixed(2)} pp`;
                    })(),
                  ],
                ] as [string, string][]).map(([label, value]) => {
                  const isAlpha = label.includes("α");
                  const alpha = result.alpha_vs_buy_and_hold ?? 0;
                  const tint = isAlpha
                    ? alpha >= 0
                      ? "border-emerald-300 bg-emerald-50 text-emerald-900"
                      : "border-rose-300 bg-rose-50 text-rose-900"
                    : "border-line bg-white";
                  return (
                    <div key={label} className={`rounded border p-3 ${tint}`}>
                      <div className="text-xs opacity-70">{label}</div>
                      <div className="mt-1 text-lg font-semibold">{value}</div>
                    </div>
                  );
                })}
              </div>
              <p className="mt-2 text-[11px] text-slate-500">
                α &gt; 0 = 策略打赢 buy-and-hold; α &lt; 0 = 策略不如直接持有该标的。趋势股上做趋势跟踪经常 α &lt; 0 (错过初始爆发 + 回撤洗出)。
              </p>
            </div>
          )}

          {priceBars.length > 0 && (
            <div className="mt-5">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium">蜡烛图 + 交易标记（{priceBars.length} 根 K 线，{tradeMarkers.length} 个标记）</span>
                <div className="flex flex-wrap items-center gap-3 text-xs text-slate-600">
                  指标:
                  <label className="flex items-center gap-1"><input type="checkbox" checked={chartIndicators.ma5} onChange={(e) => setChartIndicators((s) => ({ ...s, ma5: e.target.checked }))} /><span style={{ color: "#2563eb" }}>MA5</span></label>
                  <label className="flex items-center gap-1"><input type="checkbox" checked={chartIndicators.ma20} onChange={(e) => setChartIndicators((s) => ({ ...s, ma20: e.target.checked }))} /><span style={{ color: "#f59e0b" }}>MA20</span></label>
                  <label className="flex items-center gap-1"><input type="checkbox" checked={chartIndicators.ma60} onChange={(e) => setChartIndicators((s) => ({ ...s, ma60: e.target.checked }))} /><span style={{ color: "#a855f7" }}>MA60</span></label>
                  <label className="flex items-center gap-1"><input type="checkbox" checked={chartIndicators.boll} onChange={(e) => setChartIndicators((s) => ({ ...s, boll: e.target.checked }))} />BOLL(20,2)</label>
                </div>
              </div>
              <CandlestickChart
                bars={priceBars}
                trades={tradeMarkers}
                height={420}
                indicators={{
                  ma: [chartIndicators.ma5 && 5, chartIndicators.ma20 && 20, chartIndicators.ma60 && 60].filter((x): x is number => typeof x === "number"),
                  boll: chartIndicators.boll ? { window: 20, mult: 2 } : undefined,
                }}
              />
              <div className="mt-1 flex gap-4 text-xs text-slate-500">
                <span>▲ <span className="text-emerald-600">绿色箭头</span>=买入 (B@价格)</span>
                <span>▼ <span className="text-red-600">红色箭头</span>=卖出 (S@价格)</span>
              </div>
            </div>
          )}

          {equityCurve.length > 0 && (
            <div className="mt-5">
              <div className="mb-2 text-sm font-medium">权益曲线</div>
              <div className="h-64 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={equityCurve} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="date" fontSize={11} tickFormatter={(d) => String(d).slice(5)} />
                    <YAxis fontSize={11} tickFormatter={(v) => `$${Number(v).toFixed(0)}`} />
                    <Tooltip formatter={(value) => Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })} />
                    <Line type="monotone" dataKey="balance" stroke="#2563eb" dot={false} name="账户余额" />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {result.trades && result.trades.length > 0 && (
            <div className="mt-5">
              <div className="mb-2 text-sm font-medium">成交明细 (前 50 笔，共 {result.trades.length} 笔)</div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-line text-left text-slate-500">
                      <th className="pb-2 pr-3">时间</th>
                      <th className="pb-2 pr-3">方向</th>
                      <th className="pb-2 pr-3">价格</th>
                      <th className="pb-2">数量</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.slice(0, 50).map((t, i) => (
                      <tr key={i} className="border-b border-line/50">
                        <td className="py-1.5 pr-3 text-slate-500">{t["datetime"] ? new Date(t["datetime"] as string).toLocaleString() : "-"}</td>
                        <td className="py-1.5 pr-3">{String(t["direction"] ?? "-")}</td>
                        <td className="py-1.5 pr-3">{optionalDecimal(t["price"], 2)}</td>
                        <td className="py-1.5">{String(t["volume"] ?? "-")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>
      )}

      {savedRuns.length >= 2 && (
        <section className="panel p-4">
          <div className="flex items-baseline justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">回测 A/B 对比</div>
              <h2 className="mt-1 text-xl font-semibold">已保存 {savedRuns.length} 次（最多 10）</h2>
            </div>
            <button className="focus-ring rounded border border-line px-3 py-1.5 text-sm hover:bg-panel" onClick={() => setSavedRuns([])}>清空</button>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3">
            {[0, 1].map((slot) => (
              <label key={slot} className="block text-xs text-slate-500">
                {slot === 0 ? "A" : "B"} 方案
                <select className="focus-ring mt-1 w-full rounded border border-line px-2 py-1 text-sm" value={compareSelected[slot]}
                  onChange={(e) => setCompareSelected((c) => slot === 0 ? [Number(e.target.value), c[1]] : [c[0], Number(e.target.value)])}>
                  {savedRuns.map((r, i) => <option key={i} value={i}>{r.label}</option>)}
                </select>
              </label>
            ))}
          </div>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-slate-500">
                  <th className="pb-2 pr-4">指标</th>
                  <th className="pb-2 pr-4">A: {savedRuns[compareSelected[0]]?.label}</th>
                  <th className="pb-2 pr-4">B: {savedRuns[compareSelected[1]]?.label}</th>
                  <th className="pb-2">B − A</th>
                </tr>
              </thead>
              <tbody>
                {([
                  ["总收益率", "total_return", "pct"],
                  ["年化收益率", "annual_return", "pct"],
                  ["最大回撤", "max_drawdown", "pct"],
                  ["夏普比率", "sharpe_ratio", "num"],
                  ["收益/回撤", "return_drawdown_ratio", "num"],
                  ["净盈亏", "net_pnl", "money"],
                  ["手续费", "total_commission", "money"],
                  ["成交笔数", "trade_count", "int"],
                  ["胜率", "win_rate", "pct"],
                ] as Array<[string, string, string]>).map(([label, key, fmt]) => {
                  const a = savedRuns[compareSelected[0]]?.result as unknown as Record<string, unknown> | undefined;
                  const b = savedRuns[compareSelected[1]]?.result as unknown as Record<string, unknown> | undefined;
                  const av = typeof a?.[key] === "number" ? a[key] as number : null;
                  const bv = typeof b?.[key] === "number" ? b[key] as number : null;
                  const fmtVal = (v: number | null) => v === null ? "—" : fmt === "pct" ? optionalPct(v) : fmt === "money" ? optionalMoney(v) : fmt === "int" ? String(Math.round(v)) : optionalDecimal(v, 3);
                  const diff = av !== null && bv !== null ? bv - av : null;
                  const diffPositive = diff !== null && (key === "max_drawdown" ? diff > 0 : diff > 0); // higher drawdown ratio = closer to 0 = better
                  return (
                    <tr key={key} className="border-b border-line/40">
                      <td className="py-1.5 pr-4 text-slate-600">{label}</td>
                      <td className="py-1.5 pr-4 font-mono">{fmtVal(av)}</td>
                      <td className="py-1.5 pr-4 font-mono">{fmtVal(bv)}</td>
                      <td className={`py-1.5 font-mono ${diff === null ? "" : diffPositive ? "text-emerald-700" : "text-red-700"}`}>{fmtVal(diff)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="panel p-4">
        <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">稳健性分析</div>
        <h2 className="mt-1 text-xl font-semibold">Walk-forward 验证 & Monte Carlo 模拟</h2>
        <p className="mt-1 text-sm text-slate-500">
          <span className="font-medium">Walk-forward</span>: 把时间区间切成 N 折，每折前 IS_fraction 为样本内、后段为样本外，看策略在 OOS 上是否还赚钱（防过拟合）。
          <span className="ml-2 font-medium">Monte Carlo</span>: 取基准回测的逐笔 PnL 序列，随机重排或重抽样 N 次，统计最终权益和回撤分布（评估运气成分）。
        </p>

        <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="rounded border border-line bg-panel p-3">
            <div className="mb-2 text-sm font-medium">Walk-forward 设置</div>
            <div className="grid grid-cols-2 gap-2 text-xs text-slate-500">
              <label>
                折数 (n_folds)
                <input type="number" className="focus-ring mt-1 w-full rounded border border-line bg-white px-2 py-1 text-sm" value={wfForm.n_folds}
                  onChange={(e) => setWfForm((s) => ({ ...s, n_folds: Math.max(2, Number(e.target.value) || 4) }))} />
              </label>
              <label>
                IS 比例 (0-1)
                <input type="number" step="0.05" className="focus-ring mt-1 w-full rounded border border-line bg-white px-2 py-1 text-sm" value={wfForm.is_fraction}
                  onChange={(e) => setWfForm((s) => ({ ...s, is_fraction: Number(e.target.value) || 0.6 }))} />
              </label>
            </div>
            <button className="focus-ring mt-3 rounded border border-accent bg-accent px-3 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-50"
              disabled={wfBusy || !form.vt_symbol} onClick={handleWalkForward}>{wfBusy ? "运行中…" : "运行 Walk-forward"}</button>
            {wfResult && (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-line text-left text-slate-500">
                      <th className="pb-1 pr-2">折</th><th className="pb-1 pr-2">IS 收益</th><th className="pb-1 pr-2">IS 夏普</th><th className="pb-1 pr-2">IS 回撤</th>
                      <th className="pb-1 pr-2">OOS 收益</th><th className="pb-1 pr-2">OOS 夏普</th><th className="pb-1">OOS 回撤</th>
                    </tr>
                  </thead>
                  <tbody>
                    {wfResult.results.map((r) => (
                      <tr key={r.fold} className="border-b border-line/40">
                        <td className="py-1 pr-2 font-mono">{r.fold}</td>
                        <td className="py-1 pr-2">{optionalPct(r.is_total_return)}</td>
                        <td className="py-1 pr-2">{optionalDecimal(r.is_sharpe, 2)}</td>
                        <td className="py-1 pr-2">{optionalPct(r.is_max_drawdown)}</td>
                        <td className="py-1 pr-2 font-semibold">{optionalPct(r.oos_total_return)}</td>
                        <td className="py-1 pr-2 font-semibold">{optionalDecimal(r.oos_sharpe, 2)}</td>
                        <td className="py-1 font-semibold">{optionalPct(r.oos_max_drawdown)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="rounded border border-line bg-panel p-3">
            <div className="mb-2 text-sm font-medium">Monte Carlo 设置</div>
            <div className="grid grid-cols-2 gap-2 text-xs text-slate-500">
              <label>
                模拟次数
                <input type="number" className="focus-ring mt-1 w-full rounded border border-line bg-white px-2 py-1 text-sm" value={mcForm.n_simulations}
                  onChange={(e) => setMcForm((s) => ({ ...s, n_simulations: Math.max(10, Number(e.target.value) || 200) }))} />
              </label>
              <label>
                方法
                <select className="focus-ring mt-1 w-full rounded border border-line bg-white px-2 py-1 text-sm" value={mcForm.method}
                  onChange={(e) => setMcForm((s) => ({ ...s, method: e.target.value as "trade_shuffle" | "bootstrap" }))}>
                  <option value="trade_shuffle">交易顺序重排</option>
                  <option value="bootstrap">Bootstrap 重抽样</option>
                </select>
              </label>
            </div>
            <button className="focus-ring mt-3 rounded border border-accent bg-accent px-3 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-50"
              disabled={mcBusy || !form.vt_symbol} onClick={handleMonteCarlo}>{mcBusy ? "运行中…" : "运行 Monte Carlo"}</button>
            {mcResult && (
              <div className="mt-3 text-xs">
                <div className="mb-1 text-slate-500">基于 {mcResult.n_trades} 笔交易，运行 {mcResult.n_simulations} 次</div>
                <div className="rounded border border-line bg-white p-2">
                  <div className="mb-1 font-medium">最终权益分布 ($)</div>
                  <div className="grid grid-cols-3 gap-1 text-slate-600">
                    <div>P05: ${mcResult.final_equity_stats.p05.toFixed(0)}</div>
                    <div>P50: ${mcResult.final_equity_stats.median.toFixed(0)}</div>
                    <div>P95: ${mcResult.final_equity_stats.p95.toFixed(0)}</div>
                    <div>min: ${mcResult.final_equity_stats.min.toFixed(0)}</div>
                    <div>mean: ${mcResult.final_equity_stats.mean.toFixed(0)}</div>
                    <div>max: ${mcResult.final_equity_stats.max.toFixed(0)}</div>
                  </div>
                </div>
                <div className="mt-2 rounded border border-line bg-white p-2">
                  <div className="mb-1 font-medium">最大回撤分布 (%)</div>
                  <div className="grid grid-cols-3 gap-1 text-slate-600">
                    <div>P05: {(mcResult.max_drawdown_stats.p05 * 100).toFixed(2)}%</div>
                    <div>P50: {(mcResult.max_drawdown_stats.median * 100).toFixed(2)}%</div>
                    <div>P95: {(mcResult.max_drawdown_stats.p95 * 100).toFixed(2)}%</div>
                    <div>worst: {(mcResult.max_drawdown_stats.worst * 100).toFixed(2)}%</div>
                    <div>mean: {(mcResult.max_drawdown_stats.mean * 100).toFixed(2)}%</div>
                    <div>best: {(mcResult.max_drawdown_stats.best * 100).toFixed(2)}%</div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="panel p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">数据管理</div>
            <h2 className="mt-1 text-lg font-semibold">本地历史数据 ({overview.length} 个品种)</h2>
          </div>
          <button className="focus-ring rounded border border-line px-3 py-1.5 text-sm hover:bg-panel" onClick={refreshOverview}>刷新</button>
        </div>
        {overview.length === 0 ? (
          <p className="py-4 text-center text-sm text-slate-500">暂无本地历史数据。上方下载或通过 行情录制 页面订阅采集。</p>
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
                    <td className="py-2"><button className="text-xs text-red-600 hover:underline" onClick={() => handleDelete(row.vt_symbol)}>删除</button></td>
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

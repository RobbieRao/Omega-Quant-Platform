import { useCallback, useEffect, useMemo, useState } from "react";
import { Modal } from "../components/Modal";
import { api } from "../api";
import type { CtaLiveStrategyState, CtaStrategyMetadata } from "../types";
import { optionalDecimal, textValue } from "../utils/format";

const PROMOTE_STORAGE_KEY = "omega:cta-promote-from-backtest";

type PromotePayload = {
  class_name?: string;
  vt_symbol?: string;
  setting?: Record<string, number | string | boolean>;
  suggested_name?: string;
};

export default function CtaStrategiesPage() {
  const [templates, setTemplates] = useState<CtaStrategyMetadata[]>([]);
  const [strategies, setStrategies] = useState<CtaLiveStrategyState[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [form, setForm] = useState({
    strategy_name: "",
    class_name: "",
    vt_symbol: "",
  });
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [jsonMode, setJsonMode] = useState(false);
  const [jsonText, setJsonText] = useState("{}");

  const selectedTemplate = useMemo(
    () => templates.find((t) => t.class_name === form.class_name),
    [templates, form.class_name],
  );

  const resetParamValuesFromTemplate = useCallback((tpl: CtaStrategyMetadata | undefined) => {
    if (!tpl) { setParamValues({}); return; }
    const next: Record<string, string> = {};
    for (const name of tpl.parameters || []) {
      const def = tpl.parameter_defaults?.[name];
      next[name] = def === undefined || def === null ? "" : String(def);
    }
    setParamValues(next);
  }, []);

  const consumePromotePayload = useCallback((tplList: CtaStrategyMetadata[]) => {
    if (typeof window === "undefined") return false;
    let raw: string | null = null;
    try { raw = window.sessionStorage.getItem(PROMOTE_STORAGE_KEY); } catch { return false; }
    if (!raw) return false;
    try {
      const payload = JSON.parse(raw) as PromotePayload;
      window.sessionStorage.removeItem(PROMOTE_STORAGE_KEY);
      const tpl = tplList.find((t) => t.class_name === payload.class_name);
      if (!tpl) return false;
      setForm({
        strategy_name: payload.suggested_name || `${tpl.class_name.toLowerCase().replace(/strategy$/i, "")}_${(payload.vt_symbol || "").split(".")[0].toLowerCase()}`,
        class_name: tpl.class_name,
        vt_symbol: payload.vt_symbol || "",
      });
      const next: Record<string, string> = {};
      for (const name of tpl.parameters || []) {
        if (payload.setting && name in payload.setting) {
          next[name] = String(payload.setting[name]);
        } else {
          const def = tpl.parameter_defaults?.[name];
          next[name] = def === undefined || def === null ? "" : String(def);
        }
      }
      setParamValues(next);
      return true;
    } catch {
      return false;
    }
  }, []);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const [t, s] = await Promise.allSettled([api.ctaLiveTemplates(), api.ctaLiveStrategies()]);
      if (t.status === "fulfilled") {
        setTemplates(t.value);
        const promoted = consumePromotePayload(t.value);
        if (!promoted) {
          setForm((f) => (f.class_name || t.value.length === 0 ? f : { ...f, class_name: t.value[0].class_name }));
        }
      }
      if (s.status === "fulfilled") setStrategies(s.value);
    } finally {
      setBusy(false);
    }
  }, [consumePromotePayload]);

  useEffect(() => {
    refresh();
    const id = setInterval(() => {
      if (document.visibilityState === "visible") {
        api.ctaLiveStrategies().then(setStrategies).catch(() => null);
      }
    }, 5_000);
    return () => clearInterval(id);
  }, [refresh]);

  // Reset paramValues when class_name changes (unless we just consumed a promote payload)
  useEffect(() => {
    if (!selectedTemplate) return;
    setParamValues((cur) => {
      // Already populated for this class? keep
      const expectedKeys = (selectedTemplate.parameters || []).join("|");
      const actualKeys = Object.keys(cur).join("|");
      if (expectedKeys && expectedKeys === actualKeys) return cur;
      const next: Record<string, string> = {};
      for (const name of selectedTemplate.parameters || []) {
        const def = selectedTemplate.parameter_defaults?.[name];
        next[name] = def === undefined || def === null ? "" : String(def);
      }
      return next;
    });
  }, [selectedTemplate]);

  const buildSettingFromInputs = useCallback((): Record<string, number | string | boolean> | null => {
    if (jsonMode) {
      try { return JSON.parse(jsonText) as Record<string, number | string | boolean>; }
      catch { setError("setting 不是合法 JSON"); return null; }
    }
    const out: Record<string, number | string | boolean> = {};
    for (const [k, v] of Object.entries(paramValues)) {
      const trimmed = v.trim();
      if (trimmed === "") continue;
      // boolean
      if (trimmed === "true" || trimmed === "false") { out[k] = trimmed === "true"; continue; }
      // numeric
      const n = Number(trimmed);
      if (!Number.isNaN(n) && /^-?\d+(\.\d+)?$/.test(trimmed)) { out[k] = n; continue; }
      out[k] = trimmed;
    }
    return out;
  }, [jsonMode, jsonText, paramValues]);

  const handleAdd = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      const setting = buildSettingFromInputs();
      if (setting === null) { setBusy(false); return; }
      await api.addCtaLiveStrategy({
        strategy_name: form.strategy_name,
        class_name: form.class_name,
        vt_symbol: form.vt_symbol,
        setting,
      });
      setForm((f) => ({ ...f, strategy_name: "", vt_symbol: "" }));
      resetParamValuesFromTemplate(selectedTemplate);
      setJsonText("{}");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [form, selectedTemplate, buildSettingFromInputs, resetParamValuesFromTemplate, refresh]);

  const handleAction = useCallback(async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try { await fn(); await refresh(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }, [refresh]);

  const [editTarget, setEditTarget] = useState<CtaLiveStrategyState | null>(null);
  const [editDraft, setEditDraft] = useState<Record<string, string>>({});
  const [editJsonError, setEditJsonError] = useState<string | null>(null);

  const openEditModal = useCallback((s: CtaLiveStrategyState) => {
    setEditTarget(s);
    const draft: Record<string, string> = {};
    for (const [k, v] of Object.entries(s.parameters || {})) {
      draft[k] = String(v ?? "");
    }
    setEditDraft(draft);
    setEditJsonError(null);
  }, []);

  const submitEdit = useCallback(async () => {
    if (!editTarget) return;
    setEditJsonError(null);
    const setting: Record<string, unknown> = {};
    for (const [k, raw] of Object.entries(editDraft)) {
      if (raw === "" || raw === undefined) continue;
      const n = Number(raw);
      setting[k] = Number.isFinite(n) ? n : raw;
    }
    if (Object.keys(setting).length === 0) { setEditJsonError("至少修改一个参数"); return; }
    setBusy(true); setError(null);
    try {
      await api.editCtaLiveStrategy(editTarget.strategy_name, setting);
      setEditTarget(null);
      await refresh();
    } catch (e) {
      setEditJsonError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [editTarget, editDraft, refresh]);

  return (
    <div className="space-y-5">
      <section className="panel p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">CTA 策略管理</div>
            <h2 className="mt-1 text-xl font-semibold">注册新策略实例</h2>
          </div>
          <label className="flex shrink-0 items-center gap-2 text-xs text-slate-500">
            <input type="checkbox" className="focus-ring accent-accent" checked={jsonMode}
              onChange={(e) => setJsonMode(e.target.checked)} />
            JSON 模式
          </label>
        </div>
        {error && <p className="mt-2 rounded border border-danger/30 bg-red-50 px-3 py-2 text-sm text-danger">{error}</p>}
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <label className="block text-xs text-slate-500">
            策略类
            <select
              className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink"
              value={form.class_name}
              onChange={(e) => setForm((f) => ({ ...f, class_name: e.target.value }))}
            >
              {templates.map((t) => (
                <option key={t.class_name} value={t.class_name}>{t.class_name}</option>
              ))}
            </select>
          </label>
          <label className="block text-xs text-slate-500">
            实例名称
            <input
              className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink"
              value={form.strategy_name}
              onChange={(e) => setForm((f) => ({ ...f, strategy_name: e.target.value }))}
              placeholder="如 mu_trend_1"
            />
          </label>
          <label className="block text-xs text-slate-500">
            vt_symbol
            <input
              className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink"
              value={form.vt_symbol}
              onChange={(e) => setForm((f) => ({ ...f, vt_symbol: e.target.value.trim().toUpperCase() }))}
              placeholder="如 MU.SMART"
            />
          </label>
        </div>

        {selectedTemplate && (
          <div className="mt-3 rounded border border-line bg-panel px-3 py-2 text-xs text-slate-600">
            <span className="font-medium">{selectedTemplate.display_name || selectedTemplate.class_name}</span>
            {selectedTemplate.author && <> · {selectedTemplate.author}</>}
            {selectedTemplate.category && <span className="ml-2 rounded bg-white px-1.5 py-0.5">{selectedTemplate.category}</span>}
            {selectedTemplate.tags && selectedTemplate.tags.length > 0 && (
              <span className="ml-1">{selectedTemplate.tags.map((t) => (
                <span key={t} className="ml-1 rounded bg-sky-100 px-1.5 py-0.5 text-sky-800">{t}</span>
              ))}</span>
            )}
            {selectedTemplate.description && <div className="mt-1 text-slate-500">{selectedTemplate.description}</div>}
          </div>
        )}

        {jsonMode ? (
          <div className="mt-3">
            <label className="block text-xs text-slate-500">
              setting (JSON)
              <textarea
                className="focus-ring mt-1 h-32 w-full rounded border border-line px-3 py-2 text-sm font-mono"
                value={jsonText}
                onChange={(e) => setJsonText(e.target.value)}
                placeholder='{"trend_ma_window":80,"pullback_ma_window":3}'
              />
            </label>
          </div>
        ) : (
          <div className="mt-3">
            <div className="mb-2 flex items-center justify-between">
              <div className="text-xs font-medium text-slate-600">策略参数</div>
              {selectedTemplate && (
                <button type="button" className="focus-ring rounded border border-line bg-white px-2 py-0.5 text-[11px] text-slate-600 hover:bg-panel"
                  onClick={() => resetParamValuesFromTemplate(selectedTemplate)}>恢复默认</button>
              )}
            </div>
            {selectedTemplate && (selectedTemplate.parameters?.length ?? 0) > 0 ? (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
                {selectedTemplate.parameters.map((name) => {
                  const def = selectedTemplate.parameter_defaults?.[name];
                  return (
                    <label key={name} className="block text-[11px] text-slate-500">
                      <span className="font-mono text-slate-700">{name}</span>
                      {def !== undefined && def !== null && (
                        <span className="ml-1 text-slate-400">默认 {String(def)}</span>
                      )}
                      <input
                        className="focus-ring mt-0.5 w-full rounded border border-line px-2 py-1 text-sm font-mono"
                        value={paramValues[name] ?? ""}
                        onChange={(e) => setParamValues((cur) => ({ ...cur, [name]: e.target.value }))}
                      />
                    </label>
                  );
                })}
              </div>
            ) : (
              <p className="text-xs text-slate-500">该策略未声明可调参数。</p>
            )}
          </div>
        )}

        <div className="mt-4">
          <button
            className="focus-ring rounded border border-accent bg-accent px-4 py-2 text-sm text-white hover:opacity-90 disabled:opacity-50"
            onClick={handleAdd}
            disabled={busy || !form.strategy_name || !form.class_name || !form.vt_symbol}
          >
            注册策略
          </button>
        </div>
      </section>

      <section className="panel p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">运行实例</div>
            <h2 className="mt-1 text-lg font-semibold">策略列表 ({strategies.length})</h2>
          </div>
          <button className="focus-ring rounded border border-line px-3 py-1.5 text-sm hover:bg-panel disabled:opacity-50" onClick={refresh} disabled={busy}>刷新</button>
        </div>
        {strategies.length === 0 ? (
          <p className="py-4 text-center text-sm text-slate-500">暂无已注册的策略实例</p>
        ) : (
          <div className="space-y-3">
            {strategies.map((s) => (
              <div key={s.strategy_name} className="rounded border border-line bg-white p-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-semibold">{s.strategy_name}</div>
                    <div className="mt-1 text-xs text-slate-500">{s.class_name} · {s.vt_symbol}</div>
                    <div className="mt-1 flex flex-wrap gap-2 text-xs">
                      <span className={`rounded px-2 py-0.5 ${s.trading ? "bg-emerald-100 text-emerald-800" : s.inited ? "bg-blue-100 text-blue-800" : "bg-slate-100 text-slate-600"}`}>
                        {s.trading ? "交易中" : s.inited ? "已初始化" : "未初始化"}
                      </span>
                      {s.pos !== undefined && <span className="rounded bg-panel px-2 py-0.5 text-slate-600">持仓 {s.pos}</span>}
                      {s.last_bar && <span className="rounded bg-panel px-2 py-0.5 text-slate-600">最新价 {optionalDecimal(s.last_bar.close_price, 2)}</span>}
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-2">
                    <button className="focus-ring rounded border border-line bg-white px-3 py-1 text-xs hover:bg-panel disabled:opacity-50"
                      disabled={busy} onClick={() => openEditModal(s)}>编辑参数{s.trading ? "并重启" : ""}</button>
                    {!s.trading && s.inited && (
                      <button className="focus-ring rounded border border-emerald-300 bg-emerald-50 px-3 py-1 text-xs text-emerald-800 hover:bg-emerald-100 disabled:opacity-50" disabled={busy} onClick={() => handleAction(() => api.startCtaLiveStrategy(s.strategy_name))}>启动</button>
                    )}
                    {s.trading && (
                      <button className="focus-ring rounded border border-amber-300 bg-amber-50 px-3 py-1 text-xs text-amber-800 hover:bg-amber-100 disabled:opacity-50" disabled={busy} onClick={() => handleAction(() => api.stopCtaLiveStrategy(s.strategy_name))}>停止</button>
                    )}
                    {!s.trading && (
                      <button className="focus-ring rounded border border-red-200 bg-red-50 px-3 py-1 text-xs text-red-700 hover:bg-red-100 disabled:opacity-50" disabled={busy} onClick={() => handleAction(() => api.removeCtaLiveStrategy(s.strategy_name))}>删除</button>
                    )}
                  </div>
                </div>
                {s.variables && Object.keys(s.variables).length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-500">
                    {Object.entries(s.variables).map(([k, v]) => (
                      <span key={k} className="rounded bg-panel px-2 py-0.5">{k}: {textValue(v)}</span>
                    ))}
                  </div>
                )}
                {s.logs && s.logs.length > 0 && (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-xs font-medium text-slate-600">策略日志 ({s.logs.length} 行)</summary>
                    <pre className="mt-1 max-h-48 overflow-auto rounded bg-slate-900 p-2 font-mono text-[11px] leading-4 text-emerald-100">
                      {s.logs.slice(-100).join("\n")}
                    </pre>
                  </details>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel p-4">
        <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">可用模板</div>
        <h2 className="mt-1 text-lg font-semibold">策略类 ({templates.length})</h2>
        <div className="mt-3 space-y-2">
          {templates.map((t) => (
            <div key={t.class_name} className="rounded border border-line bg-white p-3 text-sm">
              <div className="font-medium">{t.display_name || t.class_name}</div>
              {t.description && <div className="mt-1 text-xs text-slate-500">{t.description}</div>}
              <div className="mt-1 flex flex-wrap gap-3 text-xs text-slate-500">
                {t.author && <span>作者: {t.author}</span>}
                {t.parameters && t.parameters.length > 0 && <span className="font-mono">参数: {t.parameters.join(", ")}</span>}
                {t.variables && t.variables.length > 0 && <span className="font-mono">变量: {t.variables.join(", ")}</span>}
              </div>
            </div>
          ))}
        </div>
      </section>

      <Modal
        open={!!editTarget}
        onClose={() => setEditTarget(null)}
        title={editTarget ? `编辑策略参数: ${editTarget.strategy_name}` : ""}
        size="lg"
        footer={
          <>
            <button className="rounded border border-line bg-white px-4 py-1.5 text-sm hover:bg-panel" onClick={() => setEditTarget(null)}>取消</button>
            <button className="rounded border border-accent bg-accent px-4 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-50" disabled={busy} onClick={submitEdit}>保存{editTarget?.trading ? "并重启" : ""}</button>
          </>
        }
      >
        {editTarget && (
          <>
            <p className="mb-3 text-xs text-slate-500">
              策略类 <code className="font-mono">{editTarget.class_name}</code>，标的 <code className="font-mono">{editTarget.vt_symbol}</code>
              {editTarget.trading && <span className="ml-2 rounded bg-amber-100 px-2 py-0.5 text-amber-800">运行中 — 保存后会自动 stop → 改参数 → start</span>}
            </p>
            {editJsonError && <p className="mb-3 rounded border border-danger/30 bg-red-50 px-3 py-2 text-sm text-danger">{editJsonError}</p>}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {Object.keys(editDraft).length === 0 ? (
                <p className="text-sm text-slate-500">该策略未声明可调参数。</p>
              ) : (
                Object.entries(editDraft).map(([key, value]) => (
                  <label key={key} className="block text-xs text-slate-500">
                    <span className="font-mono">{key}</span>
                    <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm font-mono" value={value}
                      onChange={(e) => setEditDraft((d) => ({ ...d, [key]: e.target.value }))} />
                  </label>
                ))
              )}
            </div>
          </>
        )}
      </Modal>
    </div>
  );
}

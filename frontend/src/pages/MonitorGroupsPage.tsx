import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { MonitorGroup, MonitorGroupPayload } from "../types";
import { money } from "../utils/format";

const STATUS_COLORS: Record<string, string> = {
  running: "border-emerald-200 bg-emerald-50 text-emerald-800",
  paused: "border-amber-200 bg-amber-50 text-amber-800",
  stopped: "border-slate-200 bg-slate-100 text-slate-600",
  error: "border-red-200 bg-red-50 text-red-800",
  archived: "border-slate-200 bg-slate-50 text-slate-400",
};

export default function MonitorGroupsPage() {
  const [groups, setGroups] = useState<MonitorGroup[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<MonitorGroupPayload>({ name: "", description: "", allocated_capital: 10_000 });

  const refresh = useCallback(async () => {
    setBusy(true);
    try { const data = await api.monitorGroups(); setGroups(data); } finally { setBusy(false); }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const handleCreate = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      await api.createMonitorGroup(form);
      setForm({ name: "", description: "", allocated_capital: 10_000 });
      setShowForm(false);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [form, refresh]);

  const handleDelete = useCallback(async (id: string) => {
    if (!confirm("确认删除监控组？")) return;
    setBusy(true);
    try { await api.deleteMonitorGroup(id); await refresh(); } finally { setBusy(false); }
  }, [refresh]);

  const handleStatus = useCallback(async (id: string, action: "pause" | "resume" | "stop") => {
    setBusy(true);
    try {
      if (action === "pause") await api.pauseGroup(id);
      else if (action === "resume") await api.resumeGroup(id);
      else await api.stopGroup(id);
      await refresh();
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  return (
    <div className="space-y-5">
      <section className="panel p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">监控组</div>
            <h2 className="mt-1 text-xl font-semibold">观测模式（{groups.length} 个）</h2>
            <p className="mt-1 text-sm text-slate-500">监控组仅做资金分组和策略关联观测；实际交易下单由 CTA 策略引擎处理。</p>
          </div>
          <div className="flex gap-2">
            <button className="focus-ring rounded border border-line px-3 py-1.5 text-sm hover:bg-panel disabled:opacity-50" onClick={refresh} disabled={busy}>刷新</button>
            <button className="focus-ring rounded border border-accent bg-accent px-3 py-1.5 text-sm text-white hover:opacity-90" onClick={() => setShowForm((v) => !v)}>
              {showForm ? "取消" : "+ 新建"}
            </button>
          </div>
        </div>

        {showForm && (
          <div className="mt-4 rounded border border-line bg-panel p-4">
            <div className="text-sm font-medium mb-3">新建监控组</div>
            {error && <p className="mb-3 rounded border border-danger/30 bg-red-50 px-3 py-2 text-sm text-danger">{error}</p>}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <label className="block text-xs text-slate-500">
                名称
                <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="Alpha 策略组" />
              </label>
              <label className="block text-xs text-slate-500">
                描述
                <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.description}
                  onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} placeholder="可选" />
              </label>
              <label className="block text-xs text-slate-500">
                分配资金 (USD)
                <input type="number" className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm text-ink" value={form.allocated_capital}
                  onChange={(e) => setForm((f) => ({ ...f, allocated_capital: Number(e.target.value) }))} />
              </label>
            </div>
            <div className="mt-3">
              <button className="focus-ring rounded border border-accent bg-accent px-4 py-2 text-sm text-white hover:opacity-90 disabled:opacity-50"
                onClick={handleCreate} disabled={busy || !form.name || form.allocated_capital <= 0}>创建</button>
            </div>
          </div>
        )}
      </section>

      {groups.length === 0 ? (
        <section className="panel p-8 text-center text-slate-500">暂无监控组</section>
      ) : (
        <div className="space-y-3">
          {groups.map((g) => (
            <section key={g.id} className="panel p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{g.name}</span>
                    <span className={`rounded border px-2 py-0.5 text-xs ${STATUS_COLORS[g.status] ?? "border-line bg-panel"}`}>{g.status}</span>
                  </div>
                  {g.description && <p className="mt-1 text-sm text-slate-500">{g.description}</p>}
                  <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-500">
                    <span>分配资金: {money(g.allocated_capital)}</span>
                    <span>ID: {g.id}</span>
                    <span>创建: {new Date(g.created_at).toLocaleDateString()}</span>
                  </div>
                </div>
                <div className="flex shrink-0 flex-wrap gap-2">
                  {g.status === "running" && (
                    <button className="focus-ring rounded border border-amber-200 bg-amber-50 px-3 py-1 text-xs text-amber-800 hover:bg-amber-100 disabled:opacity-50"
                      disabled={busy} onClick={() => handleStatus(g.id, "pause")}>暂停</button>
                  )}
                  {g.status === "paused" && (
                    <button className="focus-ring rounded border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs text-emerald-800 hover:bg-emerald-100 disabled:opacity-50"
                      disabled={busy} onClick={() => handleStatus(g.id, "resume")}>恢复</button>
                  )}
                  {(g.status === "running" || g.status === "paused") && (
                    <button className="focus-ring rounded border border-line px-3 py-1 text-xs hover:bg-panel disabled:opacity-50"
                      disabled={busy} onClick={() => handleStatus(g.id, "stop")}>停止</button>
                  )}
                  <button className="focus-ring rounded border border-red-200 bg-red-50 px-3 py-1 text-xs text-red-700 hover:bg-red-100 disabled:opacity-50"
                    disabled={busy} onClick={() => handleDelete(g.id)}>删除</button>
                </div>
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

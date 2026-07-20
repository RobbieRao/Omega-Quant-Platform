import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useLanguage } from "../i18n";

type Task = Awaited<ReturnType<typeof api.listTasks>>[number];

export function TaskTray() {
  const { language } = useLanguage();
  const zh = language === "zh-CN";
  const [tasks, setTasks] = useState<Task[]>([]);
  const [open, setOpen] = useState(false);

  const refresh = useCallback(async () => {
    try { setTasks(await api.listTasks()); } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, 2000);
    return () => clearInterval(id);
  }, [refresh]);

  const running = tasks.filter((t) => t.status === "running" || t.status === "queued");
  const recent = tasks.slice(0, 8);

  return (
    <>
      <button
        className={`fixed bottom-4 right-4 z-40 flex items-center gap-2 rounded-full border px-4 py-2 text-sm shadow-lg transition ${running.length > 0 ? "border-emerald-300 bg-emerald-50 text-emerald-900" : "border-line bg-white text-slate-700"}`}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={`inline-block h-2 w-2 rounded-full ${running.length > 0 ? "animate-pulse bg-emerald-500" : "bg-slate-300"}`} />
        {zh ? "任务" : "Tasks"} {running.length > 0 ? `(${running.length} running)` : ""}
      </button>
      {open && (
        <div className="fixed bottom-16 right-4 z-40 max-h-[60vh] w-96 overflow-y-auto rounded-lg border border-line bg-white shadow-2xl">
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <span className="text-sm font-semibold">{zh ? "后台任务" : "Background tasks"}</span>
            <button className="text-xs text-slate-500 hover:text-slate-900" onClick={() => setOpen(false)}>{zh ? "关闭" : "Close"}</button>
          </div>
          {recent.length === 0 ? (
            <p className="px-3 py-4 text-center text-sm text-slate-500">{zh ? "暂无任务" : "No tasks"}</p>
          ) : (
            <div className="divide-y divide-line/50">
              {recent.map((t) => {
                const pct = Math.round((t.progress || 0) * 100);
                const tone = t.status === "running" ? "bg-emerald-100 text-emerald-800"
                  : t.status === "completed" ? "bg-blue-100 text-blue-800"
                  : t.status === "failed" ? "bg-red-100 text-red-800"
                  : t.status === "cancelled" ? "bg-slate-100 text-slate-600"
                  : "bg-amber-100 text-amber-800";
                return (
                  <div key={t.task_id} className="px-3 py-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono">{t.kind}</span>
                      <span className={`rounded px-1.5 py-0.5 ${tone}`}>{t.status}</span>
                    </div>
                    <div className="mt-1 text-slate-500">{String(t.meta?.vt_symbol ?? "")} {String(t.meta?.algorithm ?? "")} {t.stage ? `· ${t.stage}` : ""}</div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded bg-slate-100">
                      <div className={`h-full ${t.status === "failed" ? "bg-red-500" : "bg-emerald-500"}`} style={{ width: `${pct}%` }} />
                    </div>
                    <div className="mt-1 flex items-center justify-between text-slate-400">
                      <span>{pct}%</span>
                      {(t.status === "running" || t.status === "queued") && (
                        <button className="text-red-600 hover:underline" onClick={() => api.cancelTask(t.task_id).then(refresh).catch(() => null)}>{zh ? "取消" : "Cancel"}</button>
                      )}
                    </div>
                    {t.error && <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap rounded bg-red-50 p-1 text-red-700">{t.error}</pre>}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </>
  );
}

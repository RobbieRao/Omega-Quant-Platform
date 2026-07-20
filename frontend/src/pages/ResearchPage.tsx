import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useLanguage } from "../i18n";
import type { PlatformCapabilities, PortfolioResearchReport, ResearchAudit } from "../types";

function JsonPanel({ title, value, empty }: { title: string; value: unknown; empty: string }) {
  return (
    <section className="panel min-w-0 p-4">
      <h2 className="text-base font-semibold">{title}</h2>
      {value ? (
        <pre className="mt-3 max-h-[28rem] overflow-auto rounded border border-line bg-slate-950 p-3 text-xs leading-5 text-slate-100">
          {JSON.stringify(value, null, 2)}
        </pre>
      ) : <p className="mt-3 text-sm text-slate-500">{empty}</p>}
    </section>
  );
}

export default function ResearchPage() {
  const { language } = useLanguage();
  const zh = language === "zh-CN";
  const [capabilities, setCapabilities] = useState<PlatformCapabilities | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioResearchReport | null>(null);
  const [audit, setAudit] = useState<ResearchAudit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    const results = await Promise.allSettled([
      api.platformCapabilities(), api.portfolioResearch(), api.researchAudit(50),
    ]);
    const [caps, report, events] = results;
    if (caps.status === "fulfilled") setCapabilities(caps.value);
    if (report.status === "fulfilled") setPortfolio(report.value);
    if (events.status === "fulfilled") setAudit(events.value);
    const failed = results.find((item) => item.status === "rejected") as PromiseRejectedResult | undefined;
    setError(failed ? String(failed.reason) : null);
    setBusy(false);
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  return (
    <div className="space-y-5">
      <section className="overflow-hidden rounded-xl border border-slate-800 bg-gradient-to-r from-slate-950 to-indigo-950 p-5 text-white shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">Omega Research</p>
            <h1 className="mt-2 text-2xl font-semibold">{zh ? "统一量化研究中心" : "Unified Quant Research"}</h1>
            <p className="mt-2 max-w-3xl text-sm text-slate-300">
              {zh ? "组合评估、优化、期权 Greeks、审计轨迹与 LEAN 集成使用同一套 Omega API。" : "Portfolio assessment, optimization, option Greeks, audit trails, and LEAN integration share one Omega API."}
            </p>
          </div>
          <button className="focus-ring rounded border border-cyan-400/50 px-3 py-2 text-sm hover:bg-white/10" onClick={() => void refresh()} disabled={busy}>
            {busy ? (zh ? "刷新中…" : "Refreshing…") : (zh ? "刷新" : "Refresh")}
          </button>
        </div>
      </section>
      {error && <p className="rounded border border-danger/30 bg-red-50 px-3 py-2 text-sm text-danger">{error}</p>}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <JsonPanel title={zh ? "组合研究报告" : "Portfolio Research Report"} value={portfolio} empty={zh ? "没有可用的组合数据。Demo 仍可使用示例研究。" : "No portfolio data is available. Demo research remains available."} />
        <JsonPanel title={zh ? "平台能力" : "Platform Capabilities"} value={capabilities} empty={zh ? "能力信息不可用。" : "Capability data is unavailable."} />
      </div>
      <JsonPanel title={zh ? "审计轨迹" : "Audit Trail"} value={audit} empty={zh ? "尚无审计事件。" : "No audit events yet."} />
    </div>
  );
}

import { Activity, BarChart3, BarChart2, BookOpen, Code2, Cpu, Eye, LayoutDashboard, Route, ShieldCheck, TrendingUp, GitBranch, FlaskConical } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { useEffect, useState } from "react";
import omegaMark from "../assets/omega-mark.svg";
import { TaskTray } from "../components/TaskTray";
import { api, setStoredToken } from "../api";
import type { ExecutionControl, DataModeState } from "../types";
import { useLanguage } from "../i18n";

const navItems = [
  { to: "/dashboard", label: "dashboard" as const, icon: LayoutDashboard },
  { to: "/trading", label: "trading" as const, icon: TrendingUp },
  { to: "/market", label: "marketNav" as const, icon: Eye },
  { to: "/strategies", label: "strategies" as const, icon: BookOpen },
  { to: "/strategy-files", label: "source" as const, icon: Code2 },
  { to: "/backtesting", label: "backtesting" as const, icon: BarChart2 },
  { to: "/risk", label: "risk" as const, icon: ShieldCheck },
  { to: "/option-master", label: "optionRisk" as const, icon: BarChart3 },
  { to: "/option-chain", label: "optionChain" as const, icon: BarChart3 },
  { to: "/spreads", label: "spreads" as const, icon: GitBranch },
  { to: "/algos", label: "algos" as const, icon: Route },
  { to: "/monitor-groups", label: "groups" as const, icon: Activity },
  { to: "/research", label: "research" as const, icon: FlaskConical },
  { to: "/diagnostics", label: "diagnostics" as const, icon: Cpu },
];

export function AppShell() {
  const { language, setLanguage, t } = useLanguage();
  const [execCtrl, setExecCtrl] = useState<ExecutionControl | null>(null);
  const [dataMode, setDataMode] = useState<DataModeState | null>(null);
  const [tokenInput, setTokenInput] = useState<string>(() => {
    try { return localStorage.getItem("omega_api_token") ?? ""; } catch { return ""; }
  });
  const [tokenSavedAt, setTokenSavedAt] = useState<number | null>(null);

  useEffect(() => {
    api.executionControl().then(setExecCtrl).catch(() => null);
    api.dataMode().then(setDataMode).catch(() => null);
    const id = setInterval(() => {
      if (document.visibilityState === "visible") {
        api.executionControl().then(setExecCtrl).catch(() => null);
      }
    }, 15_000);
    return () => clearInterval(id);
  }, []);

  const accountLabel = execCtrl?.target_account?.account_masked
    ? `${execCtrl.target_account.account_masked} · ${execCtrl.target_account.account_kind}`
    : "TigerOpen";
  const armed = Boolean(execCtrl?.live_gateway_armed);
  const liveConnected = Boolean(dataMode?.live_readonly_status?.connected);

  return (
    <div className="min-h-screen bg-[#eef2f6] text-ink">
      <header className="border-b border-line bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-3 px-3 py-3 sm:px-4 lg:flex-row lg:items-center lg:justify-between lg:px-6 lg:py-4">
          <div className="flex min-w-0 items-center gap-3">
            <img src={omegaMark} alt="Omega Quant Platform" className="h-10 w-10 shrink-0 rounded-lg sm:h-12 sm:w-12" />
            <div className="min-w-0">
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">Omega Quant Platform</div>
              <h1 className="truncate text-xl font-semibold sm:text-2xl">{t("productTagline")}</h1>
            </div>
          </div>
          <div className="grid w-full grid-cols-2 gap-2 text-xs sm:flex sm:w-auto sm:flex-wrap sm:items-center sm:justify-end sm:text-sm">
            <span className="rounded border border-line bg-panel px-2 py-2 text-center sm:px-3">{t("account")}: {accountLabel}</span>
            <span className={`rounded border px-2 py-2 text-center font-semibold sm:px-3 ${armed ? "border-emerald-300 bg-emerald-100 text-emerald-900" : "border-red-300 bg-red-50 text-red-700"}`}>
              {armed ? `● ${t("liveReady")}` : `○ ${t("liveLocked")}`}
            </span>
            <span className={`rounded border px-2 py-2 text-center sm:px-3 ${liveConnected ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
              {t("market")}: {liveConnected ? t("connected") : t("disconnected")}
            </span>
            <div className="col-span-2 flex items-center gap-1 sm:col-span-1">
              <input type="password" className="focus-ring w-28 rounded border border-line px-2 py-2 text-xs font-mono" placeholder="API Token" value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)} />
              <button className="focus-ring rounded border border-line px-2 py-2 text-xs hover:bg-panel" onClick={() => { setStoredToken(tokenInput); setTokenSavedAt(Date.now()); }}>{tokenSavedAt && Date.now() - tokenSavedAt < 2000 ? "✓" : t("save")}</button>
              <button className="focus-ring rounded border border-line px-2 py-2 text-xs hover:bg-panel" onClick={() => setLanguage(language === "en" ? "zh-CN" : "en")}>
                {language === "en" ? "中文" : "EN"}
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-7xl grid-cols-1 gap-3 px-3 py-3 sm:px-4 sm:py-4 lg:grid-cols-[220px_1fr] lg:gap-5 lg:px-6 lg:py-5">
        <nav className="panel sticky top-0 z-20 flex h-fit gap-1 overflow-x-auto p-2 lg:static lg:block lg:overflow-visible">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `focus-ring flex shrink-0 items-center gap-2 rounded px-3 py-2 text-left text-sm lg:mb-1 lg:w-full ${
                  isActive ? "bg-accent text-white" : "hover:bg-panel"
                }`
              }
            >
              <Icon size={16} />
              {t(label)}
            </NavLink>
          ))}
        </nav>

        <section className="min-w-0 space-y-4 lg:space-y-5">
          <Outlet />
        </section>
      </main>
      <TaskTray />
    </div>
  );
}

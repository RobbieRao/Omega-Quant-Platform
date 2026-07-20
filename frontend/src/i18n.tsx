import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

export type Language = "en" | "zh-CN";

const messages = {
  en: {
    productTagline: "US equities & options research and execution",
    account: "Account", market: "Market data", liveReady: "LIVE armed", liveLocked: "LIVE locked",
    connected: "Massive live", disconnected: "Disconnected", save: "Save", dashboard: "Dashboard",
    trading: "Trading", marketNav: "Market / Recorder", strategies: "CTA Strategies", source: "Strategy Source",
    backtesting: "Backtesting", risk: "Risk Center", optionRisk: "Option Risk", optionChain: "Option Chain",
    spreads: "Spread Trading", algos: "Algo Execution", groups: "Monitor Groups", research: "Research",
    diagnostics: "Diagnostics",
  },
  "zh-CN": {
    productTagline: "美股与期权研究、风控和执行",
    account: "账户", market: "行情", liveReady: "LIVE 已武装", liveLocked: "LIVE 未武装",
    connected: "Massive 实时", disconnected: "未连接", save: "保存", dashboard: "总控台",
    trading: "交易台", marketNav: "行情 / 录制", strategies: "CTA 策略", source: "策略源码",
    backtesting: "回测", risk: "风控中心", optionRisk: "期权风险", optionChain: "期权报价表",
    spreads: "价差交易", algos: "算法执行", groups: "监控组", research: "研究中心",
    diagnostics: "底层诊断",
  },
} as const;

type MessageKey = keyof typeof messages.en;
type LanguageContextValue = { language: Language; setLanguage: (language: Language) => void; t: (key: MessageKey) => string };
const LanguageContext = createContext<LanguageContextValue | null>(null);

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(() => {
    try { return localStorage.getItem("omega_language") === "zh-CN" ? "zh-CN" : "en"; } catch { return "en"; }
  });
  const value = useMemo<LanguageContextValue>(() => ({
    language,
    setLanguage: (next) => {
      setLanguageState(next);
      try { localStorage.setItem("omega_language", next); } catch { /* ignore */ }
    },
    t: (key) => messages[language][key],
  }), [language]);
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage(): LanguageContextValue {
  const value = useContext(LanguageContext);
  if (!value) throw new Error("useLanguage must be used inside LanguageProvider");
  return value;
}

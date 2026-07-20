import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { AppShell } from "./layout/AppShell";
import DashboardPage from "./pages/DashboardPage";
import TradingPage from "./pages/TradingPage";
import MarketWatchPage from "./pages/MarketWatchPage";
import CtaStrategiesPage from "./pages/CtaStrategiesPage";
import BacktestingPage from "./pages/BacktestingPage";
import { LanguageProvider } from "./i18n";
import ResearchPage from "./pages/ResearchPage";

const StrategyFilesPage = lazy(() => import("./pages/StrategyFilesPage"));
import RiskManagerPage from "./pages/RiskManagerPage";
import OptionMasterPage from "./pages/OptionMasterPage";
import OptionChainPage from "./pages/OptionChainPage";
import SpreadTradingPage from "./pages/SpreadTradingPage";
import AlgoTradingPage from "./pages/AlgoTradingPage";
import MonitorGroupsPage from "./pages/MonitorGroupsPage";
import CoreDiagnosticsPage from "./pages/CoreDiagnosticsPage";

export default function App() {
  return (
    <ErrorBoundary>
    <LanguageProvider>
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/trading" element={<TradingPage />} />
          <Route path="/market" element={<MarketWatchPage />} />
          <Route path="/strategies" element={<CtaStrategiesPage />} />
          <Route path="/strategy-files" element={
            <Suspense fallback={<div className="panel p-6 text-sm text-slate-500">加载代码编辑器中…</div>}>
              <StrategyFilesPage />
            </Suspense>
          } />
          <Route path="/backtesting" element={<BacktestingPage />} />
          <Route path="/risk" element={<RiskManagerPage />} />
          <Route path="/option-master" element={<OptionMasterPage />} />
          <Route path="/option-chain" element={<OptionChainPage />} />
          <Route path="/spreads" element={<SpreadTradingPage />} />
          <Route path="/algos" element={<AlgoTradingPage />} />
          <Route path="/data-recorder" element={<Navigate to="/market" replace />} />
          <Route path="/monitor-groups" element={<MonitorGroupsPage />} />
          <Route path="/research" element={<ResearchPage />} />
          <Route path="/diagnostics" element={<CoreDiagnosticsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
    </LanguageProvider>
    </ErrorBoundary>
  );
}

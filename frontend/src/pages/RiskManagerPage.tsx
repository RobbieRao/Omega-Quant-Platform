import { useCallback, useEffect, useState } from "react";
import { RiskControlPanel } from "../components/RiskControlPanel";
import { api } from "../api";
import type { CoreOrder, CoreTrade, RiskManagerConfigUpdate, RiskManagerEvaluateResult, RiskManagerStatus } from "../types";

export default function RiskManagerPage() {
  const [status, setStatus] = useState<RiskManagerStatus | null>(null);
  const [result, setResult] = useState<RiskManagerEvaluateResult | null>(null);
  const [coreOrders, setCoreOrders] = useState<CoreOrder[]>([]);
  const [coreTrades, setCoreTrades] = useState<CoreTrade[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const [s, orders, trades] = await Promise.allSettled([
        api.riskManagerStatus(),
        api.coreOrders(),
        api.coreTrades(),
      ]);
      if (s.status === "fulfilled") setStatus(s.value);
      if (orders.status === "fulfilled") setCoreOrders(orders.value);
      if (trades.status === "fulfilled") setCoreTrades(trades.value);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const handleSeed = useCallback(async () => {
    setBusy(true);
    try { await api.evaluateRiskOrder({ symbol: "AAPL", side: "buy", quantity: 10, price: 150 }); await refresh(); } finally { setBusy(false); }
  }, [refresh]);

  const handleEvaluate = useCallback(async (payload: { symbol: string; side: "buy" | "sell" | "long" | "short"; quantity: number; price: number; strategy_id?: string | null; now?: string | null }) => {
    setBusy(true);
    try {
      const r = await api.evaluateRiskOrder(payload);
      setResult(r);
    } finally {
      setBusy(false);
    }
  }, []);

  const handleUpdateConfig = useCallback(async (payload: RiskManagerConfigUpdate) => {
    setBusy(true);
    try { const s = await api.updateRiskManagerConfig(payload); setStatus(s); } finally { setBusy(false); }
  }, []);

  const handleCancelOrder = useCallback(async (orderid: string) => {
    setBusy(true);
    try { await api.cancelCoreOrder(orderid); await refresh(); } finally { setBusy(false); }
  }, [refresh]);

  return (
    <RiskControlPanel
      status={status}
      result={result}
      coreOrders={coreOrders}
      coreTrades={coreTrades}
      busy={busy}
      onSeed={handleSeed}
      onEvaluateOrder={handleEvaluate}
      onUpdateConfig={handleUpdateConfig}
      onCancelOrder={handleCancelOrder}
    />
  );
}

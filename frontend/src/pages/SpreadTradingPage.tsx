import { useCallback, useEffect, useState } from "react";
import { SpreadTradingPanel } from "../components/SpreadTradingPanel";
import { api } from "../api";
import type { SpreadData, SpreadLegPayload } from "../types";

type SpreadOrderPayload = { direction: "long" | "short"; volume: number; price_offset?: number; reference?: string };

export default function SpreadTradingPage() {
  const [spreads, setSpreads] = useState<SpreadData[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const data = await api.spreads();
      setSpreads(data);
    } catch {
      // ignore
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const handleCreate = useCallback(async (payload: { name: string; active_leg: string; legs: SpreadLegPayload[] }) => {
    setBusy(true);
    try { await api.createSpread(payload); await refresh(); } finally { setBusy(false); }
  }, [refresh]);

  const handleOrder = useCallback(async (name: string, payload: SpreadOrderPayload) => {
    setBusy(true);
    try { await api.sendSpreadOrder(name, payload); await refresh(); } finally { setBusy(false); }
  }, [refresh]);

  const handleSeed = useCallback(async () => {
    setBusy(true);
    try {
      await api.createSpread({
        name: "SPY_QQQ_pair",
        active_leg: "SPY.SMART",
        legs: [
          { symbol: "SPY", product: "equity", price_multiplier: 1, trading_multiplier: 1, variable_name: "a" },
          { symbol: "QQQ", product: "equity", price_multiplier: -1, trading_multiplier: -1, variable_name: "b" },
        ],
      });
      await refresh();
    } catch {
      // ignore
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  return (
    <SpreadTradingPanel
      spreads={spreads}
      busy={busy}
      onSeed={handleSeed}
      onCreateSpread={handleCreate}
      onSendOrder={handleOrder}
    />
  );
}

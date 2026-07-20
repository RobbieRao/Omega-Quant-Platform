import { useCallback, useEffect, useState } from "react";
import { CoreEnginesPanel } from "../components/CoreEnginesPanel";
import { api } from "../api";
import type { CoreAccount, CoreContract, CoreOrder, CorePosition, CoreRuntimeStatus, CoreTrade } from "../types";

export default function CoreDiagnosticsPage() {
  const [status, setStatus] = useState<CoreRuntimeStatus | null>(null);
  const [coreOrders, setCoreOrders] = useState<CoreOrder[]>([]);
  const [coreTrades, setCoreTrades] = useState<CoreTrade[]>([]);
  const [coreAccounts, setCoreAccounts] = useState<CoreAccount[]>([]);
  const [corePositions, setCorePositions] = useState<CorePosition[]>([]);
  const [coreContracts, setCoreContracts] = useState<CoreContract[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const [s, orders, trades, accounts, positions, contracts] = await Promise.allSettled([
        api.coreStatus(),
        api.coreOrders(),
        api.coreTrades(),
        api.coreAccounts(),
        api.corePositions(),
        api.coreContracts(),
      ]);
      if (s.status === "fulfilled") setStatus(s.value);
      if (orders.status === "fulfilled") setCoreOrders(orders.value);
      if (trades.status === "fulfilled") setCoreTrades(trades.value);
      if (accounts.status === "fulfilled") setCoreAccounts(accounts.value);
      if (positions.status === "fulfilled") setCorePositions(positions.value);
      if (contracts.status === "fulfilled") setCoreContracts(contracts.value);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  return (
    <CoreEnginesPanel
      data={{ status, coreOrders, coreTrades, coreAccounts, corePositions, coreContracts }}
      busy={busy}
      onRefresh={refresh}
    />
  );
}

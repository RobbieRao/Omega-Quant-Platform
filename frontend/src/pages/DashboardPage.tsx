import { useCallback, useEffect, useState } from "react";
import { ExecutionControlPanel } from "../components/ExecutionControlPanel";
import { RealAccountReadonlyDashboard, RealOrderReadonlyDetails } from "../components/RealReadonlyPanels";
import { api } from "../api";
import type {
  ExecutionControl,
  RealAccountSummary,
  RealPosition,
  RealPositionGroup,
  RealOrderReadonly,
  RealStreamStatus,
  RealStreamDetail,
  RealAccountRisk,
} from "../types";
import {
  defaultExecutionControl,
  defaultRealSummary,
  defaultRealStreamStatus,
  defaultRealStreamDetail,
  defaultRealRisk,
} from "../state/workbenchDefaults";

export default function DashboardPage() {
  const [execCtrl, setExecCtrl] = useState<ExecutionControl>(defaultExecutionControl);
  const [summary, setSummary] = useState<RealAccountSummary>(defaultRealSummary);
  const [positions, setPositions] = useState<RealPosition[]>([]);
  const [positionGroups, setPositionGroups] = useState<RealPositionGroup[]>([]);
  const [orders, setOrders] = useState<RealOrderReadonly[]>([]);
  const [streamStatus, setStreamStatus] = useState<RealStreamStatus>(defaultRealStreamStatus);
  const [streamDetail, setStreamDetail] = useState<RealStreamDetail>(defaultRealStreamDetail);
  const [risk, setRisk] = useState<RealAccountRisk>(defaultRealRisk);
  const [selectedOrder, setSelectedOrder] = useState<RealOrderReadonly | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const results = await Promise.allSettled([
      api.executionControl(),
      api.realAccountSummary(),
      api.realPositions(),
      api.realPositionGroups(),
      api.realOrdersReadonly(),
      api.realStreamStatus(),
      api.realStreamDetail(),
      api.realAccountRisk(),
    ]);
    const [ec, sum, pos, groups, ord, ss, sd, rsk] = results;
    if (ec.status === "fulfilled") setExecCtrl(ec.value);
    if (sum.status === "fulfilled") setSummary(sum.value);
    if (pos.status === "fulfilled") setPositions(pos.value);
    if (groups.status === "fulfilled") setPositionGroups(groups.value);
    if (ord.status === "fulfilled") setOrders(ord.value);
    if (ss.status === "fulfilled") setStreamStatus(ss.value);
    if (sd.status === "fulfilled") setStreamDetail(sd.value);
    if (rsk.status === "fulfilled") setRisk(rsk.value);
    const fail = results.find((r) => r.status === "rejected");
    setError(fail ? String((fail as PromiseRejectedResult).reason) : null);
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, 15_000);
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <div className="space-y-5">
      {error && <p className="rounded border border-danger/30 bg-red-50 px-3 py-2 text-sm text-danger">{error}</p>}
      <ExecutionControlPanel control={execCtrl} onRefresh={refresh} />
      <RealAccountReadonlyDashboard
        summary={summary}
        positions={positions}
        positionGroups={positionGroups}
        orders={orders}
        streamStatus={streamStatus}
        streamDetail={streamDetail}
        risk={risk}
        onOpenRealOrder={setSelectedOrder}
      />
      {selectedOrder && <RealOrderReadonlyDetails order={selectedOrder} />}
    </div>
  );
}

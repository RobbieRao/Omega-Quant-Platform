import { useCallback, useEffect, useState } from "react";
import { AlgoExecutionPanel } from "../components/AlgoExecutionPanel";
import { api } from "../api";
import type { AlgoState, AlgoTemplate, IcebergStartPayload, TwapStartPayload } from "../types";

export default function AlgoTradingPage() {
  const [templates, setTemplates] = useState<AlgoTemplate[]>([]);
  const [active, setActive] = useState<AlgoState[]>([]);
  const [finished, setFinished] = useState<AlgoState[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const [t, a, f] = await Promise.allSettled([api.algoTemplates(), api.activeAlgos(), api.finishedAlgos()]);
      if (t.status === "fulfilled") setTemplates(t.value);
      if (a.status === "fulfilled") setActive(a.value);
      if (f.status === "fulfilled") setFinished(f.value);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const withRefresh = useCallback(async (fn: () => Promise<AlgoState>) => {
    setBusy(true);
    try { await fn(); await refresh(); } finally { setBusy(false); }
  }, [refresh]);

  return (
    <AlgoExecutionPanel
      templates={templates}
      active={active}
      finished={finished}
      busy={busy}
      onStartTwap={(p: TwapStartPayload) => withRefresh(() => api.startTwap(p))}
      onStartIceberg={(p: IcebergStartPayload) => withRefresh(() => api.startIceberg(p))}
      onPauseAlgo={(id) => withRefresh(() => api.pauseAlgo(id))}
      onResumeAlgo={(id) => withRefresh(() => api.resumeAlgo(id))}
      onStopAlgo={(id) => withRefresh(() => api.stopAlgo(id))}
    />
  );
}

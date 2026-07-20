import { useCallback, useEffect, useState } from "react";
import { OptionRiskPanel } from "../components/OptionRiskPanel";
import { api } from "../api";
import type {
  OptionChain,
  OptionGreeksExposure,
  OptionScenarioMatrix,
  OptionTigerSyncResult,
  OptionVolCurve,
  RealAccountRisk,
  RealPosition,
  RealPositionGroup,
} from "../types";
import { defaultRealRisk } from "../state/workbenchDefaults";

const DEMO_PORTFOLIO = "omega_default";

export default function OptionMasterPage() {
  const [realPositions, setRealPositions] = useState<RealPosition[]>([]);
  const [positionGroups, setPositionGroups] = useState<RealPositionGroup[]>([]);
  const [realRisk, setRealRisk] = useState<RealAccountRisk>(defaultRealRisk);
  const [chain, setChain] = useState<OptionChain | null>(null);
  const [curve, setCurve] = useState<OptionVolCurve | null>(null);
  const [exposure, setExposure] = useState<OptionGreeksExposure | null>(null);
  const [scenario, setScenario] = useState<OptionScenarioMatrix | null>(null);
  const [syncResult, setSyncResult] = useState<OptionTigerSyncResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const [pos, groups, risk, exp] = await Promise.allSettled([
        api.realPositions(),
        api.realPositionGroups(),
        api.realAccountRisk(),
        api.optionGreeksExposure(DEMO_PORTFOLIO),
      ]);
      if (pos.status === "fulfilled") setRealPositions(pos.value);
      if (groups.status === "fulfilled") setPositionGroups(groups.value);
      if (risk.status === "fulfilled") setRealRisk(risk.value);
      if (exp.status === "fulfilled") setExposure(exp.value);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  type OptionConfigPayload = {
    portfolio: string; underlying: string; expiry: string; contractSymbol: string;
    underlyingPrice: number; lastPrice: number; bidPrice: number; askPrice: number;
    volume: number; netVolume: number; marketValue: number; averagePrice: number;
    riskFreeRate: number; dividendYield: number; daysForward: number;
  };

  const handleConfigure = useCallback(async (payload: OptionConfigPayload) => {
    setError(null);
    setBusy(true);
    try {
      await api.configureOptionPortfolio(payload.portfolio, { risk_free_rate: payload.riskFreeRate, dividend_yield: payload.dividendYield });
      await api.addOptionContract(payload.portfolio, payload.contractSymbol);
      await api.updateOptionUnderlying(payload.portfolio, payload.underlying, payload.underlyingPrice);
      await api.updateOptionQuote(payload.portfolio, {
        symbol: payload.contractSymbol,
        last_price: payload.lastPrice,
        bid_price: payload.bidPrice,
        ask_price: payload.askPrice,
        volume: payload.volume,
      });
      await api.updateOptionPosition(payload.portfolio, {
        symbol: payload.contractSymbol,
        net_volume: payload.netVolume,
        market_value: payload.marketValue,
        average_price: payload.averagePrice,
      });
      const [ch, cv, exp, sc] = await Promise.allSettled([
        api.optionChain(payload.portfolio, payload.underlying, payload.expiry),
        api.optionVolCurve(payload.portfolio, payload.underlying, payload.expiry),
        api.optionGreeksExposure(payload.portfolio),
        api.optionScenarioMatrix(payload.portfolio, payload.underlying, payload.daysForward),
      ]);
      if (ch.status === "fulfilled") setChain(ch.value);
      if (cv.status === "fulfilled") setCurve(cv.value);
      if (exp.status === "fulfilled") setExposure(exp.value);
      if (sc.status === "fulfilled") setScenario(sc.value);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const handleSyncTiger = useCallback(async () => {
    setBusy(true);
    try {
      const r = await api.syncOptionPortfolioFromTiger(DEMO_PORTFOLIO);
      setSyncResult(r);
      await refresh();
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const handleSeedDemo = useCallback(async () => {
    setBusy(true);
    try {
      await api.configureOptionPortfolio(DEMO_PORTFOLIO, { risk_free_rate: 0.05, dividend_yield: 0.0 });
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <div className="space-y-4">
      {error && <p className="rounded border border-danger/30 bg-red-50 px-3 py-2 text-sm text-danger">{error}</p>}
      <OptionRiskPanel
      realPositions={realPositions}
      positionGroups={positionGroups}
      realRisk={realRisk}
      chain={chain}
      curve={curve}
      exposure={exposure}
      scenario={scenario}
      syncResult={syncResult}
      busy={busy}
      onConfigure={handleConfigure}
      onSyncTiger={handleSyncTiger}
      onSeedDemo={handleSeedDemo}
    />
    </div>
  );
}

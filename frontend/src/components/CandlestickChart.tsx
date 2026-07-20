import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

type Bar = { timestamp: string | null; open: number; high: number; low: number; close: number; volume: number };
type TradeMarker = { datetime: string; price: number; direction: string };

function sma(values: number[], window: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (window <= 1 || window > values.length) return out;
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= window) sum -= values[i - window];
    if (i >= window - 1) out[i] = sum / window;
  }
  return out;
}

function stddev(values: number[], window: number, mean: (number | null)[]): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (window <= 1) return out;
  for (let i = window - 1; i < values.length; i++) {
    const m = mean[i];
    if (m === null) continue;
    let acc = 0;
    for (let j = i - window + 1; j <= i; j++) acc += (values[j] - m) ** 2;
    out[i] = Math.sqrt(acc / window);
  }
  return out;
}

function toUtcSeconds(timestamp: string | null): UTCTimestamp | null {
  if (!timestamp) return null;
  const t = Math.floor(new Date(timestamp).getTime() / 1000);
  return Number.isFinite(t) ? (t as UTCTimestamp) : null;
}

type Indicators = {
  ma?: number[]; // window sizes, e.g. [5, 20, 60]
  boll?: { window: number; mult: number };
};

export function CandlestickChart({
  bars,
  trades,
  height = 380,
  indicators,
}: {
  bars: Bar[];
  trades?: TradeMarker[];
  height?: number;
  indicators?: Indicators;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const overlayRefs = useRef<ISeriesApi<"Line">[]>([]);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      height,
      layout: { background: { color: "#ffffff" }, textColor: "#1e293b", fontSize: 11 },
      grid: { vertLines: { color: "#f1f5f9" }, horzLines: { color: "#f1f5f9" } },
      rightPriceScale: { borderColor: "#e2e8f0" },
      timeScale: { borderColor: "#e2e8f0", timeVisible: false, secondsVisible: false },
      crosshair: { mode: 1 },
      autoSize: true,
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#10b981",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
    });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;
    const seen = new Set<number>();
    const data = bars
      .map((b) => {
        const t = toUtcSeconds(b.timestamp);
        if (t === null || seen.has(t)) return null;
        seen.add(t);
        return { time: t, open: b.open, high: b.high, low: b.low, close: b.close };
      })
      .filter((x): x is { time: UTCTimestamp; open: number; high: number; low: number; close: number } => x !== null);
    series.setData(data);

    // Clean previous overlays
    for (const s of overlayRefs.current) {
      try { chart.removeSeries(s); } catch { /* ignore */ }
    }
    overlayRefs.current = [];

    if (data.length > 0) {
      const closes = data.map((d) => d.close);
      const palette = ["#2563eb", "#f59e0b", "#a855f7", "#10b981"];
      // MA overlays
      (indicators?.ma ?? []).forEach((win, idx) => {
        const values = sma(closes, win);
        const points = data.map((d, i) => (values[i] !== null ? { time: d.time, value: values[i] as number } : null))
          .filter((x): x is { time: UTCTimestamp; value: number } => x !== null);
        if (points.length > 0) {
          const line = chart.addSeries(LineSeries, { color: palette[idx % palette.length], lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
          line.setData(points);
          overlayRefs.current.push(line);
        }
      });
      // Bollinger Bands
      if (indicators?.boll) {
        const { window: bw, mult } = indicators.boll;
        const mean = sma(closes, bw);
        const sd = stddev(closes, bw, mean);
        const upper: Array<{ time: UTCTimestamp; value: number }> = [];
        const lower: Array<{ time: UTCTimestamp; value: number }> = [];
        const middle: Array<{ time: UTCTimestamp; value: number }> = [];
        data.forEach((d, i) => {
          const m = mean[i], s = sd[i];
          if (m !== null && s !== null) {
            middle.push({ time: d.time, value: m });
            upper.push({ time: d.time, value: m + mult * s });
            lower.push({ time: d.time, value: m - mult * s });
          }
        });
        if (middle.length > 0) {
          const colors = { mid: "#94a3b8", band: "#cbd5e1" };
          const mLine = chart.addSeries(LineSeries, { color: colors.mid, lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
          mLine.setData(middle); overlayRefs.current.push(mLine);
          const uLine = chart.addSeries(LineSeries, { color: colors.band, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
          uLine.setData(upper); overlayRefs.current.push(uLine);
          const lLine = chart.addSeries(LineSeries, { color: colors.band, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
          lLine.setData(lower); overlayRefs.current.push(lLine);
        }
      }
      chart.timeScale().fitContent();
    }
  }, [bars, indicators]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !trades) return;
    const markers = trades
      .map((t) => {
        const ts = toUtcSeconds(t.datetime);
        if (ts === null) return null;
        const isBuy = t.direction.toLowerCase().includes("long") || t.direction.toLowerCase().includes("buy");
        return {
          time: ts as Time,
          position: isBuy ? "belowBar" : "aboveBar",
          color: isBuy ? "#10b981" : "#ef4444",
          shape: isBuy ? "arrowUp" : "arrowDown",
          text: isBuy ? `B@${t.price.toFixed(2)}` : `S@${t.price.toFixed(2)}`,
        };
      })
      .filter((x): x is NonNullable<typeof x> => x !== null)
      .sort((a, b) => (a.time as number) - (b.time as number));
    // lightweight-charts v5: markers go through createSeriesMarkers plugin
    import("lightweight-charts").then(({ createSeriesMarkers }) => {
      // @ts-expect-error - markers types vary by version, but values are valid
      createSeriesMarkers(series, markers);
    });
  }, [trades]);

  return <div ref={containerRef} className="w-full" style={{ height }} />;
}

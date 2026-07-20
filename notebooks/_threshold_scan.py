"""Threshold sensitivity scan on the cached 5-stock data.

Loads the cached per-stock options CSVs + stock OHLCV via Massive REST,
then sweeps (vol_spike_x, pc_zscore, lookback) and reports hit rate + FP rate
for each combination. Output: reports/tianlongren_threshold_scan_<ts>.md
"""
import sys, time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from omega_quant.config import Settings, load_env_file
from omega_quant.adapters.massive_client import build_rest_client

settings = Settings()
load_env_file(settings.tiger_env_path)
client = build_rest_client(settings)

# === parameters ===
STOCKS = ["FUTU", "NIO", "PDD", "BIDU", "TAL"]
DATE_START = "2025-05-23"
DATE_END   = "2026-05-22"
EVENT_THRESHOLD = 0.12
MARKET_TICKER = "QQQ"
MARKET_THRESHOLD = 0.04
CALM_NEXTDAY = 0.04
CACHE_DIR = PROJECT_ROOT / "reports" / "tianlongren_cache"

# Grid to scan
VOL_SPIKES   = [1.5, 2.0, 2.5, 3.0]
PC_ZSCORES   = [1.0, 1.5, 2.0, 2.5]
LOOKBACK_SETS = {
    "T-1..3": [1, 2, 3],
    "T-1..5": [1, 2, 3, 4, 5],
    "T-1..7": [1, 2, 3, 4, 5, 6, 7],
}

# === load stock OHLCV ===
def fetch_daily(ticker):
    aggs = list(client.list_aggs(ticker, 1, "day", from_=DATE_START, to=DATE_END,
                                 adjusted=True, sort="asc", limit=400))
    rows = [{"date": pd.Timestamp(a.timestamp, unit="ms", tz="UTC").strftime("%Y-%m-%d"),
             "open": a.open, "high": a.high, "low": a.low, "close": a.close,
             "volume": int(a.volume or 0)} for a in aggs]
    df = pd.DataFrame(rows)
    if df.empty: return df
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()

stock_daily = {}
with ThreadPoolExecutor(max_workers=8) as ex:
    futs = {ex.submit(fetch_daily, t): t for t in STOCKS + [MARKET_TICKER]}
    for f in as_completed(futs):
        stock_daily[futs[f]] = f.result()

# === identify event days (same as notebook) ===
qqq = stock_daily[MARKET_TICKER].copy()
qqq["qqq_c2c"] = qqq["close"].pct_change()

events = []
for t in STOCKS:
    df = stock_daily[t]
    if df.empty: continue
    c2c = df["close"].pct_change()
    for d, r in c2c.items():
        if pd.isna(r) or abs(r) <= EVENT_THRESHOLD: continue
        qm = qqq["qqq_c2c"].get(d, 0.0)
        if pd.notna(qm) and abs(qm) >= MARKET_THRESHOLD: continue
        events.append({"ticker": t, "date": d, "event_return": float(r)})

events_df = pd.DataFrame(events).sort_values(["ticker","date"]).reset_index(drop=True)
print(f"Events: {len(events_df)}", flush=True)

# === load cached options + aggregate ===
all_rows = []
for t in STOCKS:
    cache_file = CACHE_DIR / f"{t}.csv"
    if not cache_file.exists():
        print(f"  WARN: cache missing for {t}", flush=True)
        continue
    df = pd.read_csv(cache_file)
    all_rows.append(df)

opt_daily = pd.concat(all_rows, ignore_index=True)
opt_daily["date"] = pd.to_datetime(opt_daily["date"])

agg = opt_daily.groupby(["ticker","date","type"])["volume"].sum().unstack(fill_value=0)
if "call" not in agg.columns: agg["call"] = 0
if "put"  not in agg.columns: agg["put"]  = 0
agg["total"] = agg["call"] + agg["put"]
agg["pc"] = agg["put"] / agg["call"].replace(0, np.nan)
agg = agg.reset_index().sort_values(["ticker","date"])

agg["vol_ma30"]  = agg.groupby("ticker")["total"].transform(lambda s: s.shift(1).rolling(30, min_periods=10).mean())
agg["pc_mean30"] = agg.groupby("ticker")["pc"].transform(lambda s: s.shift(1).rolling(30, min_periods=10).mean())
agg["pc_std30"]  = agg.groupby("ticker")["pc"].transform(lambda s: s.shift(1).rolling(30, min_periods=10).std())
agg["vol_zscore"] = agg["total"] / agg["vol_ma30"]
agg["pc_zscore"]  = (agg["pc"] - agg["pc_mean30"]) / agg["pc_std30"]
print(f"Total daily ticker-rows: {len(agg)}", flush=True)

# stock next-day returns for FP calc
stock_returns = {}
for t in STOCKS:
    df = stock_daily[t].copy()
    df["next_ret"] = df["close"].pct_change().shift(-1)
    stock_returns[t] = df

# === scan ===
event_set = set((r["ticker"], pd.Timestamp(r["date"])) for _, r in events_df.iterrows())

def signal_triggered(row, direction, vsx, pcz):
    vz = row.get("vol_zscore"); pz = row.get("pc_zscore")
    if pd.isna(vz) or pd.isna(pz): return False
    if vz <= vsx: return False
    if direction == "down": return pz > pcz
    else: return pz < -pcz

def evaluate(vsx, pcz, lookback):
    # hits
    hits = 0
    hit_details = []
    for _, e in events_df.iterrows():
        direction = "down" if e["event_return"] < 0 else "up"
        df_t = agg[agg["ticker"] == e["ticker"]].set_index("date")
        avail = df_t.index[df_t.index < pd.Timestamp(e["date"])].sort_values()
        if len(avail) < max(lookback): continue
        for off in lookback:
            if off > len(avail): break
            row = df_t.loc[avail[-off]]
            if signal_triggered(row, direction, vsx, pcz):
                hits += 1
                hit_details.append((e["ticker"], e["date"].strftime("%Y-%m-%d"), direction, off))
                break
    # false positives
    n_trig, n_calm = 0, 0
    for _, r in agg.iterrows():
        if (r["ticker"], r["date"]) in event_set: continue
        vz, pz = r["vol_zscore"], r["pc_zscore"]
        if pd.isna(vz) or pd.isna(pz): continue
        if vz <= vsx or abs(pz) <= pcz: continue
        df_t = stock_returns.get(r["ticker"])
        if df_t is None: continue
        nxt = df_t["next_ret"].get(r["date"], None)
        if nxt is None or pd.isna(nxt): continue
        n_trig += 1
        if abs(nxt) < CALM_NEXTDAY: n_calm += 1
    return {
        "vol_spike": vsx, "pc_zscore": pcz,
        "lookback": lookback,
        "events": len(events_df), "hits": hits,
        "hit_rate": hits / len(events_df) if len(events_df) else 0,
        "n_trig": n_trig, "n_calm": n_calm,
        "fp_rate": n_calm / n_trig if n_trig else 0,
        "hit_details": hit_details,
    }

results = []
for lname, lset in LOOKBACK_SETS.items():
    for vsx in VOL_SPIKES:
        for pcz in PC_ZSCORES:
            r = evaluate(vsx, pcz, lset)
            r["lookback_name"] = lname
            results.append(r)
            print(f"  {lname:8s}  vol>{vsx:>3.1f}  |pc|>{pcz:>3.1f}  →  hit {r['hits']}/{r['events']} ({r['hit_rate']*100:>4.1f}%)  "
                  f"FP {r['n_calm']}/{r['n_trig']} ({r['fp_rate']*100:>4.0f}%)", flush=True)

# === report ===
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
out_path = PROJECT_ROOT / "reports" / f"tianlongren_threshold_scan_{TS}.md"
lines = []
lines.append("# 天龙人检测器 — 阈值敏感性扫描（维度 A 中概股）")
lines.append(f"\n生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
lines.append(f"股票池：{', '.join(STOCKS)} ({len(STOCKS)} 支)  事件数：{len(events_df)}")
lines.append(f"\n维度 B（COIN）数据待补充。所有阈值在同一事件样本上比较。\n")

for lname in LOOKBACK_SETS:
    lines.append(f"\n## 回看窗口 {lname}")
    lines.append("| vol_spike | pc_zscore | hits | hit_rate | n_trig | FP_rate |")
    lines.append("|----------:|----------:|-----:|---------:|-------:|--------:|")
    for r in results:
        if r["lookback_name"] != lname: continue
        lines.append(f"| {r['vol_spike']:.1f} | {r['pc_zscore']:.1f} | {r['hits']}/{r['events']} | "
                     f"**{r['hit_rate']*100:.1f}%** | {r['n_trig']} | {r['fp_rate']*100:.0f}% |")

# best by F1-like: high hit_rate - high FP_rate (rough quality score)
def score(r):
    return r["hit_rate"] - 0.3 * r["fp_rate"]   # penalize FP moderately
best = sorted(results, key=score, reverse=True)[:5]
lines.append("\n## Top 5（按 hit_rate − 0.3 × FP_rate 排序）")
lines.append("| Lookback | vol_spike | pc_zscore | hit_rate | FP_rate | 命中事件 |")
lines.append("|----------|----------:|----------:|---------:|--------:|----------|")
for r in best:
    events_str = ", ".join(f"{t}@{d}({dir},T-{off})" for t,d,dir,off in r["hit_details"]) or "-"
    lines.append(f"| {r['lookback_name']} | {r['vol_spike']:.1f} | {r['pc_zscore']:.1f} | "
                 f"**{r['hit_rate']*100:.1f}%** | {r['fp_rate']*100:.0f}% | {events_str} |")

lines.append("\n## 读图建议")
lines.append("- hit_rate 升高常伴随 FP_rate 升高 — 看的是边际权衡")
lines.append("- 如果某档阈值 hit_rate < 30% 且 FP_rate > 70% → 该方向无救")
lines.append("- 等 COIN（维度 B）数据回来后，要看 hit_rate 差值（A − B），而不是 A 单独")

out_path.write_text("\n".join(lines), encoding="utf-8")
print(f"\nReport: {out_path}")

"""Targeted tick-level sweep filter on 193 trigger days (5 hits + 188 FPs).

Step 1: Reproduce trigger list from directional_scan at X=2.0
Step 2: For each (ticker, trigger_date, direction), find top-3 contracts by day volume
Step 3: For each (top_contract, trigger_date), pull list_trades and compute:
        - iso_ratio   = sum(size where conditions ∩ ISO_CODES) / total_size
        - peak_1s     = max(size aggregated in any 1-second window) / total_size
Step 4: Aggregate per trigger (volume-weighted across its 3 contracts)
Step 5: Filter: iso_ratio > 0.20 AND peak_1s > 0.10
Step 6: Report hit retention + FP purge + final precision
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

settings = Settings(); load_env_file(settings.tiger_env_path)
client = build_rest_client(settings)

STOCKS = ["FUTU", "NIO", "PDD", "BIDU", "TAL"]
DATE_START = "2025-05-23"; DATE_END = "2026-05-22"
EVENT_THRESHOLD = 0.12
MARKET_TICKER = "QQQ"; MARKET_THRESHOLD = 0.04
DIRECTIONAL_X = 2.0
LOOKBACK = [1, 2, 3]
CALM_NEXTDAY = 0.04

ISO_CODES = {219, 228, 230}
SWEEP_RATIO_THRESHOLD = 0.20
PEAK_1S_THRESHOLD = 0.10

TOP_K_CONTRACTS = 3
EXPIRY_WINDOW_DAYS = 45     # tightened from 90 — near-term flow concentrates here
STRIKE_BAND = (0.85, 1.15)  # tightened from (0.7,1.3) — ATM ± 15%

CACHE_DIR = PROJECT_ROOT / "reports" / "tianlongren_cache"

print("Stage 1: Identifying triggers...", flush=True)

def fetch_daily(t):
    aggs = list(client.list_aggs(t, 1, "day", from_=DATE_START, to=DATE_END, adjusted=True, sort="asc", limit=400))
    rows = [{"date": pd.Timestamp(a.timestamp, unit="ms", tz="UTC").strftime("%Y-%m-%d"),
             "open": a.open, "close": a.close, "low": a.low, "high": a.high} for a in aggs]
    df = pd.DataFrame(rows); df["date"] = pd.to_datetime(df["date"]); return df.set_index("date")

stock_daily = {}
with ThreadPoolExecutor(max_workers=8) as ex:
    futs = {ex.submit(fetch_daily, t): t for t in STOCKS + [MARKET_TICKER]}
    for f in as_completed(futs): stock_daily[futs[f]] = f.result()

qqq = stock_daily[MARKET_TICKER].copy(); qqq["q"] = qqq["close"].pct_change()
events = []
for t in STOCKS:
    df = stock_daily[t]; c2c = df["close"].pct_change()
    for d, r in c2c.items():
        if pd.isna(r) or abs(r) <= EVENT_THRESHOLD: continue
        qm = qqq["q"].get(d, 0.0)
        if pd.notna(qm) and abs(qm) >= MARKET_THRESHOLD: continue
        events.append({"ticker": t, "date": d, "event_return": float(r)})
events_df = pd.DataFrame(events).sort_values(["ticker", "date"]).reset_index(drop=True)
print(f"  events: {len(events_df)}")

all_rows = []
for t in STOCKS:
    df = pd.read_csv(CACHE_DIR / f"{t}.csv"); all_rows.append(df)
opt = pd.concat(all_rows, ignore_index=True); opt["date"] = pd.to_datetime(opt["date"])
agg = opt.groupby(["ticker", "date", "type"])["volume"].sum().unstack(fill_value=0)
if "call" not in agg.columns: agg["call"] = 0
if "put"  not in agg.columns: agg["put"]  = 0
agg = agg.reset_index().sort_values(["ticker", "date"])
agg["call_ma30"] = agg.groupby("ticker")["call"].transform(lambda s: s.shift(1).rolling(30, min_periods=10).mean())
agg["put_ma30"]  = agg.groupby("ticker")["put"].transform(lambda s: s.shift(1).rolling(30, min_periods=10).mean())
agg["call_z"] = agg["call"] / agg["call_ma30"]
agg["put_z"]  = agg["put"]  / agg["put_ma30"]

event_set = set((r["ticker"], pd.Timestamp(r["date"])) for _, r in events_df.iterrows())
stock_returns = {t: stock_daily[t].assign(next_ret=stock_daily[t]["close"].pct_change().shift(-1)) for t in STOCKS}

event_triggers = []
for _, e in events_df.iterrows():
    direction = "down" if e["event_return"] < 0 else "up"
    df_t = agg[agg["ticker"] == e["ticker"]].set_index("date")
    avail = df_t.index[df_t.index < pd.Timestamp(e["date"])].sort_values()
    if len(avail) < max(LOOKBACK): continue
    for off in LOOKBACK:
        if off > len(avail): break
        row = df_t.loc[avail[-off]]
        z = row["put_z"] if direction == "down" else row["call_z"]
        if pd.notna(z) and z > DIRECTIONAL_X:
            event_triggers.append({
                "ticker": e["ticker"], "trigger_date": avail[-off], "direction": direction,
                "is_event": True, "event_date": pd.Timestamp(e["date"]), "event_return": e["event_return"],
                "z": float(z),
            })
            break

fp_triggers = []
for _, r in agg.iterrows():
    if (r["ticker"], r["date"]) in event_set: continue
    cz, pz = r["call_z"], r["put_z"]
    spike_call = pd.notna(cz) and cz > DIRECTIONAL_X
    spike_put  = pd.notna(pz) and pz > DIRECTIONAL_X
    if not (spike_call or spike_put): continue
    df_t = stock_returns[r["ticker"]]
    nxt = df_t["next_ret"].get(r["date"], None)
    if nxt is None or pd.isna(nxt): continue
    if spike_put and (not spike_call or pz >= cz):
        direction = "down"; z = pz
    else:
        direction = "up"; z = cz
    fp_triggers.append({
        "ticker": r["ticker"], "trigger_date": r["date"], "direction": direction,
        "is_event": False, "next_ret": float(nxt), "calm": abs(nxt) < CALM_NEXTDAY,
        "z": float(z),
    })

triggers = event_triggers + fp_triggers
print(f"  event triggers: {len(event_triggers)}")
print(f"  FP triggers:    {len(fp_triggers)}  (calm={sum(t['calm'] for t in fp_triggers)})")
print(f"  total triggers: {len(triggers)}")

print("\nStage 2: Identifying top contracts per trigger (refactored)...", flush=True)
contracts_by_ticker = {}
for t in STOCKS:
    cs = list(client.list_options_contracts(
        underlying_ticker=t, expired=True,
        expiration_date_gte=DATE_START, expiration_date_lte="2026-11-30",
    )) + list(client.list_options_contracts(
        underlying_ticker=t, expired=False,
        expiration_date_lte="2026-11-30",
    ))
    seen = set(); out = []
    for c in cs:
        if c.ticker in seen: continue
        seen.add(c.ticker); out.append(c)
    # Pre-parse expiry once + bucket by (ticker, type)
    contracts_by_ticker[t] = {"call": [], "put": []}
    for c in out:
        try:
            exp = datetime.strptime(c.expiration_date, "%Y-%m-%d").date()
        except Exception:
            continue
        rec = {"ticker": c.ticker, "exp": exp, "strike": float(c.strike_price), "type": c.contract_type}
        contracts_by_ticker[t][c.contract_type].append(rec)
    print(f"  {t}: {len(out)} contracts → {len(contracts_by_ticker[t]['call'])} calls / {len(contracts_by_ticker[t]['put'])} puts", flush=True)

print("  selecting top contracts by heuristic (no API calls)...", flush=True)
# Heuristic: for each trigger, pick top-3 contracts in matching direction with:
#   1) nearest expiry on/after trigger_date (front-week / front-month)
#   2) strike closest to ATM (stock close on trigger_date)
# This approximates "where the action is" without needing per-contract volume queries.
t0 = time.time()
for trig in triggers:
    ticker = trig["ticker"]; td = trig["trigger_date"]; direction = trig["direction"]
    df = stock_daily[ticker]
    if td not in df.index:
        trig["top_contracts"] = []
        continue
    close = float(df.loc[td, "close"])
    lo = close * STRIKE_BAND[0]; hi = close * STRIKE_BAND[1]
    want_type = "put" if direction == "down" else "call"
    td_date = td.date()
    expiry_max = (td + pd.Timedelta(days=EXPIRY_WINDOW_DAYS)).date()
    cands = [c for c in contracts_by_ticker[ticker][want_type]
             if td_date <= c["exp"] <= expiry_max and lo <= c["strike"] <= hi]
    if not cands:
        trig["top_contracts"] = []; continue
    # Find nearest expiry (front-week, typically Friday)
    nearest_exp = min(c["exp"] for c in cands)
    # Keep only contracts with this expiry, sorted by |strike - close|
    same_exp = sorted([c for c in cands if c["exp"] == nearest_exp],
                      key=lambda c: abs(c["strike"] - close))
    # Take top 3 closest to ATM. If fewer than 3, fall through to next expiry.
    picked = same_exp[:TOP_K_CONTRACTS]
    if len(picked) < TOP_K_CONTRACTS:
        rest = sorted([c for c in cands if c["exp"] != nearest_exp],
                      key=lambda c: (c["exp"], abs(c["strike"] - close)))
        picked.extend(rest[:TOP_K_CONTRACTS - len(picked)])
    # store as (contract_dict, sentinel_volume)
    trig["top_contracts"] = [(c, 0) for c in picked]
print(f"  done in {time.time()-t0:.2f}s; triggers with top contract: "
      f"{sum(1 for t in triggers if t['top_contracts'])}/{len(triggers)}", flush=True)

print("\nStage 3: Fetching tick-level trades for top contracts...", flush=True)
def fetch_and_score(contract_ticker, td):
    td_str = td.strftime("%Y-%m-%d")
    next_day = (td + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        trades = list(client.list_trades(
            contract_ticker,
            timestamp_gte=td_str, timestamp_lt=next_day,
            limit=50000, sort="timestamp", order="asc",
        ))
    except Exception:
        return None
    if not trades: return None
    iso_size = 0; total_size = 0
    by_second = {}
    for tr in trades:
        sz = int(tr.size or 0)
        if sz <= 0: continue
        total_size += sz
        conds = set(tr.conditions or [])
        if conds & ISO_CODES: iso_size += sz
        ts_ns = tr.sip_timestamp or tr.participant_timestamp or 0
        sec_bucket = ts_ns // 1_000_000_000
        by_second[sec_bucket] = by_second.get(sec_bucket, 0) + sz
    if total_size == 0: return None
    peak_1s = max(by_second.values()) if by_second else 0
    return {
        "total_size": total_size, "iso_size": iso_size,
        "iso_ratio": iso_size / total_size,
        "peak_1s_size": peak_1s, "peak_1s_ratio": peak_1s / total_size,
        "n_trades": len(trades),
    }

work = []
for ti, trig in enumerate(triggers):
    for c, _v in trig["top_contracts"]:
        work.append((c["ticker"], trig["trigger_date"], ti))
print(f"  total (contract, day) jobs: {len(work)}")

results_by_ti = {}
t0 = time.time(); done = 0
with ThreadPoolExecutor(max_workers=10) as ex:
    futs = {ex.submit(fetch_and_score, c, d): (c, d, ti) for c, d, ti in work}
    for f in as_completed(futs):
        c, d, ti = futs[f]
        r = f.result()
        if r: results_by_ti.setdefault(ti, []).append(r)
        done += 1
        if done % 50 == 0:
            print(f"  trades: {done}/{len(work)}  ({time.time()-t0:.0f}s)", flush=True)
print(f"  done in {time.time()-t0:.0f}s")

print("\nStage 4: Aggregating sweep metrics per trigger...", flush=True)
rows = []
for ti, trig in enumerate(triggers):
    metrics_list = results_by_ti.get(ti, [])
    if not metrics_list:
        rows.append({"ticker": trig["ticker"], "trigger_date": trig["trigger_date"],
                     "direction": trig["direction"], "is_event": trig["is_event"],
                     "calm": trig.get("calm"), "event_date": trig.get("event_date"),
                     "n_contracts_with_data": 0, "iso_ratio": None, "peak_1s_ratio": None,
                     "total_size": 0})
        continue
    tot = sum(m["total_size"] for m in metrics_list)
    iso = sum(m["iso_size"] for m in metrics_list)
    peak = max(m["peak_1s_size"] for m in metrics_list)
    rows.append({"ticker": trig["ticker"], "trigger_date": trig["trigger_date"],
                 "direction": trig["direction"], "is_event": trig["is_event"],
                 "calm": trig.get("calm"), "event_date": trig.get("event_date"),
                 "n_contracts_with_data": len(metrics_list),
                 "iso_ratio": iso / tot if tot else None,
                 "peak_1s_ratio": peak / tot if tot else None,
                 "total_size": tot})
result_df = pd.DataFrame(rows)

print("\nStage 5: Applying sweep filter...", flush=True)
def passes(row):
    iso = row["iso_ratio"]; p1 = row["peak_1s_ratio"]
    if iso is None or p1 is None: return False
    return (iso > SWEEP_RATIO_THRESHOLD) and (p1 > PEAK_1S_THRESHOLD)
result_df["passes_sweep"] = result_df.apply(passes, axis=1)

events_total = int(result_df["is_event"].sum())
events_kept  = int(result_df[(result_df["is_event"]) & (result_df["passes_sweep"])].shape[0])
fps_total    = int(((~result_df["is_event"]) & (result_df["calm"] == True)).sum())
fps_kept     = int(result_df[(~result_df["is_event"]) & (result_df["calm"] == True) & (result_df["passes_sweep"])].shape[0])
all_nonevent = int((~result_df["is_event"]).sum())
all_nonevent_kept = int(result_df[(~result_df["is_event"]) & (result_df["passes_sweep"])].shape[0])

print("\n" + "=" * 70)
print(f"原命中事件: {events_total}  → 保留: {events_kept}  ({events_kept/max(events_total,1)*100:.1f}%)")
print(f"原假阳性 (calm): {fps_total}  → 剩余: {fps_kept}  清洗率 {(fps_total-fps_kept)/max(fps_total,1)*100:.1f}%")
print(f"所有非事件触发: {all_nonevent}  → 剩余: {all_nonevent_kept}")
print(f"最终精度: {events_kept}/{events_kept+all_nonevent_kept} = "
      f"{events_kept/max(events_kept+all_nonevent_kept,1)*100:.1f}%")
print("=" * 70)

TS = datetime.now().strftime("%Y%m%d_%H%M%S")
out = PROJECT_ROOT / "reports" / f"tianlongren_sweep_filter_{TS}.md"
lines = []
lines.append("# 天龙人检测器 — 微观扫盘二层提纯")
lines.append(f"\n生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
lines.append(f"基础信号：directional_vol_z > {DIRECTIONAL_X}（T-1~T-3）")
lines.append(f"扫盘过滤：iso_ratio > {SWEEP_RATIO_THRESHOLD} AND peak_1s_ratio > {PEAK_1S_THRESHOLD}")
lines.append(f"ISO codes: {sorted(ISO_CODES)}（219=ISO, 228=Auction ISO, 230=Cross ISO）")
lines.append("\n## 核心数字")
lines.append("| 指标 | 数值 |")
lines.append("|------|------|")
lines.append(f"| 原命中事件 | {events_total} |")
lines.append(f"| 过滤后保留事件 | {events_kept} |")
lines.append(f"| **事件保留率** | **{events_kept/max(events_total,1)*100:.1f}%** |")
lines.append(f"| 原假阳性 calm | {fps_total} |")
lines.append(f"| 过滤后剩余 calm | {fps_kept} |")
lines.append(f"| **FP 清洗率** | **{(fps_total-fps_kept)/max(fps_total,1)*100:.1f}%** |")
lines.append(f"| 全部非事件触发 | {all_nonevent} |")
lines.append(f"| 过滤后剩余 | {all_nonevent_kept} |")
lines.append(f"| **最终精度** | **{events_kept/max(events_kept+all_nonevent_kept,1)*100:.1f}%** |")

lines.append("\n## 事件级详情")
lines.append("| Ticker | Trigger | Dir | iso_ratio | peak_1s_ratio | passes |")
lines.append("|--------|---------|-----|----------:|--------------:|--------|")
for _, r in result_df[result_df["is_event"]].iterrows():
    iso = f"{r['iso_ratio']*100:.1f}%" if r["iso_ratio"] is not None else "n/a"
    p1  = f"{r['peak_1s_ratio']*100:.1f}%" if r["peak_1s_ratio"] is not None else "n/a"
    lines.append(f"| {r['ticker']} | {r['trigger_date'].strftime('%Y-%m-%d')} | {r['direction']} | {iso} | {p1} | {'✅' if r['passes_sweep'] else '❌'} |")

lines.append("\n## 通过过滤的非事件触发（剩余可疑）")
remain = result_df[(~result_df["is_event"]) & (result_df["passes_sweep"])]
if len(remain):
    lines.append("| Ticker | Trigger | Dir | iso_ratio | peak_1s_ratio | calm? |")
    lines.append("|--------|---------|-----|----------:|--------------:|-------|")
    for _, r in remain.iterrows():
        iso = f"{r['iso_ratio']*100:.1f}%"; p1 = f"{r['peak_1s_ratio']*100:.1f}%"
        lines.append(f"| {r['ticker']} | {r['trigger_date'].strftime('%Y-%m-%d')} | {r['direction']} | {iso} | {p1} | {r['calm']} |")
else:
    lines.append("（无）")

lines.append("\n## 全部触发的指标分布")
desc = result_df[["iso_ratio", "peak_1s_ratio"]].describe()
lines.append("```\n" + desc.to_string() + "\n```")

out.write_text("\n".join(lines), encoding="utf-8")
print(f"\nReport: {out}")
result_df.to_csv(PROJECT_ROOT / "reports" / f"tianlongren_sweep_raw_{TS}.csv", index=False)

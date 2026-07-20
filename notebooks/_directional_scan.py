"""Directional volume scan: drop P/C ratio, use put-only / call-only volume z-score.

Signal logic:
  For ↓ events: put_volume / put_ma30 > X
  For ↑ events: call_volume / call_ma30 > X

FP: on non-event days, signal triggered when EITHER put-side OR call-side spikes,
    then we check if next-day |return| < CALM_NEXTDAY.
"""
import sys
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
DATE_START = "2025-05-23"
DATE_END   = "2026-05-22"
EVENT_THRESHOLD = 0.12
MARKET_TICKER = "QQQ"
MARKET_THRESHOLD = 0.04
CALM_NEXTDAY = 0.04
CACHE_DIR = PROJECT_ROOT / "reports" / "tianlongren_cache"

DIRECTIONAL_X = [2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
LOOKBACK_SETS = {"T-1..3": [1,2,3], "T-1..5": [1,2,3,4,5], "T-1..7": list(range(1,8))}

def fetch_daily(t):
    aggs = list(client.list_aggs(t, 1, "day", from_=DATE_START, to=DATE_END, adjusted=True, sort="asc", limit=400))
    rows=[{"date":pd.Timestamp(a.timestamp,unit="ms",tz="UTC").strftime("%Y-%m-%d"),
           "open":a.open,"close":a.close} for a in aggs]
    df=pd.DataFrame(rows); df["date"]=pd.to_datetime(df["date"]); return df.set_index("date")

stock_daily={}
with ThreadPoolExecutor(max_workers=8) as ex:
    futs={ex.submit(fetch_daily,t):t for t in STOCKS+[MARKET_TICKER]}
    for f in as_completed(futs): stock_daily[futs[f]]=f.result()

qqq=stock_daily[MARKET_TICKER].copy(); qqq["q"]=qqq["close"].pct_change()
events=[]
for t in STOCKS:
    df=stock_daily[t]; c2c=df["close"].pct_change()
    for d,r in c2c.items():
        if pd.isna(r) or abs(r)<=EVENT_THRESHOLD: continue
        qm=qqq["q"].get(d,0.0)
        if pd.notna(qm) and abs(qm)>=MARKET_THRESHOLD: continue
        events.append({"ticker":t,"date":d,"event_return":float(r)})
events_df=pd.DataFrame(events).sort_values(["ticker","date"]).reset_index(drop=True)
print(f"Events: {len(events_df)}", flush=True)

all_rows=[]
for t in STOCKS:
    df=pd.read_csv(CACHE_DIR/f"{t}.csv"); all_rows.append(df)
opt=pd.concat(all_rows,ignore_index=True); opt["date"]=pd.to_datetime(opt["date"])

agg=opt.groupby(["ticker","date","type"])["volume"].sum().unstack(fill_value=0)
if "call" not in agg.columns: agg["call"]=0
if "put"  not in agg.columns: agg["put"]=0
agg=agg.reset_index().sort_values(["ticker","date"])

# directional 30-day baselines (shift(1) so we don't peek)
agg["call_ma30"]=agg.groupby("ticker")["call"].transform(lambda s: s.shift(1).rolling(30, min_periods=10).mean())
agg["put_ma30"] =agg.groupby("ticker")["put"].transform(lambda s: s.shift(1).rolling(30, min_periods=10).mean())
agg["call_z"]=agg["call"]/agg["call_ma30"]
agg["put_z"] =agg["put"] /agg["put_ma30"]
print(f"Daily rows: {len(agg)}", flush=True)

stock_returns={}
for t in STOCKS:
    df=stock_daily[t].copy(); df["next_ret"]=df["close"].pct_change().shift(-1)
    stock_returns[t]=df

event_set=set((r["ticker"], pd.Timestamp(r["date"])) for _,r in events_df.iterrows())

def evaluate(x, lookback):
    hits=0; hit_details=[]
    for _,e in events_df.iterrows():
        direction="down" if e["event_return"]<0 else "up"
        df_t=agg[agg["ticker"]==e["ticker"]].set_index("date")
        avail=df_t.index[df_t.index<pd.Timestamp(e["date"])].sort_values()
        if len(avail)<max(lookback): continue
        for off in lookback:
            if off>len(avail): break
            row=df_t.loc[avail[-off]]
            z = row["put_z"] if direction=="down" else row["call_z"]
            if pd.notna(z) and z>x:
                hits+=1
                hit_details.append((e["ticker"], e["date"].strftime("%Y-%m-%d"), direction, off, float(z)))
                break
    # FP: any directional spike on non-event days
    n_trig=0; n_calm=0
    for _,r in agg.iterrows():
        if (r["ticker"], r["date"]) in event_set: continue
        call_z, put_z = r["call_z"], r["put_z"]
        spike = (pd.notna(call_z) and call_z>x) or (pd.notna(put_z) and put_z>x)
        if not spike: continue
        df_t=stock_returns.get(r["ticker"])
        if df_t is None: continue
        nxt=df_t["next_ret"].get(r["date"], None)
        if nxt is None or pd.isna(nxt): continue
        n_trig+=1
        if abs(nxt)<CALM_NEXTDAY: n_calm+=1
    return {"x":x, "events":len(events_df), "hits":hits, "hit_rate":hits/len(events_df),
            "n_trig":n_trig, "n_calm":n_calm, "fp_rate":n_calm/n_trig if n_trig else 0,
            "hit_details":hit_details}

print()
results=[]
for lname,lset in LOOKBACK_SETS.items():
    print(f"=== {lname} ===")
    for x in DIRECTIONAL_X:
        r=evaluate(x,lset); r["lookback_name"]=lname; results.append(r)
        ev_str=", ".join(f"{t}@{d}({dr},T-{o},z={z:.1f})" for t,d,dr,o,z in r["hit_details"])
        print(f"  vol(dir)>{x:>3.1f}x  hit {r['hits']}/{r['events']} ({r['hit_rate']*100:>4.1f}%)  "
              f"FP {r['n_calm']}/{r['n_trig']} ({r['fp_rate']*100:>3.0f}%)  | {ev_str}", flush=True)

TS=datetime.now().strftime("%Y%m%d_%H%M%S")
out=PROJECT_ROOT/"reports"/f"tianlongren_directional_scan_{TS}.md"
lines=[]
lines.append("# 天龙人检测器 — 方向性单边量扫描（维度 A）")
lines.append(f"\n生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
lines.append(f"股票池：{', '.join(STOCKS)} ({len(STOCKS)} 支)  事件数：{len(events_df)}")
lines.append("\n## 信号定义")
lines.append("- ↓ 事件：`put_volume[t] / put_ma30[t-1:t-31] > X`")
lines.append("- ↑ 事件：`call_volume[t] / call_ma30[t-1:t-31] > X`")
lines.append("- 假阳性：非事件日 call_z>X 或 put_z>X，且次日 |return|<4%")
lines.append("- **关键改进**：完全不用 P/C ratio，对 meme 股（NIO/TAL）更稳健")
for lname in LOOKBACK_SETS:
    lines.append(f"\n## 回看 {lname}")
    lines.append("| vol(dir)>X | hits | hit_rate | n_trig | FP_rate | 命中事件 |")
    lines.append("|----:|:--|:--|:--|:--|:--|")
    for r in results:
        if r["lookback_name"]!=lname: continue
        ev=", ".join(f"{t}@{d}({dr},T-{o})" for t,d,dr,o,_ in r["hit_details"]) or "—"
        lines.append(f"| {r['x']:.1f} | {r['hits']}/{r['events']} | **{r['hit_rate']*100:.1f}%** | {r['n_trig']} | {r['fp_rate']*100:.0f}% | {ev} |")

def score(r): return r["hit_rate"]-0.3*r["fp_rate"]
best=sorted(results,key=score,reverse=True)[:5]
lines.append("\n## Top 5（按 hit_rate − 0.3 × FP_rate 排序）")
lines.append("| Lookback | X | hit_rate | FP_rate | 命中事件 |")
lines.append("|----|--:|--:|--:|--|")
for r in best:
    ev=", ".join(f"{t}@{d}" for t,d,_,_,_ in r["hit_details"]) or "—"
    lines.append(f"| {r['lookback_name']} | {r['x']:.1f} | **{r['hit_rate']*100:.1f}%** | {r['fp_rate']*100:.0f}% | {ev} |")

out.write_text("\n".join(lines),encoding="utf-8")
print(f"\nReport: {out}")

"""Diagnose the 6 missed events: what did their pre-event option flow look like?"""
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

STOCKS = ["FUTU","NIO","PDD","BIDU","TAL"]
DATE_START="2025-05-23"; DATE_END="2026-05-22"; MARKET="QQQ"

def fetch_daily(t):
    aggs = list(client.list_aggs(t, 1, "day", from_=DATE_START, to=DATE_END, adjusted=True, sort="asc", limit=400))
    rows=[{"date":pd.Timestamp(a.timestamp,unit="ms",tz="UTC").strftime("%Y-%m-%d"),
           "open":a.open,"close":a.close} for a in aggs]
    df=pd.DataFrame(rows); df["date"]=pd.to_datetime(df["date"]); return df.set_index("date")

stock_daily={}
with ThreadPoolExecutor(max_workers=8) as ex:
    futs={ex.submit(fetch_daily,t):t for t in STOCKS+[MARKET]}
    for f in as_completed(futs): stock_daily[futs[f]]=f.result()

# events
qqq=stock_daily[MARKET]; qqq["q"]=qqq["close"].pct_change()
events=[]
for t in STOCKS:
    df=stock_daily[t]; c2c=df["close"].pct_change()
    for d,r in c2c.items():
        if pd.isna(r) or abs(r)<=0.12: continue
        qm=qqq["q"].get(d,0.0)
        if pd.notna(qm) and abs(qm)>=0.04: continue
        events.append({"ticker":t,"date":d,"event_return":float(r)})
events_df=pd.DataFrame(events).sort_values(["ticker","date"])

# load cache + build agg
all_rows=[]
for t in STOCKS:
    df=pd.read_csv(PROJECT_ROOT/"reports"/"tianlongren_cache"/f"{t}.csv")
    all_rows.append(df)
opt=pd.concat(all_rows,ignore_index=True); opt["date"]=pd.to_datetime(opt["date"])
agg=opt.groupby(["ticker","date","type"])["volume"].sum().unstack(fill_value=0)
if "call" not in agg.columns: agg["call"]=0
if "put"  not in agg.columns: agg["put"]=0
agg["total"]=agg["call"]+agg["put"]
agg["pc"]=agg["put"]/agg["call"].replace(0,np.nan)
agg=agg.reset_index().sort_values(["ticker","date"])
agg["vol_ma30"]=agg.groupby("ticker")["total"].transform(lambda s:s.shift(1).rolling(30,min_periods=10).mean())
agg["pc_mean30"]=agg.groupby("ticker")["pc"].transform(lambda s:s.shift(1).rolling(30,min_periods=10).mean())
agg["pc_std30"]=agg.groupby("ticker")["pc"].transform(lambda s:s.shift(1).rolling(30,min_periods=10).std())
agg["vol_z"]=agg["total"]/agg["vol_ma30"]
agg["pc_z"]=(agg["pc"]-agg["pc_mean30"])/agg["pc_std30"]

print("\n=== T-1..T-7 pre-event flow for each event ===\n")
for _,e in events_df.iterrows():
    d=pd.Timestamp(e["date"]); tk=e["ticker"]; rr=e["event_return"]
    direction="↓" if rr<0 else "↑"
    df_t=agg[agg["ticker"]==tk].set_index("date")
    avail=df_t.index[df_t.index<d].sort_values()
    print(f"\n{tk} {d.strftime('%Y-%m-%d')}  return={rr*100:+.1f}% ({direction})")
    print(f"  {'Day':<11} {'call':>7} {'put':>7} {'total':>7} {'P/C':>6} {'vol_z':>6} {'pc_z':>7}")
    for off in [1,2,3,4,5,6,7]:
        if off>len(avail): break
        td=avail[-off]
        row=df_t.loc[td]
        pc_str=f"{row['pc']:.2f}" if pd.notna(row['pc']) else "n/a"
        vz_str=f"{row['vol_z']:.2f}" if pd.notna(row['vol_z']) else "n/a"
        pz_str=f"{row['pc_z']:+.2f}" if pd.notna(row['pc_z']) else "n/a"
        print(f"  T-{off} {td.strftime('%Y-%m-%d')}  {int(row['call']):>7} {int(row['put']):>7} {int(row['total']):>7} {pc_str:>6} {vz_str:>6} {pz_str:>7}")

"""Standalone COIN options data fetcher (Dimension B control group).

Pulls all COIN options daily aggs over the past year using the same cache
mechanism as the main notebook. Safe to run overnight — writes per-stock
cache CSV that the main notebook will pick up automatically.
"""
import sys, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import pandas as pd
from omega_quant.config import Settings, load_env_file
from omega_quant.adapters.massive_client import build_rest_client

settings = Settings()
load_env_file(settings.tiger_env_path)
client = build_rest_client(settings)

UNDERLYING   = "COIN"
DATE_START   = "2025-05-23"
DATE_END     = "2026-05-22"
EXPIRY_LTE   = "2026-11-30"
STRIKE_LO    = 69.68    # from previous probe — 0.5 * COIN min low
STRIKE_HI    = 889.29   # 2.0 * COIN max high
MAX_WORKERS  = 10       # gentler on server for overnight run
CACHE_FILE   = PROJECT_ROOT / "reports" / "tianlongren_cache" / f"{UNDERLYING}.csv"

print(f"COIN fetch starting; cache: {CACHE_FILE}", flush=True)
CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

if CACHE_FILE.exists():
    print(f"Cache already exists ({CACHE_FILE.stat().st_size // 1024} KB) — exiting.", flush=True)
    sys.exit(0)


def list_contracts():
    contracts = []
    for kw in [
        dict(underlying_ticker=UNDERLYING, expired=True,
             expiration_date_gte=DATE_START, expiration_date_lte=EXPIRY_LTE,
             strike_price_gte=STRIKE_LO, strike_price_lte=STRIKE_HI),
        dict(underlying_ticker=UNDERLYING, expired=False,
             expiration_date_lte=EXPIRY_LTE,
             strike_price_gte=STRIKE_LO, strike_price_lte=STRIKE_HI),
    ]:
        for attempt in range(5):
            try:
                contracts += list(client.list_options_contracts(**kw))
                break
            except Exception as e:
                if attempt == 4:
                    print(f"  contract list FAILED: {e}", flush=True)
                    return contracts
                time.sleep(2 ** attempt)
    seen, out = set(), []
    for c in contracts:
        if c.ticker in seen: continue
        seen.add(c.ticker); out.append(c)
    return out


def fetch(c):
    for attempt in range(5):
        try:
            aggs = list(client.list_aggs(c.ticker, 1, "day", from_=DATE_START, to=DATE_END,
                                         adjusted=True, sort="asc", limit=400))
            return [(pd.Timestamp(a.timestamp, unit="ms", tz="UTC").strftime("%Y-%m-%d"),
                     c.contract_type, int(a.volume or 0)) for a in aggs]
        except Exception:
            if attempt == 4:
                return []
            time.sleep(0.5 * (2 ** attempt))
    return []


t0 = time.time()
contracts = list_contracts()
print(f"COIN: {len(contracts)} contracts to fetch", flush=True)

rows = []
done = 0
with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
    futs = [ex.submit(fetch, c) for c in contracts]
    for f in as_completed(futs):
        for d, ct, v in f.result():
            rows.append((d, UNDERLYING, ct, v))
        done += 1
        if done % 500 == 0:
            print(f"  COIN: {done}/{len(contracts)} ({time.time()-t0:.0f}s)", flush=True)
            # Incremental save every 500 contracts so we never lose >500 contracts of work
            pd.DataFrame(rows, columns=["date","ticker","type","volume"]).to_csv(CACHE_FILE, index=False)

pd.DataFrame(rows, columns=["date","ticker","type","volume"]).to_csv(CACHE_FILE, index=False)
print(f"COIN: DONE  {len(contracts)} contracts -> {len(rows)} rows  ({time.time()-t0:.1f}s)", flush=True)

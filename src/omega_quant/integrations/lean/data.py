from __future__ import annotations

import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd


def _lean_price(value: Any) -> int:
    return int(round(float(value) * 10000))


def _lean_volume(value: Any) -> int:
    return int(round(float(value)))


def _extract_symbol_ohlcv(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()

    if isinstance(raw.columns, pd.MultiIndex):
        candidates = []
        if symbol in raw.columns.get_level_values(0):
            candidates.append(raw[symbol])
        if symbol in raw.columns.get_level_values(-1):
            candidates.append(raw.xs(symbol, axis=1, level=-1))
        frame = next((candidate for candidate in candidates if not candidate.empty), pd.DataFrame())
    else:
        frame = raw

    if frame.empty:
        return pd.DataFrame()

    columns = {str(column).lower(): column for column in frame.columns}
    required = ["open", "high", "low", "close", "volume"]
    if not all(name in columns for name in required):
        return pd.DataFrame()

    ohlcv = pd.DataFrame(
        {
            "open": pd.to_numeric(frame[columns["open"]], errors="coerce"),
            "high": pd.to_numeric(frame[columns["high"]], errors="coerce"),
            "low": pd.to_numeric(frame[columns["low"]], errors="coerce"),
            "close": pd.to_numeric(frame[columns["close"]], errors="coerce"),
            "volume": pd.to_numeric(frame[columns["volume"]], errors="coerce"),
        }
    )
    return ohlcv.dropna(axis=0, how="any")


def _write_lean_daily_zip(symbol: str, frame: pd.DataFrame, data_root: Path) -> dict[str, Any]:
    daily_dir = data_root / "equity" / "usa" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    normalized_symbol = symbol.lower()
    zip_path = daily_dir / f"{normalized_symbol}.zip"
    csv_name = f"{normalized_symbol}.csv"
    lines = []

    for timestamp, row in frame.iterrows():
        day = pd.Timestamp(timestamp).date()
        lines.append(
            ",".join(
                [
                    f"{day:%Y%m%d} 00:00",
                    str(_lean_price(row["open"])),
                    str(_lean_price(row["high"])),
                    str(_lean_price(row["low"])),
                    str(_lean_price(row["close"])),
                    str(_lean_volume(row["volume"])),
                ]
            )
        )

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(csv_name, "\n".join(lines) + "\n")

    return {
        "symbol": symbol,
        "rows": len(lines),
        "path": str(zip_path.resolve()),
    }


def _existing_lean_daily_rows(symbol: str, data_root: Path) -> int:
    normalized_symbol = symbol.lower()
    zip_path = data_root / "equity" / "usa" / "daily" / f"{normalized_symbol}.zip"
    csv_name = f"{normalized_symbol}.csv"
    if not zip_path.exists():
        return 0
    try:
        with zipfile.ZipFile(zip_path) as archive:
            with archive.open(csv_name) as handle:
                return sum(1 for line in handle if line.strip())
    except (OSError, KeyError, zipfile.BadZipFile):
        return 0


def _existing_lean_last_date(symbol: str, data_root: Path) -> date | None:
    """Return the last trading date in the existing LEAN zip, or None if absent/unreadable."""
    normalized_symbol = symbol.lower()
    zip_path = data_root / "equity" / "usa" / "daily" / f"{normalized_symbol}.zip"
    csv_name = f"{normalized_symbol}.csv"
    if not zip_path.exists():
        return None
    try:
        with zipfile.ZipFile(zip_path) as archive:
            with archive.open(csv_name) as handle:
                last_line = b""
                for line in handle:
                    if line.strip():
                        last_line = line
                if not last_line:
                    return None
                date_str = last_line.decode("utf-8", errors="replace").split(" ")[0]
                return date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
    except (OSError, KeyError, zipfile.BadZipFile, ValueError, IndexError):
        return None


def _is_data_stale(symbol: str, data_root: Path, *, stale_days: int = 5) -> bool:
    """Return True if the existing LEAN data is missing or older than stale_days calendar days."""
    last = _existing_lean_last_date(symbol, data_root)
    if last is None:
        return True
    return last < date.today() - timedelta(days=stale_days)


def _fetch_with_massive(
    symbols: list[str],
    data_root: Path,
    start: str,
    end: str,
    settings: Any,
) -> dict[str, Any]:
    from .massive_client import fetch_ohlcv

    ohlcv_map = fetch_ohlcv(symbols, settings, start=start, end=end)

    rows: dict[str, int] = {}
    written = []
    missing = []
    for symbol in symbols:
        frame = ohlcv_map.get(symbol, pd.DataFrame())
        if frame.empty:
            rows[symbol] = 0
            missing.append(symbol)
            continue
        info = _write_lean_daily_zip(symbol, frame, data_root)
        rows[symbol] = int(info["rows"])
        written.append(info)

    return {
        "ok": not missing,
        "source": "massive",
        "start": start,
        "end": end,
        "rows": rows,
        "written": written,
        "missing": missing,
        "error": None if not missing else f"缺少 {len(missing)} 个标的的 Massive 日线数据。",
    }


def _fetch_with_yfinance(
    symbols: list[str],
    existing_rows: dict[str, int],
    data_root: Path,
    start: str,
    end: str,
) -> dict[str, Any]:
    try:
        import yfinance as yf

        raw = yf.download(
            tickers=" ".join(symbols),
            start=start,
            end=end,
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "source": "yfinance",
            "rows": existing_rows,
            "written": [],
            "missing": symbols,
            "error": str(exc),
        }

    rows: dict[str, int] = dict(existing_rows)
    written = []
    missing = []
    for symbol in symbols:
        frame = _extract_symbol_ohlcv(raw, symbol)
        if frame.empty:
            rows[symbol] = 0
            missing.append(symbol)
            continue
        info = _write_lean_daily_zip(symbol, frame, data_root)
        rows[symbol] = int(info["rows"])
        written.append(info)

    return {
        "ok": not missing,
        "source": "yfinance",
        "start": start,
        "end": end,
        "rows": rows,
        "written": written,
        "missing": missing,
        "error": None if not missing else f"缺少 {len(missing)} 个标的的 yfinance 日线数据。",
    }


def ensure_lean_daily_equity_data(
    symbols: list[str],
    data_root: Path | str,
    *,
    start: str = "2021-11-01",
    end: str | None = None,
    min_existing_rows: int = 30,
    settings: Any = None,
) -> dict[str, Any]:
    universe = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    if not universe:
        return {"ok": True, "source": "local", "rows": {}, "written": [], "missing": [], "error": None}

    data_root_path = Path(data_root)
    end_date = end or (date.today() + timedelta(days=1)).isoformat()

    existing_rows = {symbol: _existing_lean_daily_rows(symbol, data_root_path) for symbol in universe}

    # Use date-staleness check when possible, fall back to row-count for compatibility.
    if settings is not None:
        download_symbols = [s for s in universe if _is_data_stale(s, data_root_path)]
    else:
        download_symbols = [s for s in universe if existing_rows.get(s, 0) < min_existing_rows]

    if not download_symbols:
        return {
            "ok": True,
            "source": "local",
            "rows": existing_rows,
            "written": [],
            "missing": [],
            "error": None,
        }

    # Prefer Massive when settings + API key are available.
    if settings is not None and getattr(settings, "massive_api_key", None):
        result = _fetch_with_massive(download_symbols, data_root_path, start, end_date, settings)
        if result["ok"] or not result.get("missing"):
            result["rows"] = {**existing_rows, **result["rows"]}
            return result
        # Fall through to yfinance for any missing symbols.
        still_missing = result.get("missing") or download_symbols
        yf_result = _fetch_with_yfinance(still_missing, existing_rows, data_root_path, start, end_date)
        merged_rows = {**existing_rows, **result["rows"], **yf_result["rows"]}
        all_missing = yf_result.get("missing") or []
        return {
            "ok": not all_missing,
            "source": "massive+yfinance",
            "start": start,
            "end": end_date,
            "rows": merged_rows,
            "written": (result.get("written") or []) + (yf_result.get("written") or []),
            "missing": all_missing,
            "error": yf_result.get("error") if all_missing else None,
        }

    return _fetch_with_yfinance(download_symbols, existing_rows, data_root_path, start, end_date)

"""TigerOpen 历史 K 线下载适配器（美股 / 美股期权）。

通过官方 SDK 的 ``QuoteClient.get_bars`` 和 ``get_option_bars`` 拉取历史 Bar
数据，转换为 ``RecordedBar`` 持久化到本地 SQLite，供回测器复用。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..broker.tigeropen_client import build_quote_client


_INTERVAL_TO_PERIOD: dict[str, str] = {
    "1m": "1min",
    "3m": "3min",
    "5m": "5min",
    "10m": "10min",
    "15m": "15min",
    "30m": "30min",
    "45m": "45min",
    "60m": "60min",
    "1h": "60min",
    "2h": "2hour",
    "3h": "3hour",
    "4h": "4hour",
    "6h": "6hour",
    "1d": "day",
    "1w": "week",
    "1M": "month",
}


@dataclass
class DownloadResult:
    vt_symbol: str
    interval: str
    product: str
    rows_received: int
    bars_inserted: int
    bars_skipped: int
    start: datetime | None
    end: datetime | None
    source: str = "tigeropen_history"

    def asdict(self) -> dict[str, Any]:
        return {
            "vt_symbol": self.vt_symbol,
            "interval": self.interval,
            "product": self.product,
            "rows_received": self.rows_received,
            "bars_inserted": self.bars_inserted,
            "bars_skipped": self.bars_skipped,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "source": self.source,
        }


def _normalize_period(interval: str) -> str:
    key = (interval or "1m").strip().lower()
    return _INTERVAL_TO_PERIOD.get(key, _INTERVAL_TO_PERIOD.get(key.replace(" ", ""), "1min"))


def _split_vt(vt_symbol: str) -> tuple[str, str]:
    parts = vt_symbol.split(".", 1)
    if len(parts) == 2:
        return parts[0].strip().upper(), parts[1].strip().upper() or "SMART"
    return vt_symbol.strip().upper(), "SMART"


def _looks_like_underlying_only(symbol: str) -> bool:
    """Tiger 期权合约代码通常含空格 + 6位日期 + C/P + 8位行权价 (OCC), 例如 'AAPL  240119C00150000'。
    纯字母 (e.g. 'AAPL') 或带交易所后缀的 'AAPL.SMART' 形式都是 underlying。
    """

    cleaned = symbol.strip().upper()
    if not cleaned:
        return True
    # OCC 含空格 (root 与 expiry/strike 之间)
    if " " in cleaned:
        return False
    # 或者数字 + C/P 同时出现 (e.g. "AAPL240119C00150000")
    has_digit = any(c.isdigit() for c in cleaned)
    has_cp = ("C" in cleaned or "P" in cleaned) and any(c.isdigit() for c in cleaned)
    if has_digit and has_cp and len(cleaned) >= 12:
        return False
    return True


def _to_epoch_millis(value: datetime | None) -> int:
    if value is None:
        return -1
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def download_history_bars(
    vt_symbol: str,
    *,
    interval: str = "1m",
    start: datetime | None = None,
    end: datetime | None = None,
    product: str = "equity",
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], DownloadResult]:
    """从 TigerOpen 拉取历史 Bar。

    - 股票：使用 ``QuoteClient.get_bars_by_page``，自动分页直到 ``total`` 条或区间走完。
    - 期权：使用 ``QuoteClient.get_option_bars``，SDK 暂无分页版本。

    返回 ``(rows, summary)``。``rows`` 是可直接写入 ``RecordedBar`` 的 dict 列表，
    调用方（API 路由层）负责去重 + 持久化。
    """

    symbol, exchange = _split_vt(vt_symbol)
    period = _normalize_period(interval)
    client = build_quote_client()
    total_cap = int(limit) if limit and limit > 0 else 10_000
    page_size = min(1_000, total_cap)

    if product == "option":
        # 期权下载需要完整 OCC 合约代码 (如 "AAPL  240119C00150000"), 不是 underlying ticker。
        # 仅 underlying (例如 "AAPL" 或 "AAPL.SMART") 传给 Tiger SDK 会被识别为空, 报
        #   code=1010 msg=biz param error(symbols cannot be empty and cannot exceed:30)
        # 这里在客户端先拦截, 给出可操作的错误信息。
        if _looks_like_underlying_only(symbol):
            raise ValueError(
                f"期权下载需要完整 OCC 合约代码 (例如 'AAPL  240119C00150000'), 不能只填 '{symbol}'。"
                "可通过 '/api/option-master/chain' 查询期权链取得完整合约代码; "
                "另外 Tiger 账户需开通美股期权行情订阅 (usOptQuote) 才能获取数据。"
            )
        try:
            df = client.get_option_bars(
                identifiers=symbol,
                begin_time=_to_epoch_millis(start) if start else -1,
                end_time=_to_epoch_millis(end) if end else 4_070_880_000_000,
                period=period,
                limit=total_cap,
            )
        except Exception as exc:
            msg = str(exc)
            if "permission denied" in msg.lower() or "us opt" in msg.lower():
                raise ValueError(
                    "Tiger 账户没有美股期权行情权限 (usOptQuote/usStockOpenQuote)。请在 Tiger 客户端"
                    "开通对应订阅; 当前权限可通过 GET /api/market/quote-permissions 查看。"
                ) from exc
            raise
    else:
        df = client.get_bars_by_page(
            symbol=symbol,
            period=period,
            begin_time=_to_epoch_millis(start) if start else -1,
            end_time=_to_epoch_millis(end) if end else -1,
            total=total_cap,
            page_size=page_size,
        )

    rows: list[dict[str, Any]] = []
    if df is None or getattr(df, "empty", True):
        return rows, DownloadResult(
            vt_symbol=f"{symbol}.{exchange}",
            interval=interval,
            product=product,
            rows_received=0,
            bars_inserted=0,
            bars_skipped=0,
            start=start,
            end=end,
        )

    for record in df.to_dict("records"):
        ts_raw = record.get("time") or record.get("datetime") or record.get("bar_time")
        if ts_raw is None:
            continue
        if isinstance(ts_raw, (int, float)):
            ts = datetime.fromtimestamp(float(ts_raw) / 1000.0, tz=timezone.utc)
        else:
            try:
                ts = datetime.fromisoformat(str(ts_raw))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        rows.append(
            {
                "symbol": symbol,
                "exchange": exchange,
                "vt_symbol": f"{symbol}.{exchange}",
                "interval": interval,
                "timestamp": ts,
                "open_price": float(record.get("open", 0.0) or 0.0),
                "high_price": float(record.get("high", 0.0) or 0.0),
                "low_price": float(record.get("low", 0.0) or 0.0),
                "close_price": float(record.get("close", 0.0) or 0.0),
                "volume": float(record.get("volume", 0.0) or 0.0),
                "source": "tigeropen_history",
                "raw_json": record,
            }
        )

    rows.sort(key=lambda r: r["timestamp"])
    method = "get_option_bars" if product == "option" else "get_bars_by_page"
    summary = DownloadResult(
        vt_symbol=f"{symbol}.{exchange}",
        interval=interval,
        product=product,
        rows_received=len(df),
        bars_inserted=0,
        bars_skipped=0,
        start=rows[0]["timestamp"] if rows else start,
        end=rows[-1]["timestamp"] if rows else end,
        source=f"tigeropen_history:{method}",
    )
    return rows, summary

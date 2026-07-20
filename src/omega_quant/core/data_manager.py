"""vn.py-style DataManager engine for Omega historical data."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..models import RecordedBar, RecordedTick
from .engine import BaseEngine, MainEngine
from .event import EventEngine


@dataclass(frozen=True)
class DataOverview:
    vt_symbol: str
    data_type: str
    interval: str | None
    count: int
    start: datetime | None
    end: datetime | None
    sources: tuple[str, ...] = ()


class DataManagerEngine(BaseEngine):
    """History data query/export/delete core without vn.py's Qt UI."""

    engine_name = "data_manager"

    def __init__(
        self,
        main_engine: MainEngine,
        event_engine: EventEngine,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        super().__init__(main_engine, event_engine, self.engine_name)
        self.session_factory = session_factory

    def bind_session_factory(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def overview(self) -> list[DataOverview]:
        session = self._session()
        try:
            tick_rows = session.execute(
                select(
                    RecordedTick.vt_symbol,
                    func.count(RecordedTick.id),
                    func.min(RecordedTick.timestamp),
                    func.max(RecordedTick.timestamp),
                ).group_by(RecordedTick.vt_symbol)
            ).all()
            bar_rows = session.execute(
                select(
                    RecordedBar.vt_symbol,
                    RecordedBar.interval,
                    func.count(RecordedBar.id),
                    func.min(RecordedBar.timestamp),
                    func.max(RecordedBar.timestamp),
                ).group_by(RecordedBar.vt_symbol, RecordedBar.interval)
            ).all()
            tick_sources = _source_map(
                session.execute(select(RecordedTick.vt_symbol, RecordedTick.source).group_by(RecordedTick.vt_symbol, RecordedTick.source)).all()
            )
            bar_sources = _source_map(
                session.execute(select(RecordedBar.vt_symbol, RecordedBar.source, RecordedBar.interval).group_by(RecordedBar.vt_symbol, RecordedBar.source, RecordedBar.interval)).all(),
                interval_index=2,
            )
            result = [
                DataOverview(
                    vt_symbol=row[0],
                    data_type="tick",
                    interval=None,
                    count=int(row[1]),
                    start=row[2],
                    end=row[3],
                    sources=tuple(sorted(tick_sources.get((row[0], None), set()))),
                )
                for row in tick_rows
            ]
            result.extend(
                DataOverview(
                    vt_symbol=row[0],
                    data_type="bar",
                    interval=row[1],
                    count=int(row[2]),
                    start=row[3],
                    end=row[4],
                    sources=tuple(sorted(bar_sources.get((row[0], row[1]), set()))),
                )
                for row in bar_rows
            )
            return sorted(result, key=lambda item: (item.vt_symbol, item.data_type, item.interval or ""))
        finally:
            session.close()

    def load_ticks(self, vt_symbol: str, *, start: datetime | None = None, end: datetime | None = None, limit: int = 10_000) -> list[RecordedTick]:
        session = self._session()
        try:
            stmt = select(RecordedTick).where(RecordedTick.vt_symbol == vt_symbol.upper()).order_by(RecordedTick.timestamp)
            if start:
                stmt = stmt.where(RecordedTick.timestamp >= start)
            if end:
                stmt = stmt.where(RecordedTick.timestamp <= end)
            return list(session.scalars(stmt.limit(limit)))
        finally:
            session.close()

    def load_bars(
        self,
        vt_symbol: str,
        *,
        interval: str = "1m",
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 10_000,
    ) -> list[RecordedBar]:
        session = self._session()
        try:
            stmt = (
                select(RecordedBar)
                .where(RecordedBar.vt_symbol == vt_symbol.upper(), RecordedBar.interval == interval)
                .order_by(RecordedBar.timestamp)
            )
            if start:
                stmt = stmt.where(RecordedBar.timestamp >= start)
            if end:
                stmt = stmt.where(RecordedBar.timestamp <= end)
            return list(session.scalars(stmt.limit(limit)))
        finally:
            session.close()

    def export_bars_csv(self, vt_symbol: str, *, interval: str = "1m", start: datetime | None = None, end: datetime | None = None) -> str:
        bars = self.load_bars(vt_symbol, interval=interval, start=start, end=end)
        output = StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=["datetime", "symbol", "exchange", "interval", "open", "high", "low", "close", "volume"],
        )
        writer.writeheader()
        for bar in bars:
            writer.writerow(
                {
                    "datetime": bar.timestamp.isoformat(),
                    "symbol": bar.symbol,
                    "exchange": bar.exchange,
                    "interval": bar.interval,
                    "open": bar.open_price,
                    "high": bar.high_price,
                    "low": bar.low_price,
                    "close": bar.close_price,
                    "volume": bar.volume,
                }
            )
        return output.getvalue()

    def import_bars_csv(
        self,
        csv_text: str,
        *,
        vt_symbol: str | None = None,
        interval: str = "1m",
        source: str = "csv_import",
    ) -> dict[str, int | str]:
        rows = list(csv.DictReader(StringIO(csv_text.strip())))
        if not rows:
            return {"vt_symbol": (vt_symbol or "").upper(), "interval": interval, "source": source, "rows_read": 0, "bars_created": 0, "bars_skipped": 0}

        required = {"datetime", "open", "high", "low", "close"}
        missing = sorted(required - set(rows[0].keys()))
        if missing:
            raise ValueError(f"K线 CSV 缺少字段：{', '.join(missing)}")

        parsed: list[RecordedBar] = []
        for row in rows:
            symbol = str(row.get("symbol") or (vt_symbol or "").split(".", 1)[0]).strip().upper()
            exchange = str(row.get("exchange") or "SMART").strip().upper()
            row_interval = str(row.get("interval") or interval).strip() or interval
            row_vt_symbol = (vt_symbol or f"{symbol}.{exchange}").strip().upper()
            if "." in row_vt_symbol:
                symbol = row_vt_symbol.rsplit(".", 1)[0]
                exchange = row_vt_symbol.rsplit(".", 1)[1]
            parsed.append(
                RecordedBar(
                    symbol=symbol,
                    exchange=exchange,
                    vt_symbol=row_vt_symbol,
                    interval=row_interval,
                    timestamp=datetime.fromisoformat(str(row["datetime"]).strip()),
                    open_price=float(row["open"]),
                    high_price=float(row["high"]),
                    low_price=float(row["low"]),
                    close_price=float(row["close"]),
                    volume=float(row.get("volume") or 0.0),
                    gateway_name=str(row.get("gateway_name") or "OMEGA_IMPORT"),
                    source=source,
                    raw_json={"import_source": "csv"},
                )
            )

        target_vt_symbol = parsed[0].vt_symbol
        target_interval = parsed[0].interval
        session = self._session()
        try:
            existing = {
                _as_utc(timestamp)
                for timestamp in session.scalars(
                    select(RecordedBar.timestamp).where(
                        RecordedBar.vt_symbol == target_vt_symbol,
                        RecordedBar.interval == target_interval,
                    )
                )
            }
            created = 0
            skipped = 0
            seen: set[datetime] = set()
            for bar in parsed:
                if bar.vt_symbol != target_vt_symbol or bar.interval != target_interval:
                    raise ValueError("K线 CSV 一次只能导入一个 vt_symbol 和一个 interval。")
                timestamp_key = _as_utc(bar.timestamp)
                if timestamp_key in existing or timestamp_key in seen:
                    skipped += 1
                    continue
                seen.add(timestamp_key)
                session.add(bar)
                created += 1
            session.commit()
            return {
                "vt_symbol": target_vt_symbol,
                "interval": target_interval,
                "source": source,
                "rows_read": len(rows),
                "bars_created": created,
                "bars_skipped": skipped,
            }
        finally:
            session.close()

    def import_history_bars(
        self,
        rows: list[dict[str, Any]],
        *,
        source: str = "tigeropen_history",
    ) -> dict[str, int | str]:
        """批量写入 history bars，按 (vt_symbol, interval, timestamp) 去重。"""

        if not rows:
            return {"vt_symbol": "", "interval": "", "source": source, "rows_read": 0, "bars_created": 0, "bars_skipped": 0}

        target_vt_symbol = str(rows[0]["vt_symbol"]).upper()
        target_interval = str(rows[0]["interval"])
        session = self._session()
        try:
            existing = {
                _as_utc(timestamp)
                for timestamp in session.scalars(
                    select(RecordedBar.timestamp).where(
                        RecordedBar.vt_symbol == target_vt_symbol,
                        RecordedBar.interval == target_interval,
                    )
                )
            }
            created = 0
            skipped = 0
            seen: set[datetime] = set()
            for row in rows:
                vt = str(row["vt_symbol"]).upper()
                interval = str(row["interval"])
                if vt != target_vt_symbol or interval != target_interval:
                    raise ValueError("import_history_bars 一次只能写入一个 vt_symbol+interval 组合")
                timestamp_key = _as_utc(row["timestamp"])
                if timestamp_key in existing or timestamp_key in seen:
                    skipped += 1
                    continue
                seen.add(timestamp_key)
                session.add(
                    RecordedBar(
                        symbol=str(row.get("symbol") or vt.split(".", 1)[0]),
                        exchange=str(row.get("exchange") or "SMART"),
                        vt_symbol=vt,
                        interval=interval,
                        timestamp=row["timestamp"],
                        open_price=float(row["open_price"]),
                        high_price=float(row["high_price"]),
                        low_price=float(row["low_price"]),
                        close_price=float(row["close_price"]),
                        volume=float(row.get("volume") or 0.0),
                        gateway_name=str(row.get("gateway_name") or "TIGEROPEN"),
                        source=str(row.get("source") or source),
                        raw_json=row.get("raw_json") or {},
                    )
                )
                created += 1
            session.commit()
            return {
                "vt_symbol": target_vt_symbol,
                "interval": target_interval,
                "source": source,
                "rows_read": len(rows),
                "bars_created": created,
                "bars_skipped": skipped,
            }
        finally:
            session.close()

    def aggregate_ticks_to_bars(
        self,
        vt_symbol: str,
        *,
        interval: str = "1m",
        start: datetime | None = None,
        end: datetime | None = None,
        source: str = "tick_aggregation",
    ) -> dict[str, int | str]:
        """Aggregate recorded ticks into minute bars.

        This keeps the first practical Omega version deliberately narrow:
        one-minute bars, US symbols/options, and duplicate-safe inserts. It is
        enough to connect vn.py-style DataRecorder -> DataManager ->
        CtaBacktester without pulling in desktop/database abstractions.
        """

        if interval != "1m":
            raise ValueError("当前 tick 聚合仅支持 interval=1m")
        ticks = self.load_ticks(vt_symbol, start=start, end=end, limit=1_000_000)
        buckets: dict[datetime, list[RecordedTick]] = {}
        for tick in ticks:
            timestamp = _as_utc(tick.timestamp)
            bucket = timestamp.replace(second=0, microsecond=0)
            buckets.setdefault(bucket, []).append(tick)

        session = self._session()
        try:
            existing = {
                _as_utc(timestamp)
                for timestamp in session.scalars(
                    select(RecordedBar.timestamp).where(
                        RecordedBar.vt_symbol == vt_symbol.upper(),
                        RecordedBar.interval == interval,
                    )
                )
            }
            created = 0
            skipped = 0
            for bucket, rows in sorted(buckets.items()):
                if bucket in existing:
                    skipped += 1
                    continue
                rows = sorted(rows, key=lambda row: row.timestamp)
                prices = [float(row.last_price) for row in rows]
                first = rows[0]
                session.add(
                    RecordedBar(
                        symbol=first.symbol,
                        exchange=first.exchange,
                        vt_symbol=first.vt_symbol,
                        interval=interval,
                        timestamp=bucket,
                        open_price=prices[0],
                        high_price=max(prices),
                        low_price=min(prices),
                        close_price=prices[-1],
                        volume=sum(float(row.volume or 0.0) for row in rows),
                        gateway_name=first.gateway_name,
                        source=source,
                        raw_json={"source_tick_count": len(rows)},
                    )
                )
                created += 1
            session.commit()
            return {
                "vt_symbol": vt_symbol.upper(),
                "interval": interval,
                "source": source,
                "ticks_read": len(ticks),
                "bars_created": created,
                "bars_skipped": skipped,
            }
        finally:
            session.close()

    def delete_symbol_data(self, vt_symbol: str) -> dict[str, int]:
        session = self._session()
        try:
            ticks = list(session.scalars(select(RecordedTick).where(RecordedTick.vt_symbol == vt_symbol.upper())))
            bars = list(session.scalars(select(RecordedBar).where(RecordedBar.vt_symbol == vt_symbol.upper())))
            for row in ticks + bars:
                session.delete(row)
            session.commit()
            return {"ticks_deleted": len(ticks), "bars_deleted": len(bars)}
        finally:
            session.close()

    def _session(self) -> Session:
        if not self.session_factory:
            raise RuntimeError("DataManagerEngine session_factory is not bound")
        return self.session_factory()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo:
        return value.astimezone(timezone.utc)
    return value.replace(tzinfo=timezone.utc)


def _source_map(rows, *, interval_index: int | None = None) -> dict[tuple[str, str | None], set[str]]:
    result: dict[tuple[str, str | None], set[str]] = {}
    for row in rows:
        vt_symbol = str(row[0]).upper()
        source = str(row[1] or "unknown")
        interval = str(row[interval_index]) if interval_index is not None else None
        result.setdefault((vt_symbol, interval), set()).add(source)
    return result

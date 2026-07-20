from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def id_with(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Base(DeclarativeBase):
    pass


class MonitorGroup(Base):
    __tablename__ = "monitor_groups"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: id_with("mg"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    allocated_capital: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="stopped")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class CtaStrategyConfig(Base):
    __tablename__ = "cta_strategy_configs"

    strategy_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    class_name: Mapped[str] = mapped_column(String(120), nullable=False)
    vt_symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    setting_json: Mapped[dict] = mapped_column(JSON, default=dict)
    gateway_name: Mapped[str] = mapped_column(String(32), default="TIGEROPEN", nullable=False)
    product: Mapped[str] = mapped_column(String(16), default="equity", nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), default="SMART", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="stopped", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class RiskManagerSetting(Base):
    __tablename__ = "risk_manager_settings"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default="default")
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class RecordedTick(Base):
    __tablename__ = "recorded_ticks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: id_with("tick"))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(16), default="SMART", nullable=False)
    vt_symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_price: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    bid_price_1: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask_price_1: Mapped[float | None] = mapped_column(Float, nullable=True)
    gateway_name: Mapped[str] = mapped_column(String(32), default="OMEGA", nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="live_readonly", nullable=False)
    raw_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RecordedBar(Base):
    __tablename__ = "recorded_bars"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: id_with("bar"))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(16), default="SMART", nullable=False)
    vt_symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    interval: Mapped[str] = mapped_column(String(16), default="1m", nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    open_price: Mapped[float] = mapped_column(Float, nullable=False)
    high_price: Mapped[float] = mapped_column(Float, nullable=False)
    low_price: Mapped[float] = mapped_column(Float, nullable=False)
    close_price: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    gateway_name: Mapped[str] = mapped_column(String(32), default="OMEGA", nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="live_readonly", nullable=False)
    raw_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DataRecorderSetting(Base):
    __tablename__ = "data_recorder_settings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: id_with("recset"))
    vt_symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    data_type: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

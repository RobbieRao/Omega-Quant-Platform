"""Pruned vn.py trading constants for Omega.

Upstream source basis: ``vnpy/trader/constant.py``.

Omega keeps the enum names and core semantics that mature vn.py engines expect,
but removes domestic China exchanges, futures-led product categories, crypto,
and desktop-oriented labels. The target scope is US stocks, ETFs, and options
through TigerOpen/Massive gateways.
"""

from enum import Enum


class Direction(Enum):
    """Direction of order/trade/position."""

    LONG = "long"
    SHORT = "short"
    NET = "net"


class Offset(Enum):
    """Offset of order/trade."""

    NONE = ""
    OPEN = "open"
    CLOSE = "close"


class Status(Enum):
    """Order status."""

    SUBMITTING = "submitting"
    NOTTRADED = "not_traded"
    PARTTRADED = "partial_traded"
    ALLTRADED = "all_traded"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class Product(Enum):
    """Product class in Omega's US-market scope."""

    EQUITY = "equity"
    OPTION = "option"
    ETF = "etf"
    INDEX = "index"
    FUND = "fund"
    SPREAD = "spread"


class OrderType(Enum):
    """Order type."""

    LIMIT = "limit"
    MARKET = "market"
    STOP = "stop"             # 触发价 (stop-market): 触发后以市价成交
    STOP_LIMIT = "stop_limit"  # 触发价 + 限价: 触发后挂限价单
    FAK = "fak"
    FOK = "fok"


class OptionType(Enum):
    """Option type."""

    CALL = "call"
    PUT = "put"


class Exchange(Enum):
    """US/HK/global exchange identifiers kept for Omega."""

    SMART = "SMART"
    NYSE = "NYSE"
    NASDAQ = "NASDAQ"
    ARCA = "ARCA"
    EDGEA = "EDGEA"
    ISLAND = "ISLAND"
    BATS = "BATS"
    IEX = "IEX"
    AMEX = "AMEX"
    CBOE = "CBOE"
    CFE = "CFE"
    SEHK = "SEHK"
    OTC = "OTC"
    IBKRATS = "IBKRATS"
    LOCAL = "LOCAL"
    GLOBAL = "GLOBAL"


class Currency(Enum):
    """Currency."""

    USD = "USD"
    HKD = "HKD"
    CAD = "CAD"


class Interval(Enum):
    """Interval of bar data."""

    MINUTE = "1m"
    HOUR = "1h"
    DAILY = "d"
    WEEKLY = "w"
    TICK = "tick"


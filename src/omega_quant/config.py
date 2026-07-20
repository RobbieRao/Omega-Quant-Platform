from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path


DEFAULT_VAULT_ROOT = Path.home() / "Documents" / "OmegaQuant"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_SECRETS_PATH = PROJECT_ROOT / "config" / "omega.local.secrets.env"


@dataclass(frozen=True)
class Settings:
    tiger_env_path: Path = Path.home() / ".tigeropen" / "tigermcp.env"
    local_secrets_path: Path = LOCAL_SECRETS_PATH
    vault_root: Path = DEFAULT_VAULT_ROOT
    investment_folder: str = "research"
    account_mask_prefix: int = 2
    account_mask_suffix: int = 2
    max_single_position_weight: float = 0.40
    max_option_weight: float = 0.20
    short_dated_option_days: int = 21
    stream_enabled: bool = True
    stream_cache_path: Path = Path("reports/json/live_stream.json")
    stream_frontend_refresh_ms: int = 250
    stream_cache_flush_seconds: float = 0.25
    stream_sse_heartbeat_seconds: float = 1.0
    stream_stale_after_seconds: float = 3.0
    stream_hard_stale_after_seconds: float = 10.0
    market_data_enabled: bool = True
    market_data_provider: str = "massive"
    massive_host: str = "massive.com"
    massive_secure: bool = True
    market_data_include_options: bool = True
    massive_subscribe_quotes: bool = True
    massive_subscribe_trades: bool = False
    market_data_reconnect_seconds: float = 5.0
    market_data_pause_without_sse_seconds: float = 60.0
    option_greeks_enabled: bool = True
    option_greeks_min_interval_seconds: float = 0.5
    option_greeks_last_trade_max_age_seconds: float = 15.0
    option_greeks_last_mid_max_deviation_pct: float = 0.10
    option_greeks_last_mid_max_deviation_abs: float = 0.05
    option_risk_free_rate: float = 0.04
    snapshot_auto_refresh_seconds: int = 300
    max_stock_spread_pct: float = 0.025
    max_option_spread_pct: float = 0.15
    audit_log_path: Path = Path("reports/json/omega_audit.jsonl")
    audit_duckdb_path: Path = Path("reports/omega_audit.duckdb")

    @property
    def investment_dir(self) -> Path:
        return self.vault_root / self.investment_folder

    @property
    def review_dir(self) -> Path:
        return self.investment_dir / "交易复盘"

    @property
    def resolved_market_data_provider(self) -> str:
        return (self._value("OMEGA_MARKET_DATA_PROVIDER") or self.market_data_provider).strip().lower()

    @property
    def resolved_market_data_enabled(self) -> bool:
        raw = self._value("OMEGA_MARKET_DATA_ENABLED")
        if raw is None:
            return self.market_data_enabled
        return raw.strip().lower() not in {"0", "false", "no", "off"}

    @property
    def resolved_market_data_include_options(self) -> bool:
        raw = self._value("OMEGA_MARKET_DATA_OPTIONS")
        if raw is None:
            return self.market_data_include_options
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @property
    def resolved_massive_subscribe_quotes(self) -> bool:
        raw = self._value("OMEGA_MASSIVE_SUBSCRIBE_QUOTES")
        if raw is None:
            return self.massive_subscribe_quotes
        return raw.strip().lower() not in {"0", "false", "no", "off"}

    @property
    def resolved_massive_subscribe_trades(self) -> bool:
        raw = self._value("OMEGA_MASSIVE_SUBSCRIBE_TRADES")
        if raw is None:
            return self.massive_subscribe_trades
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @property
    def resolved_market_data_pause_without_sse_seconds(self) -> float:
        raw = self._value("OMEGA_MARKET_DATA_PAUSE_WITHOUT_SSE_SECONDS")
        if raw is None:
            return self.market_data_pause_without_sse_seconds
        try:
            return max(0.0, float(raw))
        except ValueError:
            return self.market_data_pause_without_sse_seconds

    @property
    def resolved_option_greeks_enabled(self) -> bool:
        raw = self._value("OMEGA_OPTION_GREEKS_ENABLED")
        if raw is None:
            return self.option_greeks_enabled
        return raw.strip().lower() not in {"0", "false", "no", "off"}

    @property
    def resolved_option_greeks_min_interval_seconds(self) -> float:
        raw = self._value("OMEGA_OPTION_GREEKS_MIN_INTERVAL_SECONDS")
        if raw is None:
            return self.option_greeks_min_interval_seconds
        try:
            return max(0.0, float(raw))
        except ValueError:
            return self.option_greeks_min_interval_seconds

    @property
    def resolved_massive_host(self) -> str:
        return (
            self._value("OMEGA_MASSIVE_HOST")
            or self.massive_host
        ).strip()

    @property
    def resolved_massive_secure(self) -> bool:
        host = self.resolved_massive_host.lower()
        if host.startswith(("http://", "ws://")):
            return False
        if host.startswith(("https://", "wss://")):
            return True
        raw = self._value("OMEGA_MASSIVE_SECURE")
        if raw is None:
            return self.massive_secure
        return raw.strip().lower() not in {"0", "false", "no", "off", "http", "ws", "insecure"}

    @staticmethod
    def _strip_scheme(host: str) -> str:
        for scheme in ("https://", "http://", "wss://", "ws://"):
            if host.lower().startswith(scheme):
                return host[len(scheme):]
        return host

    @property
    def resolved_massive_rest_host(self) -> str:
        override = self._value("OMEGA_MASSIVE_REST_HOST")
        if override:
            return override.strip()
        host = self.resolved_massive_host.rstrip("/")
        if host.startswith(("http://", "https://", "ws://", "wss://")):
            return host
        bare = self._strip_scheme(host)
        if bare and "." in bare and not bare.startswith(("api.", "socket.")):
            return f"api.{bare}"
        return host

    @property
    def resolved_massive_ws_host(self) -> str:
        override = self._value("OMEGA_MASSIVE_WS_HOST")
        if override:
            return self._strip_scheme(override.strip()).rstrip("/")
        host = self.resolved_massive_host.rstrip("/")
        bare = self._strip_scheme(host)
        if bare and "." in bare and not bare.startswith(("api.", "socket.")):
            return f"socket.{bare}"
        return bare

    @property
    def massive_rest_base_url(self) -> str:
        host = self.resolved_massive_rest_host.rstrip("/")
        if host.startswith(("http://", "https://")):
            return host
        if host.startswith("ws://"):
            return f"http://{host[5:]}"
        if host.startswith("wss://"):
            return f"https://{host[6:]}"
        scheme = "https" if self.resolved_massive_secure else "http"
        return f"{scheme}://{host}"

    @property
    def massive_ws_host(self) -> str:
        return self.resolved_massive_ws_host

    @property
    def massive_api_key(self) -> str:
        return self._value("MASSIVE_API_KEY") or ""

    def load_local_secrets(self) -> dict[str, str]:
        if os.environ.get("OMEGA_DISABLE_LOCAL_SECRETS", "").strip().lower() in {"1", "true", "yes", "on"}:
            return {}
        if not self.local_secrets_path.exists():
            return {}
        values: dict[str, str] = {}
        for line in self.local_secrets_path.read_text(encoding="utf-8").splitlines():
            parsed = parse_env_line(line)
            if parsed is not None:
                values[parsed[0]] = parsed[1]
        return values

    def _value(self, *keys: str) -> str | None:
        for key in keys:
            value = os.environ.get(key)
            if value is not None:
                return value
        local = self.load_local_secrets()
        for key in keys:
            value = local.get(key)
            if value is not None:
                return value
        return None


def parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, raw_value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    try:
        parsed = shlex.split(f"x={raw_value}", posix=True)
    except ValueError:
        value = raw_value.strip().strip("'").strip('"')
    else:
        if not parsed or "=" not in parsed[0]:
            value = raw_value.strip()
        else:
            value = parsed[0].split("=", 1)[1]
    return key, value


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"Tiger env file not found: {path}")

    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        parsed = parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        values[key] = value
        os.environ[key] = value
    return values


def load_env_file_if_exists(path: Path, *, override: bool = False) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        parsed = parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        values[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return values


def mask_account(account: object, prefix: int = 2, suffix: int = 2) -> str:
    text = str(account or "")
    if not text:
        return ""
    if len(text) <= prefix + suffix:
        return "*" * len(text)
    return text[:prefix] + "*" * (len(text) - prefix - suffix) + text[-suffix:]

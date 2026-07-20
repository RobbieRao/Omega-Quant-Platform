"""Logger shim for Omega's pruned vn.py core."""

from __future__ import annotations

import logging


DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR
CRITICAL = logging.CRITICAL


class _BoundLogger:
    def __init__(self, gateway_name: str = "Logger") -> None:
        self.gateway_name = gateway_name
        self._logger = logging.getLogger(f"omega.vnpy_core.{gateway_name}")

    def bind(self, gateway_name: str = "Logger", **_: object) -> "_BoundLogger":
        return _BoundLogger(gateway_name)

    def log(self, level: int | str, msg: str) -> None:
        self._logger.log(_level(level), msg)

    def info(self, msg: str) -> None:
        self.log(INFO, msg)

    def warning(self, msg: str) -> None:
        self.log(WARNING, msg)

    def error(self, msg: str) -> None:
        self.log(ERROR, msg)


def _level(level: int | str) -> int:
    if isinstance(level, int):
        return level
    return getattr(logging, level.upper(), INFO)


logger = _BoundLogger()


__all__ = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "logger"]


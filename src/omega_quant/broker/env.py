"""TigerOpen credential environment-file loading for Omega."""

from __future__ import annotations

import os
import shlex
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCAL_SECRETS_PATH = PROJECT_ROOT / "config" / "omega.local.secrets.env"
DEFAULT_TIGER_ENV_PATH = Path.home() / ".tigeropen" / "tigermcp.env"


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


def _load_env_file(
    resolved: Path,
    *,
    override: bool,
    required: bool,
) -> dict[str, str]:
    if not resolved.exists():
        if required:
            raise FileNotFoundError(f"Tiger env file not found: {resolved}")
        return {}

    values: dict[str, str] = {}
    for line in resolved.read_text(encoding="utf-8").splitlines():
        parsed = parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        values[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return values


def load_tiger_env(
    path: Path | str | None = None,
    *,
    override: bool = True,
) -> dict[str, str]:
    """读取 Tiger 凭证 env 文件并把键值塞到 ``os.environ``。

    默认先加载仓库内被 gitignore 的 ``config/omega.local.secrets.env``，再加载
    用户级 ``~/.tigeropen/tigermcp.env``。若显式传入 path 且文件
    不存在，抛出 ``FileNotFoundError`` ——调用方负责给出明确错误信息。
    """

    if os.environ.get("OMEGA_DISABLE_LOCAL_SECRETS", "").strip().lower() in {"1", "true", "yes", "on"}:
        return {}
    resolved = Path(path) if path else DEFAULT_TIGER_ENV_PATH
    values: dict[str, str] = {}
    values.update(_load_env_file(resolved, override=override, required=path is not None))
    values.update(_load_env_file(LOCAL_SECRETS_PATH, override=override, required=False))
    return values

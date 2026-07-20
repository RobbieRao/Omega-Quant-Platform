from __future__ import annotations

from importlib.util import find_spec


def optional_imports_status() -> dict[str, bool]:
    checks: dict[str, bool] = {}
    for module_name in ("vectorbt", "fast_vollib", "pypfopt", "riskfolio"):
        checks[module_name] = find_spec(module_name) is not None
    return checks

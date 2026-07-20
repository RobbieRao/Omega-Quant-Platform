from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


DEFAULT_LEAN_SCENE_DIR = Path("/private/tmp/omega_lean_sidecar")


def _lean_identifier(value: str) -> str:
    text = re.sub(r"[^0-9a-zA-Z_]+", "_", str(value).strip())
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return "OmegaStrategy"
    if text[0].isdigit():
        text = f"_{text}"
    return text


def normalize_target_weights(target_weights: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in target_weights or []:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        try:
            weight = float(row.get("weight"))
        except (TypeError, ValueError):
            continue
        normalized.append(
            {
                "symbol": symbol,
                "weight": round(weight, 6),
                "label": str(row.get("label") or symbol),
            }
        )
    return normalized


def build_lean_strategy_spec(
    *,
    name: str = "Omega Sidecar",
    universe: list[str] | None = None,
    target_weights: list[dict[str, Any]] | None = None,
    max_single_position_weight: float = 0.4,
    max_option_weight: float = 0.2,
    max_daily_loss: float = 0.03,
    rebalance_cadence: str = "WeekEnd",
) -> dict[str, Any]:
    return {
        "name": name,
        "universe": sorted({str(symbol).strip().upper() for symbol in (universe or []) if str(symbol).strip()}),
        "target_weights": normalize_target_weights(target_weights),
        "risk": {
            "max_single_position_weight": float(max_single_position_weight),
            "max_option_weight": float(max_option_weight),
            "max_daily_loss": float(max_daily_loss),
        },
        "rebalance_cadence": rebalance_cadence,
    }


def build_lean_strategy_spec_from_assessment(
    assessment: dict[str, Any],
    *,
    name: str = "Omega Sidecar",
    max_single_position_weight: float = 0.4,
    max_option_weight: float = 0.2,
    max_daily_loss: float = 0.03,
) -> dict[str, Any]:
    positions = assessment.get("positions") or []
    target_weights = []
    universe = []
    for row in positions:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        universe.append(symbol)
        weight = row.get("weight")
        try:
            weight_value = float(weight)
        except (TypeError, ValueError):
            continue
        target_weights.append(
            {
                "symbol": symbol,
                "label": str(row.get("display") or symbol),
                "weight": weight_value,
            }
        )
    return build_lean_strategy_spec(
        name=name,
        universe=universe,
        target_weights=target_weights,
        max_single_position_weight=max_single_position_weight,
        max_option_weight=max_option_weight,
        max_daily_loss=max_daily_loss,
        rebalance_cadence="WeekEnd",
    )


def build_lean_algorithm_source(spec: dict[str, Any]) -> str:
    name = _lean_identifier(str(spec.get("name") or "Omega Strategy"))
    universe = [str(symbol).strip().upper() for symbol in spec.get("universe") or [] if str(symbol).strip()]
    target_weights = normalize_target_weights(spec.get("target_weights"))
    risk = spec.get("risk") or {}
    max_single_position_weight = float(risk.get("max_single_position_weight") or 0.4)
    max_option_weight = float(risk.get("max_option_weight") or 0.2)
    max_daily_loss = float(risk.get("max_daily_loss") or 0.03)
    rebalance_cadence = str(spec.get("rebalance_cadence") or "WeekEnd")

    universe_lines = "\n".join(
        f'        self.{symbol} = self.AddEquity("{symbol}", Resolution.Daily).Symbol'
        for symbol in universe
    )
    target_lines = "\n".join(
        f'        self.SetHoldings(self.{row["symbol"]}, {row["weight"]})'
        for row in target_weights
    )
    target_comment_lines = "\n".join(
        f'        # {row["label"]}: target {row["weight"]:.4f}' for row in target_weights
    )

    return f'''from AlgorithmImports import *


class {name}(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(2022, 1, 1)
        self.SetCash(100000)
        self.SetWarmUp(30, Resolution.Daily)
        self.max_single_position_weight = {max_single_position_weight}
        self.max_option_weight = {max_option_weight}
        self.max_daily_loss = {max_daily_loss}
        self.rebalance_cadence = "{rebalance_cadence}"
        self._last_rebalance_date = None
{universe_lines if universe_lines else "        # Universe will be populated from the generated spec."}

    def OnData(self, data):
        if self.IsWarmingUp:
            return
        if self.Time.date() == self._last_rebalance_date:
            return
        self._last_rebalance_date = self.Time.date()
{target_comment_lines if target_comment_lines else "        # No target weights were provided."}
{target_lines if target_lines else "        return"}
'''


def export_lean_sidecar(spec: dict[str, Any], output_dir: Path | str = DEFAULT_LEAN_SCENE_DIR) -> dict[str, Any]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    source = build_lean_algorithm_source(spec)
    algorithm_path = destination / "Algorithm.py"
    spec_path = destination / "strategy.json"
    algorithm_path.write_text(source, encoding="utf-8")
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "output_dir": str(destination.resolve()),
        "algorithm_path": str(algorithm_path.resolve()),
        "spec_path": str(spec_path.resolve()),
        "universe": spec.get("universe") or [],
        "target_weight_count": len(spec.get("target_weights") or []),
    }

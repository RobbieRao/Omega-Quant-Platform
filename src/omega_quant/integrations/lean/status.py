from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from .export import DEFAULT_LEAN_SCENE_DIR


DEFAULT_LEAN_ROOT = Path("references/upstream/Lean")


MODEL_GROUPS = {
    "Alpha": "Algorithm.Framework/Alphas",
    "Portfolio": "Algorithm.Framework/Portfolio",
    "Risk": "Algorithm.Framework/Risk",
    "Execution": "Algorithm.Framework/Execution",
    "Selection": "Algorithm.Framework/Selection",
}


def _short_commit(root: Path) -> str | None:
    if not (root / ".git").exists():
        return None
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _model_files(root: Path, relative_dir: str) -> list[str]:
    path = root / relative_dir
    if not path.exists():
        return []
    names = []
    for file_path in sorted(path.iterdir()):
        if file_path.suffix.lower() in {".py", ".cs"} and not file_path.name.startswith("__"):
            names.append(file_path.stem)
    return names


def discover_lean_models(root: Path = DEFAULT_LEAN_ROOT) -> dict[str, list[str]]:
    return {group: _model_files(root, relative_dir) for group, relative_dir in MODEL_GROUPS.items()}


def discover_brokerage_models(root: Path = DEFAULT_LEAN_ROOT) -> list[str]:
    path = root / "Common/Brokerages"
    if not path.exists():
        return []
    return sorted(
        file_path.stem
        for file_path in path.glob("*BrokerageModel.cs")
        if file_path.name != "IBrokerageModel.cs"
    )


def lean_engine_status(root: Path = DEFAULT_LEAN_ROOT) -> dict[str, Any]:
    models = discover_lean_models(root)
    brokerages = discover_brokerage_models(root)
    return {
        "root": str(root),
        "available": root.exists(),
        "commit": _short_commit(root),
        "dotnet": shutil.which("dotnet"),
        "lean_cli": shutil.which("lean"),
        "models": models,
        "modelCounts": {name: len(items) for name, items in models.items()},
        "brokerages": brokerages,
        "hasTigerBrokerage": any("Tiger" in name for name in brokerages),
        "sidecarExporterAvailable": True,
        "sidecarRunnerAvailable": True,
        "sidecarRuntimeReady": bool(shutil.which("dotnet")),
        "sidecarExportRoot": str(DEFAULT_LEAN_SCENE_DIR),
        "recommendedRole": "sidecar_backtest_and_architecture_reference",
    }

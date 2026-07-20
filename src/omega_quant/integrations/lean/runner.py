from __future__ import annotations

import json
import os
import shutil
import subprocess
import sysconfig
import uuid
from pathlib import Path
from typing import Any

from .data import ensure_lean_daily_equity_data
from .export import DEFAULT_LEAN_SCENE_DIR, _lean_identifier, export_lean_sidecar
from .status import DEFAULT_LEAN_ROOT


DEFAULT_LEAN_RESULTS_DIR = DEFAULT_LEAN_SCENE_DIR / "results"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_lines(path: Path, *, limit: int = 20) -> list[str]:
    try:
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()][:limit]
    except OSError:
        return []


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _is_pythonnet_shutdown_warning(stderr: str | None) -> bool:
    text = stderr or ""
    return (
        "GIL must always be released" in text
        and "Python.Runtime.Py.GILState.Finalize" in text
    )


def _pythonnet_environment(*, python_path: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if python_path is not None:
        existing_python_path = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"{python_path}{os.pathsep}{existing_python_path}" if existing_python_path else str(python_path)
        )
    if not env.get("PYTHONNET_PYDLL"):
        brew_python_home = Path("/opt/homebrew/opt/python@3.11/Frameworks/Python.framework/Versions/3.11")
        brew_python_dll = brew_python_home / "lib/libpython3.11.dylib"
        if brew_python_dll.exists():
            env["PYTHONNET_PYDLL"] = str(brew_python_dll)
            env.setdefault("PYTHONHOME", str(brew_python_home))
            return env
        libdir = Path(str(sysconfig.get_config_var("LIBDIR") or ""))
        library = str(sysconfig.get_config_var("LDLIBRARY") or "")
        candidates = [
            libdir / library if library else None,
            *libdir.glob("libpython*.dylib"),
        ]
        python_dll = next((path for path in candidates if path and path.exists()), None)
        if python_dll:
            env["PYTHONNET_PYDLL"] = str(python_dll)
    return env


def build_lean_runtime_config(
    spec: dict[str, Any],
    *,
    output_dir: Path | str = DEFAULT_LEAN_SCENE_DIR,
    lean_root: Path | str = DEFAULT_LEAN_ROOT,
    algorithm_id: str | None = None,
    settings: Any = None,
) -> dict[str, Any]:
    destination = Path(output_dir)
    lean_root_path = Path(lean_root)
    exported = export_lean_sidecar(spec, destination)
    run_id = algorithm_id or f"omega-{uuid.uuid4().hex[:12]}"
    results_dir = destination / "results" / run_id
    results_dir.mkdir(parents=True, exist_ok=True)
    object_store_root = destination / "storage"
    object_store_root.mkdir(parents=True, exist_ok=True)
    data_dir = lean_root_path / "Data"
    data_prep = ensure_lean_daily_equity_data(list(exported.get("universe") or []), data_dir, settings=settings)
    config_path = destination / "lean.config.json"
    algorithm_name = _lean_identifier(str(spec.get("name") or "Omega Sidecar"))
    config = {
        "environment": "backtesting",
        "algorithm-language": "Python",
        "algorithm-location": exported["algorithm_path"],
        "algorithm-type-name": algorithm_name,
        "algorithm-id": run_id,
        "backtest-name": str(spec.get("name") or "Omega Sidecar"),
        "data-folder": str(data_dir.resolve()),
        "results-destination-folder": str(results_dir.resolve()),
        "debugging": False,
        "live-mode": False,
        "messaging-handler": "QuantConnect.Messaging.Messaging",
        "job-queue-handler": "QuantConnect.Queues.JobQueue",
        "api-handler": "QuantConnect.Api.Api",
        "map-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskMapFileProvider",
        "factor-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskFactorFileProvider",
        "data-provider": "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider",
        "object-store": "QuantConnect.Lean.Engine.Storage.LocalObjectStore",
        "object-store-root": str(object_store_root.resolve()),
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "algorithm_id": run_id,
        "config_path": str(config_path.resolve()),
        "config": config,
        "data_prep": data_prep,
        "exported": exported,
        "results_dir": str(results_dir.resolve()),
        "lean_root": str(lean_root_path.resolve()),
    }


def parse_lean_backtest_summary(
    results_dir: Path | str,
    *,
    algorithm_id: str | None = None,
) -> dict[str, Any]:
    root = Path(results_dir)
    candidates: list[Path] = []
    if algorithm_id:
        candidates.extend([root / f"{algorithm_id}-summary.json", root / f"{algorithm_id}.json"])
    candidates.extend(sorted(root.glob("*-summary.json")))
    candidates.extend(sorted(root.glob("*.json")))

    source_path = next((path for path in candidates if path.exists()), None)
    if source_path is None:
        return {"available": False, "source_path": None, "metrics": {}, "statistics": {}, "runtime": {}}

    payload = _read_json(source_path)
    statistics = _first_present(payload, "Statistics", "statistics") or {}
    runtime = _first_present(payload, "RuntimeStatistics", "runtimeStatistics") or {}
    total_performance = _first_present(payload, "TotalPerformance", "totalPerformance") or {}
    portfolio_stats = _first_present(total_performance, "PortfolioStatistics", "portfolioStatistics") or {}
    trade_stats = _first_present(payload, "TradeStatistics", "tradeStatistics") or {}
    charts = _first_present(payload, "Charts", "charts") or {}
    data_monitor_path = next(iter(sorted(root.glob("data-monitor-report-*.json"))), None)
    failed_requests_path = next(iter(sorted(root.glob("failed-data-requests-*.txt"))), None)
    succeeded_requests_path = next(iter(sorted(root.glob("succeeded-data-requests-*.txt"))), None)
    data_monitor = _read_json(data_monitor_path) if data_monitor_path else {}
    metric_sources = [statistics, portfolio_stats, trade_stats, runtime]
    metric_names = [
        "Total Net Profit",
        "Sharpe Ratio",
        "Drawdown",
        "Total Trades",
        "Average Win",
        "Average Loss",
        "Win Rate",
        "Compounding Annual Return",
        "Probabilistic Sharpe Ratio",
    ]
    metrics: dict[str, Any] = {}
    for name in metric_names:
        for source in metric_sources:
            value = _first_present(source, name, name.replace(" ", ""))
            if value is not None:
                metrics[name] = value
                break
    chart_points = 0
    if isinstance(charts, dict):
        for chart in charts.values():
            if not isinstance(chart, dict):
                continue
            series = chart.get("series") or chart.get("Series") or {}
            if not isinstance(series, dict):
                continue
            for item in series.values():
                if isinstance(item, dict):
                    values = item.get("values") or item.get("Values") or []
                    if isinstance(values, list):
                        chart_points += len(values)

    return {
        "available": True,
        "source_path": str(source_path.resolve()),
        "state": _first_present(payload, "State", "state"),
        "metrics": metrics,
        "statistics": statistics,
        "runtime": runtime,
        "portfolioStatistics": portfolio_stats,
        "tradeStatistics": trade_stats,
        "chartCount": len(charts) if isinstance(charts, dict) else 0,
        "chartPointCount": chart_points,
        "dataMonitor": data_monitor,
        "failedDataRequests": _read_lines(failed_requests_path) if failed_requests_path else [],
        "succeededDataRequests": _read_lines(succeeded_requests_path) if succeeded_requests_path else [],
    }


def run_lean_sidecar_backtest(
    spec: dict[str, Any],
    *,
    output_dir: Path | str = DEFAULT_LEAN_SCENE_DIR,
    lean_root: Path | str = DEFAULT_LEAN_ROOT,
    timeout_seconds: int = 120,
    settings: Any = None,
) -> dict[str, Any]:
    runtime = build_lean_runtime_config(spec, output_dir=output_dir, lean_root=lean_root, settings=settings)
    lean_root_path = Path(runtime["lean_root"])
    project_path = lean_root_path / "Launcher" / "QuantConnect.Lean.Launcher.csproj"
    log_dir = Path(runtime["results_dir"])
    stdout_path = log_dir / "lean.stdout.log"
    stderr_path = log_dir / "lean.stderr.log"
    dotnet = shutil.which("dotnet")

    if not dotnet:
        return {
            "ok": False,
            "status": "missing_runtime",
            "message": "LEAN 侧车已导出，但本机缺少 dotnet，无法启动本地 LEAN Launcher 回测。",
            "runtime": runtime,
            "result": parse_lean_backtest_summary(runtime["results_dir"], algorithm_id=runtime["algorithm_id"]),
        }
    if not project_path.exists():
        return {
            "ok": False,
            "status": "missing_launcher",
            "message": f"LEAN Launcher 项目不存在：{project_path}",
            "runtime": runtime,
            "result": parse_lean_backtest_summary(runtime["results_dir"], algorithm_id=runtime["algorithm_id"]),
        }

    release_dir = project_path.parent / "bin" / "Release"
    launcher_dll = release_dir / "QuantConnect.Lean.Launcher.dll"
    command = [
        dotnet,
        "run",
        *(["--no-build"] if launcher_dll.exists() else []),
        "--project",
        str(project_path),
        "--configuration",
        "Release",
        "--",
        "--config",
        runtime["config_path"],
    ]
    completed: subprocess.CompletedProcess[str] | None = None
    try:
        completed = subprocess.run(
            command,
            cwd=str(lean_root_path),
            env=_pythonnet_environment(python_path=release_dir if release_dir.exists() else None),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_path.write_text(exc.stdout or "", encoding="utf-8")
        stderr_path.write_text(exc.stderr or "", encoding="utf-8")
        return {
            "ok": False,
            "status": "timeout",
            "message": f"LEAN 回测超过 {timeout_seconds} 秒未完成。",
            "runtime": runtime,
            "stdout_path": str(stdout_path.resolve()),
            "stderr_path": str(stderr_path.resolve()),
            "result": parse_lean_backtest_summary(runtime["results_dir"], algorithm_id=runtime["algorithm_id"]),
        }

    stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")
    result = parse_lean_backtest_summary(runtime["results_dir"], algorithm_id=runtime["algorithm_id"])
    state = result.get("state") if isinstance(result, dict) else None
    lean_status = state.get("Status") if isinstance(state, dict) else state
    lean_runtime_error = state.get("RuntimeError") if isinstance(state, dict) else None
    lean_completed = lean_status == "Completed" and not lean_runtime_error
    shutdown_warning = _is_pythonnet_shutdown_warning(completed.stderr)
    ok = bool(result.get("available")) and (completed.returncode == 0 or lean_completed)
    status = (
        "completed"
        if ok and (completed.returncode == 0 or shutdown_warning)
        else "completed_with_warning"
        if ok
        else "failed"
    )
    message = (
        "LEAN 回测已完成。"
        if status == "completed"
        else f"LEAN 回测已完成，但 Launcher 退出码 {completed.returncode}，请查看日志。"
        if status == "completed_with_warning"
        else f"LEAN Launcher 退出码 {completed.returncode}，请查看日志。"
    )
    return {
        "ok": ok,
        "status": status,
        "message": message,
        "runtime": runtime,
        "returncode": completed.returncode,
        "shutdownWarning": shutdown_warning,
        "stdout_path": str(stdout_path.resolve()),
        "stderr_path": str(stderr_path.resolve()),
        "result": result,
    }

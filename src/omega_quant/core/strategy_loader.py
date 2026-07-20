"""Filesystem-backed CTA strategy file loader (vn.py-style)。

用户可以在 ``strategies/`` 目录下放置 Python 文件，每个文件定义一个或多个
``CtaStrategyTemplate`` 子类。本模块负责扫描、读取、写入、删除并热加载这些
策略文件到 ``CTA_STRATEGY_REGISTRY``，供 CtaStrategyEngine 和 CtaBacktester 复用。
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from .strategy_template import (
    CTA_STRATEGY_REGISTRY,
    CtaStrategyTemplate,
)


_DEFAULT_STRATEGIES_DIRNAME = "user_strategies"
_LOADED_FILE_CLASSES: dict[str, list[str]] = {}

# 内置策略名 → 它们所在的源码文件 (用于 inspect.getsource 之外的备份方案)。
# 真正读源码用 inspect.getsource(class)，能精确拿到单个类的代码片段。
_BUILTIN_SOURCE_FILES: dict[str, Path] = {}


def get_strategies_dir() -> Path:
    """Resolve the user strategies directory.

    Honor ``OMEGA_STRATEGIES_DIR`` if set; otherwise use ``<repo>/data/omega_quant/user_strategies``.
    Always returns an existing absolute path (created on first call).
    """

    override = os.environ.get("OMEGA_STRATEGIES_DIR")
    if override:
        path = Path(override).expanduser()
    else:
        # repo root = parents[3] of this file (core/strategy_loader.py -> omega_quant -> src -> repo)
        repo_root = Path(__file__).resolve().parents[3]
        path = repo_root / "data" / "omega_quant" / _DEFAULT_STRATEGIES_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class StrategyFileInfo:
    name: str
    filename: str
    bytes_size: int
    modified_at: float
    classes: list[str]
    error: str | None = None
    # 分类元数据 — 从文件里首个匹配的策略类的 category/tags class attr 中提取
    category: str = ""
    tags: list[str] = None  # type: ignore[assignment]
    readonly: bool = False
    source_type: str = "user"  # "user" or "builtin"

    def __post_init__(self) -> None:
        if self.tags is None:
            self.tags = []

    def asdict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "filename": self.filename,
            "bytes": self.bytes_size,
            "modified_at": self.modified_at,
            "classes": list(self.classes),
            "error": self.error,
            "category": self.category,
            "tags": list(self.tags),
            "readonly": self.readonly,
            "source_type": self.source_type,
        }


def _aggregate_metadata_from_classes(class_names: list[str]) -> tuple[str, list[str]]:
    """从一组已注册的策略类里聚合 category/tags。

    category: 取首个非空的类。
    tags: 所有类的 tags 求并集 (按顺序去重)。
    """

    category = ""
    tags: list[str] = []
    seen: set[str] = set()
    for cname in class_names:
        cls = CTA_STRATEGY_REGISTRY.get(cname)
        if cls is None:
            continue
        if not category:
            cls_cat = (getattr(cls, "category", "") or "").strip()
            if cls_cat:
                category = cls_cat
        for tag in getattr(cls, "tags", []) or []:
            tag = str(tag).strip()
            if tag and tag not in seen:
                seen.add(tag)
                tags.append(tag)
    return category, tags


def _validate_filename(name: str) -> str:
    stem = name.removesuffix(".py").strip()
    if not stem:
        raise ValueError("策略文件名不能为空")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", stem):
        raise ValueError(f"非法策略文件名: {name}（仅允许字母数字下划线连字符，不能以连字符开头）")
    return f"{stem}.py"


def _file_path(name: str) -> Path:
    filename = _validate_filename(name)
    return get_strategies_dir() / filename


def _module_name_for(filename: str) -> str:
    stem = filename.removesuffix(".py")
    return f"omega_user_strategies.{stem}"


def _unregister_classes(filename: str) -> None:
    for class_name in _LOADED_FILE_CLASSES.pop(filename, []):
        CTA_STRATEGY_REGISTRY.pop(class_name, None)


def _load_module_from_file(path: Path) -> ModuleType:
    module_name = _module_name_for(path.name)
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载策略文件: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _register_module_classes(filename: str, module: ModuleType) -> list[str]:
    discovered: list[str] = []
    for name, obj in inspect.getmembers(module, inspect.isclass):
        if obj is CtaStrategyTemplate:
            continue
        if not issubclass(obj, CtaStrategyTemplate):
            continue
        if obj.__module__ != module.__name__:
            continue  # imported, not defined here
        CTA_STRATEGY_REGISTRY[name] = obj
        discovered.append(name)
    _LOADED_FILE_CLASSES[filename] = discovered
    return discovered


def reload_strategy_file_with_engine(name: str, engine: Any = None) -> tuple[StrategyFileInfo, list[str]]:
    """Reload a file and best-effort restart any running strategies referencing its classes.

    Returns (info, restarted_strategy_names). The cta_strategy engine, if provided,
    is used to stop+remove old instances and recreate them with the freshly loaded
    class. Each restart is wrapped in try/except so partial failures don't block
    the reload.
    """

    previous_classes = list(_LOADED_FILE_CLASSES.get(_validate_filename(name), []))
    info = reload_strategy_file(name)
    restarted: list[str] = []
    if engine is None or info.error is not None:
        return info, restarted
    affected_classes = set(previous_classes) | set(info.classes)
    if not affected_classes:
        return info, restarted
    candidates: list[tuple[str, str, str, dict[str, Any], bool]] = []
    for snap in engine.strategies_snapshot():
        if snap.get("class_name") in affected_classes:
            candidates.append(
                (
                    snap["strategy_name"],
                    snap["class_name"],
                    snap["vt_symbol"],
                    dict(snap.get("parameters") or {}),
                    bool(snap.get("trading")),
                )
            )
    for strategy_name, class_name, vt_symbol, setting, was_trading in candidates:
        try:
            if was_trading:
                engine.stop_strategy(strategy_name)
            engine.remove_strategy(strategy_name)
            engine.add_strategy(
                strategy_name=strategy_name,
                class_name=class_name,
                vt_symbol=vt_symbol,
                setting=setting,
            )
            if was_trading:
                engine.start_strategy(strategy_name)
            restarted.append(strategy_name)
        except Exception:  # noqa: BLE001
            continue
    return info, restarted


def reload_strategy_file(name: str) -> StrategyFileInfo:
    path = _file_path(name)
    if not path.exists():
        raise FileNotFoundError(f"策略文件不存在: {path.name}")
    _unregister_classes(path.name)
    error: str | None = None
    classes: list[str] = []
    try:
        module = _load_module_from_file(path)
        classes = _register_module_classes(path.name, module)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    stat = path.stat()
    category, tags = _aggregate_metadata_from_classes(classes)
    return StrategyFileInfo(
        name=path.stem,
        filename=path.name,
        bytes_size=stat.st_size,
        modified_at=stat.st_mtime,
        classes=classes,
        error=error,
        category=category,
        tags=tags,
        readonly=False,
        source_type="user",
    )


def reload_all_strategy_files() -> list[StrategyFileInfo]:
    results: list[StrategyFileInfo] = []
    directory = get_strategies_dir()
    seen_files: set[str] = set()
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        seen_files.add(path.name)
        results.append(reload_strategy_file(path.stem))
    # Remove registry entries for files that no longer exist on disk.
    for stale_file in list(_LOADED_FILE_CLASSES.keys()):
        if stale_file not in seen_files:
            _unregister_classes(stale_file)
    return results


def list_strategy_files() -> list[StrategyFileInfo]:
    results: list[StrategyFileInfo] = []
    for path in sorted(get_strategies_dir().glob("*.py")):
        if path.name.startswith("_"):
            continue
        stat = path.stat()
        classes = list(_LOADED_FILE_CLASSES.get(path.name, []))
        category, tags = _aggregate_metadata_from_classes(classes)
        results.append(
            StrategyFileInfo(
                name=path.stem,
                filename=path.name,
                bytes_size=stat.st_size,
                modified_at=stat.st_mtime,
                classes=classes,
                category=category,
                tags=tags,
                readonly=False,
                source_type="user",
            )
        )
    return results


def list_builtin_strategies() -> list[StrategyFileInfo]:
    """列出所有内置策略 (每个类一条记录), 用于在前端展示只读源码视图。"""

    import inspect

    results: list[StrategyFileInfo] = []
    for class_name, cls in sorted(CTA_STRATEGY_REGISTRY.items()):
        try:
            source = inspect.getsource(cls)
            try:
                source_file = Path(inspect.getsourcefile(cls) or "")
            except TypeError:
                source_file = Path()
        except (OSError, TypeError):
            source = ""
            source_file = Path()
        size = len(source.encode("utf-8")) if source else 0
        category = (getattr(cls, "category", "") or "").strip()
        tags = [str(t).strip() for t in (getattr(cls, "tags", []) or []) if str(t).strip()]
        mtime = 0.0
        if source_file and source_file.exists():
            try:
                mtime = source_file.stat().st_mtime
            except OSError:
                mtime = 0.0
        results.append(
            StrategyFileInfo(
                name=class_name,
                filename=f"{class_name} (built-in)",
                bytes_size=size,
                modified_at=mtime,
                classes=[class_name],
                error=None,
                category=category,
                tags=tags,
                readonly=True,
                source_type="builtin",
            )
        )
    return results


def read_builtin_strategy(class_name: str) -> str:
    """返回内置策略类的 Python 源码 (仅这个类的定义)。"""

    import inspect

    cls = CTA_STRATEGY_REGISTRY.get(class_name)
    if cls is None:
        raise FileNotFoundError(f"内置策略不存在: {class_name}")
    try:
        return inspect.getsource(cls)
    except (OSError, TypeError) as exc:
        raise FileNotFoundError(f"无法读取内置策略源码: {class_name}: {exc}") from exc


def read_strategy_file(name: str) -> str:
    path = _file_path(name)
    if not path.exists():
        raise FileNotFoundError(f"策略文件不存在: {path.name}")
    return path.read_text(encoding="utf-8")


def write_strategy_file(name: str, content: str) -> StrategyFileInfo:
    if not content.strip():
        raise ValueError("策略文件内容不能为空")
    # Syntax check before touching disk.
    try:
        compile(content, _validate_filename(name), "exec")
    except SyntaxError as exc:
        raise ValueError(f"Python 语法错误: line {exc.lineno}: {exc.msg}") from exc
    path = _file_path(name)
    path.write_text(content, encoding="utf-8")
    return reload_strategy_file(path.stem)


def delete_strategy_file(name: str) -> bool:
    path = _file_path(name)
    if not path.exists():
        return False
    _unregister_classes(path.name)
    path.unlink()
    return True


SAMPLE_STRATEGY_TEMPLATE = '''"""Sample MA cross strategy — copy this file as a starting point."""

from __future__ import annotations

from omega_quant.core.strategy_template import CtaStrategyTemplate
from omega_quant.core.cta_utility import ArrayManager
from omega_quant.core.objects import BarData


class SampleMaCrossStrategy(CtaStrategyTemplate):
    """5/20 MA crossover demo strategy."""

    author = "user"
    parameters = ["fast_window", "slow_window", "fixed_size"]
    variables = ["inited", "trading", "pos", "fast_ma", "slow_ma"]

    fast_window: int = 5
    slow_window: int = 20
    fixed_size: float = 1.0

    fast_ma: float = 0.0
    slow_ma: float = 0.0

    def on_init(self) -> None:
        self.am = ArrayManager()
        self.inited = True
        self.write_log("Sample strategy initialized")

    def on_start(self) -> None:
        self.trading = True

    def on_stop(self) -> None:
        self.trading = False

    def on_tick(self, tick) -> None:  # noqa: ANN001
        pass

    def on_bar(self, bar: BarData) -> None:
        self.am.update_bar(bar)
        if not self.am.inited:
            return
        self.fast_ma = float(self.am.sma(int(self.fast_window)))
        self.slow_ma = float(self.am.sma(int(self.slow_window)))
        if not self.trading:
            return
        if self.fast_ma > self.slow_ma and self.pos <= 0:
            self.buy(bar.close_price, self.fixed_size)
        elif self.fast_ma < self.slow_ma and self.pos >= 0:
            self.sell(bar.close_price, self.fixed_size)
'''


def ensure_sample_strategy_exists() -> None:
    sample_path = get_strategies_dir() / "sample_ma_cross.py"
    if not sample_path.exists():
        sample_path.write_text(SAMPLE_STRATEGY_TEMPLATE, encoding="utf-8")

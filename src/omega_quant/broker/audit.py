"""Omega 实盘审计写盘。

设计：

- 写入 Omega 专用 ``omega_live_audit.jsonl``，便于事故复盘且不混写外部日志。
- DuckDB 镜像表名为 ``omega_live_audit``。
- 敏感字段 (api_key / private_key / token / account) 在写入前掩码或剔除。
- 同步刷盘失败时回退到 ``/private/tmp/omega-live-audit/`` 让本地仍能保留事件。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_AUDIT_JSONL = Path("reports/json/omega_live_audit.jsonl")
DEFAULT_AUDIT_DUCKDB = Path("reports/omega_live_v1.duckdb")
FALLBACK_AUDIT_DIR = Path("/private/tmp/omega-live-audit")

SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "private_key",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "massive_api_key",
    "polygon_api_key",
    "polygon_io_api_key",
    "tiger_id",
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask_account(value: Any, *, prefix: int = 2, suffix: int = 2) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= prefix + suffix:
        return "*" * len(text)
    return text[:prefix] + "*" * (len(text) - prefix - suffix) + text[-suffix:]


def sanitize_audit_value(value: Any, *, key: str = "") -> Any:
    lowered = key.lower()
    if any(part in lowered for part in SENSITIVE_KEY_PARTS):
        return "<redacted>"
    if lowered == "account":
        return _mask_account(value)
    if isinstance(value, dict):
        return {str(k): sanitize_audit_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_audit_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_audit_value(item) for item in value]
    return value


def _resolved_jsonl_path() -> Path:
    override = os.environ.get("OMEGA_LIVE_AUDIT_DIR")
    if override:
        return Path(override).expanduser() / "omega_live_audit.jsonl"
    return DEFAULT_AUDIT_JSONL


def _resolved_duckdb_path() -> Path:
    override = os.environ.get("OMEGA_LIVE_AUDIT_DIR")
    if override:
        return Path(override).expanduser() / "omega_live_v1.duckdb"
    return DEFAULT_AUDIT_DUCKDB


def _append_jsonl(path: Path, event: dict[str, Any]) -> Path:
    for candidate in (path, FALLBACK_AUDIT_DIR / path.name):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with candidate.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            return candidate
        except Exception:
            continue
    raise PermissionError(
        f"无法写入 omega 实盘审计日志：{path} 及回退 {FALLBACK_AUDIT_DIR / path.name} 都不可写。"
    )


def _write_duckdb(path: Path, event: dict[str, Any]) -> Path | None:
    try:
        import duckdb  # type: ignore
    except Exception:
        return None
    for candidate in (path, FALLBACK_AUDIT_DIR / path.name):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with duckdb.connect(str(candidate)) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS omega_live_audit (
                        id VARCHAR PRIMARY KEY,
                        created_at TIMESTAMP,
                        kind VARCHAR,
                        payload_json VARCHAR
                    )
                    """
                )
                conn.execute(
                    "INSERT OR REPLACE INTO omega_live_audit VALUES (?, ?, ?, ?)",
                    [
                        event["id"],
                        event["created_at"],
                        event["kind"],
                        json.dumps(event["payload"], ensure_ascii=False, sort_keys=True),
                    ],
                )
            return candidate
        except Exception:
            continue
    return None


def record_live_event(
    kind: str,
    payload: dict[str, Any],
    *,
    jsonl_path: Path | None = None,
    duckdb_path: Path | None = None,
) -> dict[str, Any]:
    event = {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "created_at": _utc_iso(),
        "payload": sanitize_audit_value(payload),
    }
    written_jsonl = _append_jsonl(jsonl_path or _resolved_jsonl_path(), event)
    written_duckdb = _write_duckdb(duckdb_path or _resolved_duckdb_path(), event)
    event["_jsonl_path"] = str(written_jsonl)
    if written_duckdb:
        event["_duckdb_path"] = str(written_duckdb)
    return event


def read_recent_events(path: Path | None = None, *, limit: int = 100) -> list[dict[str, Any]]:
    target = path or _resolved_jsonl_path()
    candidates = [target, FALLBACK_AUDIT_DIR / target.name]
    for candidate in candidates:
        if not candidate.exists():
            continue
        rows: list[dict[str, Any]] = []
        for line in candidate.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
        return rows
    return []

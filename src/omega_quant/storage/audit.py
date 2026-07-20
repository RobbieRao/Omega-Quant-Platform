from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Settings, mask_account


SENSITIVE_KEY_PARTS = (
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
)
AUDIT_FALLBACK_DIR = Path("/private/tmp/omega-quant-platform")


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_audit_value(value: Any, *, key: str = "") -> Any:
    lowered = key.lower()
    if any(part in lowered for part in SENSITIVE_KEY_PARTS):
        return "<redacted>"
    if lowered == "account":
        return mask_account(value)
    if isinstance(value, dict):
        return {str(k): sanitize_audit_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_audit_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_audit_value(item) for item in value]
    return value


def append_jsonl(path: Path, event: dict[str, Any]) -> None:
    for candidate in (path, AUDIT_FALLBACK_DIR / path.name):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with candidate.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            return
        except PermissionError:
            continue
    raise PermissionError(f"Unable to write audit log to {path} or fallback {AUDIT_FALLBACK_DIR / path.name}")


def write_duckdb_event(path: Path, event: dict[str, Any]) -> None:
    try:
        import duckdb
    except Exception:
        return
    for candidate in (path, AUDIT_FALLBACK_DIR / path.name):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with duckdb.connect(str(candidate)) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_events (
                        id VARCHAR PRIMARY KEY,
                        created_at TIMESTAMP,
                        kind VARCHAR,
                        payload_json VARCHAR
                    )
                    """
                )
                conn.execute(
                    "INSERT OR REPLACE INTO audit_events VALUES (?, ?, ?, ?)",
                    [
                        event["id"],
                        event["created_at"],
                        event["kind"],
                        json.dumps(event["payload"], ensure_ascii=False, sort_keys=True),
                    ],
                )
            return
        except PermissionError:
            continue


def record_audit_event(
    kind: str,
    payload: dict[str, Any],
    settings: Settings | None = None,
    *,
    jsonl_path: Path | None = None,
    duckdb_path: Path | None = None,
) -> dict[str, Any]:
    settings = settings or Settings()
    event = {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "created_at": utc_iso(),
        "payload": sanitize_audit_value(payload),
    }
    append_jsonl(jsonl_path or settings.audit_log_path, event)
    write_duckdb_event(duckdb_path or settings.audit_duckdb_path, event)
    return event


def read_audit_events(path: Path, limit: int = 200) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def render_daily_audit_summary(settings: Settings | None = None, limit: int = 200) -> list[str]:
    settings = settings or Settings()
    events = read_audit_events(settings.audit_log_path, limit=limit)
    if not events and settings.audit_log_path != AUDIT_FALLBACK_DIR / settings.audit_log_path.name:
        events = read_audit_events(AUDIT_FALLBACK_DIR / settings.audit_log_path.name, limit=limit)
    if not events:
        return ["- No Omega recommendations, approvals, or rejections have been recorded today."]
    counts: dict[str, int] = {}
    recent: list[str] = []
    for event in events:
        kind = str(event.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    for event in events[-8:]:
        payload = event.get("payload") or {}
        action = payload.get("action") or {}
        display = action.get("display") or action.get("symbol") or payload.get("symbol") or "--"
        status = payload.get("status") or payload.get("decision") or payload.get("result", {}).get("status") or event.get("kind")
        message = payload.get("message") or payload.get("result", {}).get("message") or ""
        recent.append(f"- `{display}`：{status}。{message}".rstrip())
    summary = ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))
    return [f"- 审计事件统计：{summary}。", *recent]

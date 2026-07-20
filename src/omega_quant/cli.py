"""Single command-line entry point for Omega Quant Platform."""

from __future__ import annotations

import argparse
import json
import os
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omega-quant", description="Omega Quant Platform")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("api", "demo"):
        command = subparsers.add_parser(name, help=f"start the Omega {name} service")
        command.add_argument("--host", default="127.0.0.1")
        command.add_argument("--port", type=int, default=8770)

    subparsers.add_parser("doctor", help="print a credential-safe platform readiness report")
    subparsers.add_parser("snapshot", help="print a masked, read-only TigerOpen account snapshot")
    subparsers.add_parser("data-check", help="print Massive market-data configuration readiness")
    return parser


def _serve(*, host: str, port: int, demo: bool) -> int:
    if demo:
        from pathlib import Path

        demo_root = Path("/tmp") / f"omega-quant-demo-{os.getpid()}"
        demo_root.mkdir(parents=True, exist_ok=True)
        os.environ["OMEGA_DEMO_MODE"] = "1"
        os.environ["OMEGA_DISABLE_LOCAL_SECRETS"] = "1"
        os.environ["OMEGA_TIGER_PUSH_ENABLED"] = "0"
        os.environ["OMEGA_DATA_RECORDER_AUTO_LIVE_SUBSCRIBE"] = "0"
        os.environ["OMEGA_SERVE_UI"] = "1"
        ui_dist = Path.cwd() / "frontend" / "dist"
        if ui_dist.joinpath("index.html").is_file():
            os.environ.setdefault("OMEGA_UI_DIST", str(ui_dist))
        os.environ["OMEGA_DATABASE_URL"] = f"sqlite:///{demo_root / 'omega.sqlite'}"
        os.environ["OMEGA_STRATEGIES_DIR"] = str(demo_root / "strategies")
        os.environ["OMEGA_REAL_READONLY_SNAPSHOT_PATH"] = str(demo_root / "no-account-snapshot.json")
        os.environ["OMEGA_REAL_READONLY_STREAM_PATH"] = str(demo_root / "no-account-stream.json")
        os.environ["OMEGA_STREAM_CACHE_PATH"] = str(demo_root / "no-market-cache.json")
        os.environ["OMEGA_AUDIT_LOG_PATH"] = str(demo_root / "no-private-audit.jsonl")
    os.environ["OMEGA_API_HOST"] = host
    os.environ["OMEGA_API_PORT"] = str(port)
    import uvicorn

    uvicorn.run("omega_quant.api:app", host=host, port=port, reload=False)
    return 0


def _doctor() -> int:
    from .adapters.platform_context import platform_capabilities
    from .execution_control import ExecutionControlController

    payload = {
        "platform": "Omega Quant Platform",
        "execution": ExecutionControlController().status(),
        "capabilities": platform_capabilities(),
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def _snapshot() -> int:
    from .adapters.account_context import get_real_account_snapshot

    print(json.dumps(get_real_account_snapshot(), indent=2, sort_keys=True, default=str))
    return 0


def _data_check() -> int:
    from .config import Settings

    settings = Settings()
    payload = {
        "provider": settings.resolved_market_data_provider,
        "rest_base_url": settings.massive_rest_base_url,
        "websocket_host": settings.massive_ws_host,
        "api_key_configured": bool(settings.massive_api_key),
        "options_enabled": settings.resolved_market_data_include_options,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "api":
        return _serve(host=args.host, port=args.port, demo=False)
    if args.command == "demo":
        return _serve(host=args.host, port=args.port, demo=True)
    if args.command == "doctor":
        return _doctor()
    if args.command == "snapshot":
        return _snapshot()
    return _data_check()


if __name__ == "__main__":
    raise SystemExit(main())

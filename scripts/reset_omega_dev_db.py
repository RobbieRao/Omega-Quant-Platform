#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from sqlalchemy.engine import make_url

from omega_quant.database import build_engine, build_session_factory, init_db
from omega_quant.models import Base
from omega_quant.monitor_groups import MonitorGroupService


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DEV_DATABASE_URL = f"sqlite:///{ROOT_DIR / 'data' / 'omega_quant' / 'dev.sqlite'}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reset and seed the Omega development SQLite database."
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("OMEGA_DATABASE_URL") or DEFAULT_DEV_DATABASE_URL,
        help="SQLite database URL to reset. Defaults to OMEGA_DATABASE_URL or data/omega_quant/dev.sqlite.",
    )
    return parser.parse_args()


def assert_safe_dev_sqlite(database_url: str) -> None:
    url = make_url(database_url)
    if url.drivername != "sqlite":
        raise SystemExit(f"Refusing to reset non-SQLite database: {database_url}")
    if database_url in {"sqlite://", "sqlite:///:memory:"}:
        return

    db_path = Path(str(url.database or "")).expanduser()
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    db_path = db_path.resolve()
    allowed_roots = [
        (ROOT_DIR / "data" / "omega_quant").resolve(),
        Path("/private/tmp").resolve(),
        Path(os.environ.get("TMPDIR", "/tmp")).resolve(),
    ]
    if not any(db_path == root or root in db_path.parents for root in allowed_roots):
        allowed = ", ".join(str(root) for root in allowed_roots)
        raise SystemExit(f"Refusing to reset SQLite path outside dev roots: {db_path}. Allowed roots: {allowed}")


def main() -> None:
    args = parse_args()
    assert_safe_dev_sqlite(args.database_url)

    engine = build_engine(args.database_url)
    Base.metadata.drop_all(engine)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        svc = MonitorGroupService(session)
        svc.seed_default_monitor_group()
        groups = svc.list_monitor_groups()
    finally:
        session.close()

    print("Omega dev DB reset complete.")
    print(f"database_url={args.database_url}")
    print(f"monitor_groups={len(groups)}")


if __name__ == "__main__":
    main()

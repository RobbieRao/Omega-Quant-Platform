"""Drop legacy Omega tables and slim the monitor_groups table.

Run once per environment after upgrading to the vnpy-transition branch.
Safe to re-run: all operations are guarded by IF EXISTS / column-existence checks.

Usage:
    python scripts/migrate_drop_legacy_tables.py [DB_URL]

    DB_URL defaults to sqlite:///data/omega_quant/dev.sqlite
"""

from __future__ import annotations

import sys

from sqlalchemy import create_engine, inspect, text


LEGACY_TABLES = [
    "risk_profiles",
    "factors",
    "factor_signals",
    "strategies",
    "backtest_runs",
    "orders",
    "positions",
    "lineage_events",
]

MONITOR_GROUP_LEGACY_COLUMNS = [
    "strategy_id",
    "risk_profile_id",
    "auto_submit",
    "execution_mode",
    "asset_class_allow",
    "live_unlock_acknowledged_at",
    "used_capital",
    "free_capital",
]

CTA_LEGACY_CLASSES = {"StructuredRuleStrategy", "ImmediateSignalStrategy"}


def migrate(db_url: str) -> None:
    engine = create_engine(db_url)

    with engine.begin() as conn:
        for table in LEGACY_TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
            print(f"  dropped (if existed): {table}")

    inspector = inspect(engine)

    existing_mg_cols = {col["name"] for col in inspector.get_columns("monitor_groups")}
    cols_to_remove = [c for c in MONITOR_GROUP_LEGACY_COLUMNS if c in existing_mg_cols]

    if cols_to_remove:
        keep_cols = [
            c["name"]
            for c in inspector.get_columns("monitor_groups")
            if c["name"] not in MONITOR_GROUP_LEGACY_COLUMNS
        ]
        keep_list = ", ".join(keep_cols)

        with engine.begin() as conn:
            conn.execute(text(
                f"CREATE TABLE monitor_groups_new AS SELECT {keep_list} FROM monitor_groups"
            ))
            conn.execute(text("DROP TABLE monitor_groups"))
            conn.execute(text("ALTER TABLE monitor_groups_new RENAME TO monitor_groups"))
        print(f"  slimmed monitor_groups: removed {cols_to_remove}")
    else:
        print("  monitor_groups already slim — no columns removed")

    if inspector.has_table("cta_strategy_configs"):
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    "SELECT strategy_name, class_name FROM cta_strategy_configs"
                    " WHERE class_name IN ('StructuredRuleStrategy', 'ImmediateSignalStrategy')"
                )
            )
            rows = result.fetchall()
            if rows:
                names = [r[0] for r in rows]
                conn.execute(
                    text(
                        "DELETE FROM cta_strategy_configs"
                        " WHERE class_name IN ('StructuredRuleStrategy', 'ImmediateSignalStrategy')"
                    )
                )
                print(f"  deleted legacy CTA configs: {names}")
            else:
                print("  no legacy CTA strategy configs found")

    print("Migration complete.")


if __name__ == "__main__":
    db_url = sys.argv[1] if len(sys.argv) > 1 else "sqlite:///data/omega_quant/dev.sqlite"
    print(f"Migrating: {db_url}")
    migrate(db_url)

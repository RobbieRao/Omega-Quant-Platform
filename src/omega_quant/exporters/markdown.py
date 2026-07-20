from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import Settings
from ..storage.audit import render_daily_audit_summary
from ..utils import money, pct


HKT = ZoneInfo("Asia/Hong_Kong")
ET = ZoneInfo("America/New_York")


def note_timestamp() -> datetime:
    return datetime.now(HKT)


def parse_queried_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def format_market_time(value: datetime, timezone: ZoneInfo) -> str:
    local = value.astimezone(timezone)
    return local.strftime("%Y-%m-%d %H:%M:%S %Z")


def render_time_comparison(queried_at: str) -> list[str]:
    queried_dt = parse_queried_at(queried_at)
    if queried_dt is None:
        return [f"- 原始查询时间：`{queried_at}`"]
    return [
        "| 时区 | 时间 | 用途 |",
        "|---|---:|---|",
        f"| UTC | `{format_market_time(queried_dt, ZoneInfo('UTC'))}` | API 原始时间 |",
        f"| 香港时间 | `{format_market_time(queried_dt, HKT)}` | 本地复盘与 Obsidian 日期 |",
        f"| 美东时间 | `{format_market_time(queried_dt, ET)}` | 美股盘中/盘前盘后对照 |",
    ]


def render_snapshot_note(
    snapshot: dict,
    assessment: dict,
    now: datetime | None = None,
    *,
    audit_summary: list[str] | None = None,
) -> str:
    now = now or note_timestamp()
    date_text = now.strftime("%Y-%m-%d")
    title = f"{date_text} Tiger 账户快照与交易复盘"
    queried_at = snapshot.get("queried_at", "")

    lines = [
        "---",
        "type: finance-note",
        "area: finance",
        "status: active",
        f"date: {date_text}",
        "source: tigeropen-readonly",
        "---",
        "",
        f"# {title}",
        "",
        "## Decision / Event",
        "",
        "- 事件：TigerOpen 只读账户快照。",
        "- 当前定位：10 倍实验组合的风险基线，不作为自动交易指令。",
        "",
        "### Time",
        "",
        *render_time_comparison(queried_at),
        "",
        "## Numbers",
        "",
        f"- 账户：`{assessment.get('account')}`",
        f"- 净清算价值：`{money(assessment.get('net_liquidation'))}`",
        f"- 总持仓市值：`{money(assessment.get('gross_position_value'))}`",
        f"- 现金余额：`{money(assessment.get('cash_balance'))}`",
        f"- 可交易现金：`{money(assessment.get('cash_available_for_trade'))}`",
        f"- 购买力：`{money(assessment.get('buying_power'))}`",
        f"- 杠杆：`{assessment.get('leverage')}`",
        f"- 今日盈亏：`{money(assessment.get('total_today_pl'))}`",
        f"- 未实现盈亏：`{money(assessment.get('unrealized_pl'))}`",
        f"- 期权暴露：`{money(assessment.get('option_market_value_abs'))}` "
        f"({pct(assessment.get('option_weight'))})",
        "",
        "### Positions",
        "",
        "| 标的 | 类型 | 市值 | 仓位占比 | 今日盈亏 | 未实现盈亏 | DTE |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    for row in assessment.get("positions", []):
        dte = "" if row.get("days_to_expiry") is None else str(row["days_to_expiry"])
        lines.append(
            f"| `{row.get('display')}` | {row.get('sec_type')} | "
            f"{money(row.get('market_value'))} | {pct(row.get('weight'))} | "
            f"{money(row.get('today_pnl'))} | {money(row.get('unrealized_pnl'))} | {dte} |"
        )

    lines.extend(
        [
            "",
            "## Reasoning",
            "",
            "### 风险提示",
            "",
        ]
    )
    if assessment.get("flags"):
        lines.extend(f"- {flag}" for flag in assessment["flags"])
    else:
        lines.append("- 未触发预设风险提示。")

    lines.extend(
        [
            "",
            "### Omega audit summary",
            "",
            *(audit_summary or ["- 暂无建议/批准/拒绝审计记录。"]),
            "",
            "### Thesis / 复盘问题",
            "",
            "- 当前最大的收益来源假设是什么？",
            "- 当前最大的亏损来源假设是什么？",
            "- 哪个仓位如果明天开盘反向跳空，最影响心态？",
            "- 哪个仓位的退出条件还没有写清楚？",
            "",
            "## Follow-up",
            "",
            "- [ ] 给每个期权仓位写清楚到期前退出条件。",
            "- [ ] 检查是否需要降低短期期权时间价值暴露。",
            "- [ ] 把本次判断更新到 [[20 领域/财务/投资/投资总览|投资总览]] 或相关 thesis note。",
            "",
        ]
    )
    return "\n".join(lines)


def write_snapshot_note(
    snapshot: dict,
    assessment: dict,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> Path:
    settings = settings or Settings()
    now = now or note_timestamp()
    settings.review_dir.mkdir(parents=True, exist_ok=True)
    path = settings.review_dir / f"{now.strftime('%Y-%m-%d')} Tiger 账户快照与交易复盘.md"
    path.write_text(
        render_snapshot_note(snapshot, assessment, now, audit_summary=render_daily_audit_summary(settings)),
        encoding="utf-8",
    )
    return path

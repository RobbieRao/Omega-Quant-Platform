from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MonitorGroup, id_with, utc_now
from .schemas import MonitorGroupCreate, MonitorGroupUpdate


class MonitorGroupService:
    def __init__(self, session: Session):
        self.session = session

    def seed_default_monitor_group(self) -> None:
        if self.session.get(MonitorGroup, "mg_default_alpha"):
            return
        self.session.add(
            MonitorGroup(
                id="mg_default_alpha",
                name="Alpha TigerOpen 监控组",
                description="默认示例监控组，执行目标为当前 TigerOpen 配置账户。",
                allocated_capital=10_000.0,
                status="running",
            )
        )
        self.session.commit()

    def list_monitor_groups(self, *, include_archived: bool = False) -> list[MonitorGroup]:
        stmt = select(MonitorGroup).order_by(MonitorGroup.created_at)
        if not include_archived:
            stmt = stmt.where(MonitorGroup.status != "archived")
        return list(self.session.scalars(stmt).all())

    def get_monitor_group(self, group_id: str) -> MonitorGroup:
        group = self.session.get(MonitorGroup, group_id)
        if not group:
            raise ValueError(f"monitor group not found: {group_id}")
        return group

    def create_monitor_group(self, payload: MonitorGroupCreate) -> MonitorGroup:
        group = MonitorGroup(
            name=payload.name,
            description=payload.description,
            allocated_capital=payload.allocated_capital,
            status="stopped",
        )
        self.session.add(group)
        self.session.commit()
        self.session.refresh(group)
        return group

    def update_monitor_group(self, group_id: str, payload: MonitorGroupUpdate) -> MonitorGroup:
        group = self.get_monitor_group(group_id)
        update = payload.model_dump(exclude_unset=True)
        for field in ("name", "description", "allocated_capital", "status"):
            if field in update:
                setattr(group, field, update[field])
        group.updated_at = utc_now()
        self.session.commit()
        self.session.refresh(group)
        return group

    def set_monitor_status(self, group_id: str, status: str) -> MonitorGroup:
        group = self.get_monitor_group(group_id)
        group.status = status
        group.updated_at = utc_now()
        self.session.commit()
        self.session.refresh(group)
        return group

    def delete_monitor_group(self, group_id: str) -> None:
        group = self.get_monitor_group(group_id)
        self.session.delete(group)
        self.session.commit()

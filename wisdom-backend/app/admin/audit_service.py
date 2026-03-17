"""Audit log service — append-only event logging and querying."""

import csv
import io
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.models import User
from app.cases.models import AuditLog


async def log_event(
    user_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    old_values: dict | None,
    new_values: dict | None,
    request: Request | None,
    db: AsyncSession,
) -> AuditLog:
    """Create an append-only AuditLog record."""
    entry = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=request.client.host if request and request.client else None,
        user_agent=request.headers.get("user-agent") if request else None,
        old_values=old_values,
        new_values=new_values,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def get_audit_log(
    db: AsyncSession,
    user_id: Optional[UUID] = None,
    resource_type: Optional[str] = None,
    action: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict], int]:
    """Paginated audit log with filters. Returns (items, total_count)."""
    query = select(AuditLog).order_by(AuditLog.timestamp.desc())
    count_query = select(func.count(AuditLog.id))

    if user_id:
        query = query.where(AuditLog.user_id == user_id)
        count_query = count_query.where(AuditLog.user_id == user_id)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
        count_query = count_query.where(AuditLog.resource_type == resource_type)
    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if date_from:
        query = query.where(AuditLog.timestamp >= date_from)
        count_query = count_query.where(AuditLog.timestamp >= date_from)
    if date_to:
        query = query.where(AuditLog.timestamp <= date_to)
        count_query = count_query.where(AuditLog.timestamp <= date_to)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    logs = result.scalars().all()

    # Enrich with user details
    items = []
    for log in logs:
        item = {
            "id": log.id,
            "user_id": log.user_id,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "ip_address": log.ip_address,
            "user_agent": log.user_agent,
            "old_values": log.old_values,
            "new_values": log.new_values,
            "timestamp": log.timestamp,
            "user_email": None,
            "user_role": None,
        }
        if log.user_id:
            user_result = await db.execute(
                select(User).options(selectinload(User.role)).where(User.id == log.user_id)
            )
            user = user_result.scalar_one_or_none()
            if user:
                item["user_email"] = user.email
                item["user_role"] = user.role.name if user.role else None
        items.append(item)

    return items, total


async def export_audit_log_csv(
    db: AsyncSession,
    user_id: Optional[UUID] = None,
    resource_type: Optional[str] = None,
    action: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> str:
    """Return a CSV-formatted string of audit log entries."""
    items, _ = await get_audit_log(
        db,
        user_id=user_id,
        resource_type=resource_type,
        action=action,
        date_from=date_from,
        date_to=date_to,
        page=1,
        page_size=10000,
    )

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "timestamp", "user_email", "user_role", "action",
            "resource_type", "resource_id", "ip_address",
        ],
    )
    writer.writeheader()
    for item in items:
        writer.writerow({
            "timestamp": item["timestamp"],
            "user_email": item["user_email"] or "",
            "user_role": item["user_role"] or "",
            "action": item["action"],
            "resource_type": item["resource_type"],
            "resource_id": item["resource_id"] or "",
            "ip_address": item["ip_address"] or "",
        })

    return output.getvalue()

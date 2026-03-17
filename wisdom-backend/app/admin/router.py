"""Admin panel API routes — user management, roles, audit log, config, sessions."""

import secrets
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.admin.audit_service import export_audit_log_csv, get_audit_log, log_event
from app.admin.user_service import (
    create_user,
    deactivate_user,
    force_logout_user,
    list_users,
    reset_user_password,
    update_user,
)
from app.auth.guards import get_current_user, require_admin, require_super_admin
from app.auth.models import (
    ConsentRecord,
    Permission,
    RefreshToken,
    Role,
    RolePermission,
    User,
)
from app.auth.schemas import (
    AuditLogOut,
    CreateUserRequest,
    MessageResponse,
    RoleOut,
    SystemConfigOut,
    SystemConfigUpdate,
    UpdateRolePermissionsRequest,
    UpdateUserRequest,
    UserListItem,
)
from app.cases.models import ChildCase
from app.database import get_db

router = APIRouter()


# ===========================================================================
# User Management — /admin/users
# ===========================================================================

@router.get("/users")
async def admin_list_users(
    role: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    items, total = await list_users(db, role_filter=role, status_filter=status, search=search, page=page, page_size=page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/users", status_code=201)
async def admin_create_user(
    body: CreateUserRequest,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # Check duplicate email
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    new_user = await create_user(
        db, email=body.email, password=body.password, full_name=body.full_name,
        role_name=body.role, phone=body.phone, is_verified=body.is_verified,
    )
    await log_event(user.id, "CREATE_USER", "User", str(new_user.id), None, {"email": body.email, "role": body.role}, request, db)

    role_name = new_user.role.name if new_user.role else body.role
    return {
        "id": new_user.id, "email": new_user.email, "full_name": new_user.full_name,
        "role": role_name, "is_active": new_user.is_active, "is_verified": new_user.is_verified,
    }


@router.put("/users/{user_id}")
async def admin_update_user(
    user_id: UUID,
    body: UpdateUserRequest,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    updates = body.model_dump(exclude_none=True)
    updated = await update_user(db, user_id, **updates)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")

    await log_event(user.id, "UPDATE_USER", "User", str(user_id), None, updates, request, db)
    role_name = updated.role.name if updated.role else None
    return {
        "id": updated.id, "email": updated.email, "full_name": updated.full_name,
        "role": role_name, "is_active": updated.is_active, "is_verified": updated.is_verified,
    }


@router.post("/users/{user_id}/deactivate")
async def admin_deactivate_user(
    user_id: UUID,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    success = await deactivate_user(db, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    await log_event(user.id, "DEACTIVATE_USER", "User", str(user_id), None, None, request, db)
    return MessageResponse(message="User deactivated")


@router.post("/users/{user_id}/reset-password")
async def admin_reset_password(
    user_id: UUID,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    temp_password = secrets.token_urlsafe(12)
    success = await reset_user_password(db, user_id, temp_password)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    await log_event(user.id, "RESET_PASSWORD", "User", str(user_id), None, None, request, db)
    return {"message": "Password reset", "temporary_password": temp_password}


@router.post("/users/{user_id}/force-logout")
async def admin_force_logout(
    user_id: UUID,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    await force_logout_user(db, user_id)
    await log_event(user.id, "FORCE_LOGOUT", "User", str(user_id), None, None, request, db)
    return MessageResponse(message="User sessions revoked")


@router.post("/users/bulk-deactivate")
async def admin_bulk_deactivate(
    user_ids: list[UUID],
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    count = 0
    for uid in user_ids:
        if await deactivate_user(db, uid):
            count += 1
    await log_event(user.id, "BULK_DEACTIVATE", "User", None, None, {"count": count}, request, db)
    return {"message": f"{count} users deactivated"}


@router.get("/users/export-csv")
async def admin_export_users_csv(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    items, _ = await list_users(db, page=1, page_size=10000)
    import csv, io
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["email", "full_name", "phone", "role", "is_active", "last_login", "created_at"])
    writer.writeheader()
    for item in items:
        writer.writerow({k: item.get(k, "") for k in writer.fieldnames})
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=users.csv"})


# ===========================================================================
# Roles & Permissions — /admin/roles
# ===========================================================================

@router.get("/roles")
async def admin_list_roles(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Role))
    roles = result.scalars().all()
    items = []
    for r in roles:
        count_result = await db.execute(select(func.count(User.id)).where(User.role_id == r.id))
        user_count = count_result.scalar() or 0
        items.append({
            "id": r.id, "name": r.name, "description": r.description,
            "is_system_role": r.is_system_role, "user_count": user_count,
        })
    return items


@router.get("/roles/{role_id}/permissions")
async def admin_get_role_permissions(
    role_id: UUID,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Permission)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
    )
    permissions = result.scalars().all()
    return [{"id": p.id, "resource": p.resource, "action": p.action, "description": p.description} for p in permissions]


@router.put("/roles/{role_id}/permissions")
async def admin_update_role_permissions(
    role_id: UUID,
    body: UpdateRolePermissionsRequest,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # Get old permissions for audit
    old_perms = await db.execute(
        select(RolePermission.permission_id).where(RolePermission.role_id == role_id)
    )
    old_ids = [str(row[0]) for row in old_perms.all()]

    # Delete existing and insert new
    await db.execute(
        RolePermission.__table__.delete().where(RolePermission.role_id == role_id)
    )
    for pid in body.permission_ids:
        db.add(RolePermission(role_id=role_id, permission_id=pid))
    await db.commit()

    new_ids = [str(pid) for pid in body.permission_ids]
    await log_event(user.id, "UPDATE_ROLE_PERMISSIONS", "Role", str(role_id), {"permission_ids": old_ids}, {"permission_ids": new_ids}, request, db)
    return MessageResponse(message="Permissions updated")


# ===========================================================================
# Audit Log — /admin/audit-log (Super Admin only)
# ===========================================================================

@router.get("/audit-log")
async def admin_get_audit_log(
    user_id: Optional[UUID] = None,
    resource_type: Optional[str] = None,
    action: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    df = datetime.fromisoformat(date_from) if date_from else None
    dt = datetime.fromisoformat(date_to) if date_to else None
    items, total = await get_audit_log(db, user_id=user_id, resource_type=resource_type, action=action, date_from=df, date_to=dt, page=page, page_size=page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/audit-log/export-csv")
async def admin_export_audit_csv(
    user_id: Optional[UUID] = None,
    resource_type: Optional[str] = None,
    action: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    df = datetime.fromisoformat(date_from) if date_from else None
    dt = datetime.fromisoformat(date_to) if date_to else None
    csv_data = await export_audit_log_csv(db, user_id=user_id, resource_type=resource_type, action=action, date_from=df, date_to=dt)
    return Response(content=csv_data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=audit_log.csv"})


# ===========================================================================
# Consent Tracker — /admin/consent
# ===========================================================================

@router.get("/consent")
async def admin_list_consent(
    missing_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if missing_only:
        # Cases that have NO active consent records
        subq = select(ConsentRecord.child_case_id).where(ConsentRecord.is_active == True).distinct()  # noqa: E712
        query = select(ChildCase).where(ChildCase.id.notin_(subq))
        count_query = select(func.count(ChildCase.id)).where(ChildCase.id.notin_(subq))
    else:
        query = select(ChildCase)
        count_query = select(func.count(ChildCase.id))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    cases = result.scalars().all()

    items = []
    for c in cases:
        consent_result = await db.execute(
            select(ConsentRecord).where(
                ConsentRecord.child_case_id == c.id,
                ConsentRecord.is_active == True,  # noqa: E712
            ).order_by(ConsentRecord.given_at.desc())
        )
        consents = consent_result.scalars().all()
        items.append({
            "case_id": c.id,
            "case_number": c.case_number,
            "child_name": f"{c.first_name} {c.last_name[0] if c.last_name else ''}.",
            "has_consent": len(consents) > 0,
            "consent_date": consents[0].given_at if consents else None,
            "consent_type": consents[0].consent_type if consents else None,
        })

    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ===========================================================================
# Session Monitor — /admin/sessions
# ===========================================================================

@router.get("/sessions")
async def admin_list_sessions(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List active user sessions (non-revoked refresh tokens)."""
    result = await db.execute(
        select(RefreshToken)
        .where(RefreshToken.revoked == False, RefreshToken.expires_at > func.now())  # noqa: E712
        .order_by(RefreshToken.created_at.desc())
    )
    tokens = result.scalars().all()

    items = []
    for t in tokens:
        user_result = await db.execute(
            select(User).options(selectinload(User.role)).where(User.id == t.user_id)
        )
        u = user_result.scalar_one_or_none()
        if u:
            items.append({
                "session_id": t.id,
                "user_id": u.id,
                "user_email": u.email,
                "user_name": u.full_name,
                "role": u.role.name if u.role else None,
                "login_time": t.created_at,
                "expires_at": t.expires_at,
            })

    return items


@router.post("/sessions/{session_id}/force-logout")
async def admin_force_logout_session(
    session_id: UUID,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(RefreshToken).where(RefreshToken.id == session_id).values(revoked=True)
    )
    await db.commit()
    await log_event(user.id, "FORCE_LOGOUT_SESSION", "Session", str(session_id), None, None, request, db)
    return MessageResponse(message="Session terminated")


# ===========================================================================
# System Configuration — /admin/config (Super Admin only)
# ===========================================================================

@router.get("/config")
async def admin_get_config(
    user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.admin.models import SystemConfig
    result = await db.execute(select(SystemConfig))
    configs = result.scalars().all()
    return [
        {"key": c.key, "masked_value": f"****{c.encrypted_value[-4:]}" if len(c.encrypted_value) > 4 else "****"}
        for c in configs
    ]


@router.put("/config")
async def admin_update_config(
    body: SystemConfigUpdate,
    request: Request,
    user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.admin.models import SystemConfig
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == body.key))
    config = result.scalar_one_or_none()

    if config:
        old_masked = f"****{config.encrypted_value[-4:]}" if len(config.encrypted_value) > 4 else "****"
        config.encrypted_value = body.value
        config.updated_by = user.id
    else:
        old_masked = None
        config = SystemConfig(key=body.key, encrypted_value=body.value, updated_by=user.id)

    db.add(config)
    await db.commit()

    new_masked = f"****{body.value[-4:]}" if len(body.value) > 4 else "****"
    await log_event(user.id, "CONFIG_CHANGE", "SystemConfig", body.key, {"value": old_masked}, {"value": new_masked}, request, db)
    return MessageResponse(message=f"Configuration '{body.key}' updated")


# ===========================================================================
# Dashboard stats — /admin/dashboard
# ===========================================================================

@router.get("/dashboard")
async def admin_dashboard(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.assessments.models import AssessmentResponse, RiskAlert

    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    active_cases = (await db.execute(
        select(func.count(ChildCase.id)).where(ChildCase.status == "active")
    )).scalar() or 0

    # Assessments completed this month
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    assessments_this_month = (await db.execute(
        select(func.count(AssessmentResponse.id)).where(
            AssessmentResponse.completed_at >= month_start
        )
    )).scalar() or 0

    # Risk alerts this month
    risk_alerts_this_month = (await db.execute(
        select(func.count(RiskAlert.id)).where(RiskAlert.created_at >= month_start)
    )).scalar() or 0

    return {
        "total_users": total_users,
        "active_cases": active_cases,
        "assessments_this_month": assessments_this_month,
        "risk_alerts_this_month": risk_alerts_this_month,
    }


# ===========================================================================
# WhatsApp Campaigns — /admin/campaigns
# ===========================================================================

@router.post("/campaigns/send")
async def send_campaign(
    template_name: str,
    recipient_group: str,  # "parents" or "donors"
    template_params: list[str],
    campaign_name: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.messaging.campaigns import send_campaign_broadcast, get_active_parent_phones, get_opted_in_donor_phones

    if recipient_group == "parents":
        recipients = await get_active_parent_phones(db)
    elif recipient_group == "donors":
        recipients = await get_opted_in_donor_phones(db)
    else:
        raise HTTPException(400, detail="recipient_group must be 'parents' or 'donors'")

    if not recipients:
        return {"status": "no_recipients", "total": 0}

    result = await send_campaign_broadcast(
        template_name=template_name,
        recipient_list=recipients,
        template_params=template_params,
        campaign_name=campaign_name,
        db=db,
        background_tasks=background_tasks,
    )
    return result

"""In-app notification service."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.models import Notification, ScheduledNotification

logger = logging.getLogger(__name__)


async def create_notification(
    user_id: UUID,
    title: str,
    body: str,
    notification_type: str,
    related_resource_type: Optional[str] = None,
    related_resource_id: Optional[UUID] = None,
    db: Optional[AsyncSession] = None,
) -> Optional[Notification]:
    """Create an in-app notification for a user."""
    if not db:
        return None

    notification = Notification(
        user_id=user_id,
        title=title,
        body=body,
        type=notification_type,
        related_resource_type=related_resource_type,
        related_resource_id=related_resource_id,
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)
    return notification


async def get_user_notifications(
    user_id: UUID,
    unread_only: bool = False,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = None,
) -> tuple[list[Notification], int]:
    """Get paginated notifications for a user."""
    if not db:
        return [], 0

    query = select(Notification).where(Notification.user_id == user_id)
    count_query = select(func.count(Notification.id)).where(Notification.user_id == user_id)

    if unread_only:
        query = query.where(Notification.is_read == False)  # noqa: E712
        count_query = count_query.where(Notification.is_read == False)  # noqa: E712

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(Notification.created_at.desc())
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    notifications = result.scalars().all()
    return list(notifications), total


async def mark_notification_read(
    notification_id: UUID,
    db: AsyncSession,
) -> bool:
    """Mark a notification as read."""
    result = await db.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    notification = result.scalar_one_or_none()
    if not notification:
        return False
    notification.is_read = True
    await db.commit()
    return True


async def schedule_appointment_reminders(booking_id: UUID, db: AsyncSession, background_tasks):
    """
    Schedule two WhatsApp reminders for a therapy session booking:
    1. 24h before: APPOINTMENT_REMINDER_24H
    2. 1h before: APPOINTMENT_REMINDER_1H
    Store in ScheduledNotification table.
    """
    from app.scheduling.models import RoomBooking, Room
    from app.cases.models import ChildCase
    from app.auth.models import User

    result = await db.execute(
        select(RoomBooking).where(RoomBooking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking or booking.booking_type != "therapy":
        return

    # Get room name
    room_result = await db.execute(select(Room).where(Room.id == booking.room_id))
    room = room_result.scalar_one_or_none()

    # Get case details for guardian phone
    if not booking.case_id:
        return
    case_result = await db.execute(select(ChildCase).where(ChildCase.id == booking.case_id))
    case = case_result.scalar_one_or_none()
    if not case or not case.guardian_phone:
        return

    # Get therapist name
    therapist_result = await db.execute(select(User).where(User.id == booking.booked_by))
    therapist = therapist_result.scalar_one_or_none()

    # Decrypt names if needed (they're encrypted in ChildCase)
    from app.security.encryption import decrypt_value
    child_first_name = decrypt_value(case.first_name) if case.first_name else "Child"
    guardian_phone = decrypt_value(case.guardian_phone) if case.guardian_phone else None
    if not guardian_phone:
        return

    therapist_name = therapist.full_name if therapist and hasattr(therapist, 'full_name') else (therapist.email if therapist else "Staff")
    room_name = room.name if room else "Room"
    booking_date = booking.start_datetime.strftime("%d %b %Y")
    booking_time = booking.start_datetime.strftime("%I:%M %p")

    # Schedule 24h reminder
    reminder_24h_at = booking.start_datetime - timedelta(hours=24)
    if reminder_24h_at > datetime.now(timezone.utc):
        notif_24h = ScheduledNotification(
            notification_type="appointment_reminder_24h",
            scheduled_at=reminder_24h_at,
            payload={
                "recipient_phone": guardian_phone,
                "template_name": "APPOINTMENT_REMINDER_24H",
                "template_params": [child_first_name, therapist_name, booking_date, booking_time, room_name],
                "booking_id": str(booking_id),
            },
            status="pending",
        )
        db.add(notif_24h)

    # Schedule 1h reminder
    reminder_1h_at = booking.start_datetime - timedelta(hours=1)
    if reminder_1h_at > datetime.now(timezone.utc):
        notif_1h = ScheduledNotification(
            notification_type="appointment_reminder_1h",
            scheduled_at=reminder_1h_at,
            payload={
                "recipient_phone": guardian_phone,
                "template_name": "APPOINTMENT_REMINDER_1H",
                "template_params": [child_first_name, booking_date, booking_time],
                "booking_id": str(booking_id),
            },
            status="pending",
        )
        db.add(notif_1h)

    await db.commit()


async def notify_assessment_assigned(assignment_id: UUID, db: AsyncSession, background_tasks):
    """Send ASSESSMENT_DUE WhatsApp to guardian when assessment is assigned to parent."""
    from app.assessments.models import AssessmentAssignment, Assessment
    from app.cases.models import ChildCase
    from app.security.encryption import decrypt_value
    from app.config import get_settings

    result = await db.execute(
        select(AssessmentAssignment).where(AssessmentAssignment.id == assignment_id)
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        return

    # Get assessment title
    assess_result = await db.execute(
        select(Assessment).where(Assessment.id == assignment.assessment_id)
    )
    assessment = assess_result.scalar_one_or_none()

    # Get case for guardian phone
    case_result = await db.execute(
        select(ChildCase).where(ChildCase.id == assignment.case_id)
    )
    case = case_result.scalar_one_or_none()
    if not case or not case.guardian_phone:
        return

    guardian_phone = decrypt_value(case.guardian_phone) if case.guardian_phone else None
    if not guardian_phone:
        return

    settings = get_settings()
    portal_link = f"{settings.FRONTEND_URL}/parent/portal/assessments/{assignment_id}"
    due_date = assignment.due_date.strftime("%d %b %Y") if hasattr(assignment, 'due_date') and assignment.due_date else "N/A"

    from app.messaging.whatsapp import send_whatsapp_template
    await send_whatsapp_template(
        recipient_phone=guardian_phone,
        template_name="ASSESSMENT_DUE",
        template_params=[assessment.title if assessment else "Assessment", due_date, portal_link],
        case_id=assignment.case_id,
        risk_alert_id=None,
        db=db,
    )


async def process_scheduled_notifications(db: AsyncSession):
    """
    Process pending scheduled notifications that are past their scheduled_at time.
    Called every 5 minutes by a background task.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ScheduledNotification).where(
            ScheduledNotification.status == "pending",
            ScheduledNotification.scheduled_at <= now,
        )
    )
    notifications = result.scalars().all()

    for notif in notifications:
        try:
            from app.messaging.whatsapp import send_whatsapp_template
            payload = notif.payload or {}
            await send_whatsapp_template(
                recipient_phone=payload.get("recipient_phone", ""),
                template_name=payload.get("template_name", ""),
                template_params=payload.get("template_params", []),
                case_id=None,
                risk_alert_id=None,
                db=db,
            )
            notif.status = "sent"
        except Exception as e:
            logger.error(f"Failed to process scheduled notification {notif.id}: {e}")
            notif.status = "failed"

    await db.commit()

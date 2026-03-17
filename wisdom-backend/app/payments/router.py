"""Payment router — invoices, Razorpay, and Stripe endpoints."""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.audit_service import log_event
from app.auth.guards import get_current_user, require_role
from app.auth.models import User
from app.database import get_db
from app.payments.invoicing import (
    create_invoice,
    generate_invoice_pdf,
    get_invoice,
    list_invoices,
    send_invoice,
    update_invoice_status,
)
from app.payments.schemas import (
    InvoiceCreate,
    InvoiceOut,
    InvoiceStatusUpdate,
    RazorpayOrderCreate,
    StripeIntentCreate,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Staff/Admin role guard
# ---------------------------------------------------------------------------
require_staff = require_role("super_admin", "admin", "chief_therapist", "supervisor", "therapist", "staff")


# ---------------------------------------------------------------------------
# Invoice endpoints
# ---------------------------------------------------------------------------


@router.post("/invoices", response_model=InvoiceOut, status_code=201)
async def create_invoice_endpoint(
    body: InvoiceCreate,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
):
    """Create a new invoice (Staff/Admin only)."""
    billing_details = {
        "billing_name": body.billing_name,
        "billing_email": body.billing_email,
        "billing_phone": body.billing_phone,
    }
    line_items = [item.model_dump() for item in body.line_items]

    invoice_dict = await create_invoice(
        case_id=body.case_id,
        billing_details=billing_details,
        line_items=line_items,
        discount=body.discount,
        apply_gst=body.apply_gst,
        currency=body.currency,
        due_date=body.due_date,
        created_by=user.id,
        db=db,
    )

    background_tasks.add_task(
        log_event,
        user_id=user.id,
        action="invoice_created",
        resource_type="invoice",
        resource_id=str(invoice_dict["id"]),
        old_values=None,
        new_values={"invoice_number": invoice_dict["invoice_number"], "total": invoice_dict["total"]},
        request=None,
        db=db,
    )

    return invoice_dict


@router.get("/invoices")
async def list_invoices_endpoint(
    status: Optional[str] = Query(None, description="Filter by status: draft/sent/paid/cancelled"),
    case_id: Optional[UUID] = Query(None, description="Filter by case ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List invoices with optional filters and pagination."""
    invoices, total = await list_invoices(
        db=db,
        status=status,
        case_id=case_id,
        page=page,
        page_size=page_size,
    )
    return {
        "items": invoices,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
async def get_invoice_endpoint(
    invoice_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get invoice detail by ID."""
    invoice_dict = await get_invoice(invoice_id, db)
    if not invoice_dict:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice_dict


@router.get("/invoices/{invoice_id}/pdf")
async def download_invoice_pdf(
    invoice_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Download invoice as PDF."""
    try:
        pdf_bytes = await generate_invoice_pdf(invoice_id, db)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Fetch invoice number for the filename
    invoice_dict = await get_invoice(invoice_id, db)
    filename = f"{invoice_dict['invoice_number']}.pdf" if invoice_dict else "invoice.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/invoices/{invoice_id}/send")
async def send_invoice_endpoint(
    invoice_id: UUID,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
):
    """Send invoice with Razorpay payment link via WhatsApp."""
    try:
        payment_link = await send_invoice(invoice_id, db, background_tasks)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    await log_event(
        user_id=user.id,
        action="invoice_sent",
        resource_type="invoice",
        resource_id=str(invoice_id),
        old_values=None,
        new_values={"payment_link": payment_link},
        request=None,
        db=db,
    )

    return {"status": "sent", "payment_link": payment_link}


@router.put("/invoices/{invoice_id}/status", response_model=InvoiceOut)
async def update_invoice_status_endpoint(
    invoice_id: UUID,
    body: InvoiceStatusUpdate,
    user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
):
    """Manually mark an invoice as paid or cancelled."""
    if body.status not in ("paid", "cancelled"):
        raise HTTPException(status_code=400, detail="Status must be 'paid' or 'cancelled'")

    invoice_dict = await update_invoice_status(invoice_id, body.status, db)
    if not invoice_dict:
        raise HTTPException(status_code=404, detail="Invoice not found")

    await log_event(
        user_id=user.id,
        action="invoice_status_updated",
        resource_type="invoice",
        resource_id=str(invoice_id),
        old_values=None,
        new_values={"status": body.status},
        request=None,
        db=db,
    )

    return invoice_dict


# ---------------------------------------------------------------------------
# Razorpay endpoints
# ---------------------------------------------------------------------------


@router.post("/razorpay/orders")
async def create_razorpay_order_endpoint(
    body: RazorpayOrderCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Razorpay order for client-side checkout."""
    from app.payments.razorpay import create_razorpay_order

    try:
        order = await create_razorpay_order(
            amount_paise=body.amount_paise,
            currency=body.currency,
            receipt=body.receipt,
        )
    except Exception as exc:
        logger.exception("Failed to create Razorpay order")
        raise HTTPException(status_code=502, detail=f"Razorpay error: {exc}")

    return order


@router.post("/razorpay/webhook")
async def razorpay_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Razorpay webhook receiver — no authentication required."""
    payload = await request.json()
    signature = request.headers.get("X-Razorpay-Signature", "")

    from app.payments.razorpay import handle_razorpay_webhook

    try:
        result = await handle_razorpay_webhook(payload, signature, db, background_tasks)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return result


# ---------------------------------------------------------------------------
# Stripe endpoints
# ---------------------------------------------------------------------------


@router.post("/stripe/create-intent")
async def create_stripe_intent(
    body: StripeIntentCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Stripe PaymentIntent for the donation form."""
    from app.payments.stripe_client import create_stripe_payment_intent

    try:
        intent = await create_stripe_payment_intent(
            amount_cents=body.amount_cents,
            currency=body.currency,
            metadata=body.metadata or {},
        )
    except Exception as exc:
        logger.exception("Failed to create Stripe PaymentIntent")
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc}")

    return intent


@router.post("/stripe/webhook")
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Stripe webhook receiver — no authentication required."""
    payload = await request.body()
    signature = request.headers.get("Stripe-Signature", "")

    from app.payments.stripe_client import handle_stripe_webhook

    try:
        result = await handle_stripe_webhook(payload, signature, db, background_tasks)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return result

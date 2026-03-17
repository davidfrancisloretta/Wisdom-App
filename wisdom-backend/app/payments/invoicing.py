"""Invoice creation, PDF generation, and sending."""
import io
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.payments.models import Invoice

logger = logging.getLogger(__name__)


async def generate_invoice_number(db: AsyncSession) -> str:
    """Generate invoice number: INV-{YEAR}-{5-digit-sequence}."""
    year = datetime.now(timezone.utc).year
    prefix = f"INV-{year}-"
    result = await db.execute(
        select(func.count(Invoice.id)).where(
            Invoice.invoice_number.like(f"{prefix}%")
        )
    )
    count = (result.scalar() or 0) + 1
    return f"{prefix}{count:05d}"


async def create_invoice(
    case_id: UUID | None,
    billing_details: dict,
    line_items: list[dict],
    discount: float,
    apply_gst: bool,
    currency: str,
    due_date,
    created_by: UUID | None,
    db: AsyncSession,
) -> dict:
    """Create invoice with calculated totals."""
    # Calculate line item amounts
    processed_items = []
    subtotal = 0
    for item in line_items:
        amount = item.get("amount") or (item.get("rate", 0) * item.get("quantity", 1))
        processed_items.append({**item, "amount": float(amount)})
        subtotal += amount

    discount_amount = float(discount)
    taxable = subtotal - discount_amount
    tax_amount = round(taxable * 0.18, 2) if apply_gst else 0
    total = round(taxable + tax_amount, 2)

    invoice_number = await generate_invoice_number(db)

    invoice = Invoice(
        invoice_number=invoice_number,
        case_id=case_id,
        billing_name=billing_details.get("billing_name", ""),
        billing_email=billing_details.get("billing_email"),
        billing_phone=billing_details.get("billing_phone"),
        line_items={"items": processed_items},
        subtotal=subtotal,
        discount_amount=discount_amount,
        tax_amount=tax_amount,
        total=total,
        currency=currency,
        status="draft",
        due_date=due_date,
        created_by=created_by,
    )
    db.add(invoice)
    await db.commit()
    await db.refresh(invoice)

    return _invoice_to_dict(invoice)


async def get_invoice(invoice_id: UUID, db: AsyncSession) -> dict | None:
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    invoice = result.scalar_one_or_none()
    return _invoice_to_dict(invoice) if invoice else None


async def list_invoices(
    db: AsyncSession,
    status: str | None = None,
    case_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query = select(Invoice).order_by(Invoice.created_at.desc())
    count_query = select(func.count(Invoice.id))

    if status:
        query = query.where(Invoice.status == status)
        count_query = count_query.where(Invoice.status == status)
    if case_id:
        query = query.where(Invoice.case_id == case_id)
        count_query = count_query.where(Invoice.case_id == case_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    invoices = result.scalars().all()

    return [_invoice_to_dict(inv) for inv in invoices], total


async def update_invoice_status(
    invoice_id: UUID, status: str, db: AsyncSession
) -> dict | None:
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    invoice = result.scalar_one_or_none()
    if not invoice:
        return None
    invoice.status = status
    if status == "paid":
        invoice.paid_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(invoice)
    return _invoice_to_dict(invoice)


async def generate_invoice_pdf(invoice_id: UUID, db: AsyncSession) -> bytes:
    """Generate a professional PDF invoice using reportlab."""
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise ValueError("Invoice not found")

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('InvoiceTitle', parent=styles['Heading1'], fontSize=24, textColor=colors.HexColor('#462380'))

    elements = []

    # Header
    elements.append(Paragraph("The Ark Centre", title_style))
    elements.append(Paragraph("INVOICE", styles['Heading2']))
    elements.append(Spacer(1, 10*mm))

    # Invoice details
    elements.append(Paragraph(f"<b>Invoice #:</b> {invoice.invoice_number}", styles['Normal']))
    elements.append(Paragraph(f"<b>Date:</b> {invoice.created_at.strftime('%d %b %Y')}", styles['Normal']))
    if invoice.due_date:
        elements.append(Paragraph(f"<b>Due Date:</b> {invoice.due_date.strftime('%d %b %Y')}", styles['Normal']))
    elements.append(Paragraph(f"<b>Status:</b> {invoice.status.upper()}", styles['Normal']))
    elements.append(Spacer(1, 5*mm))

    # Bill to
    elements.append(Paragraph("<b>Bill To:</b>", styles['Normal']))
    elements.append(Paragraph(invoice.billing_name, styles['Normal']))
    if invoice.billing_email:
        elements.append(Paragraph(invoice.billing_email, styles['Normal']))
    if invoice.billing_phone:
        elements.append(Paragraph(invoice.billing_phone, styles['Normal']))
    elements.append(Spacer(1, 10*mm))

    # Line items table
    table_data = [['#', 'Description', 'Type', 'Rate', 'Qty', 'Amount']]
    items = (invoice.line_items or {}).get('items', [])
    for i, item in enumerate(items, 1):
        table_data.append([
            str(i),
            item.get('description', ''),
            item.get('item_type', ''),
            f"{invoice.currency} {item.get('rate', 0):,.2f}",
            str(item.get('quantity', 1)),
            f"{invoice.currency} {item.get('amount', 0):,.2f}",
        ])

    table = Table(table_data, colWidths=[15*mm, 55*mm, 30*mm, 25*mm, 15*mm, 30*mm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#462380')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ALIGN', (3, 0), (-1, -1), 'RIGHT'),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 5*mm))

    # Totals
    totals_data = [
        ['', '', '', '', 'Subtotal:', f"{invoice.currency} {float(invoice.subtotal):,.2f}"],
        ['', '', '', '', 'Discount:', f"-{invoice.currency} {float(invoice.discount_amount):,.2f}"],
        ['', '', '', '', 'GST (18%):', f"{invoice.currency} {float(invoice.tax_amount):,.2f}"],
        ['', '', '', '', 'TOTAL:', f"{invoice.currency} {float(invoice.total):,.2f}"],
    ]
    totals = Table(totals_data, colWidths=[15*mm, 55*mm, 30*mm, 25*mm, 15*mm, 30*mm])
    totals.setStyle(TableStyle([
        ('FONTNAME', (-2, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (-2, -1), (-1, -1), 12),
        ('LINEABOVE', (-2, -1), (-1, -1), 1, colors.black),
        ('ALIGN', (-2, 0), (-1, -1), 'RIGHT'),
    ]))
    elements.append(totals)

    doc.build(elements)
    return buffer.getvalue()


async def send_invoice(invoice_id: UUID, db: AsyncSession, background_tasks):
    """Generate PDF, create Razorpay payment link, send WhatsApp."""
    invoice_dict = await get_invoice(invoice_id, db)
    if not invoice_dict:
        raise ValueError("Invoice not found")

    # Create Razorpay payment link
    from app.payments.razorpay import create_razorpay_payment_link
    payment_link = await create_razorpay_payment_link(
        amount_paise=int(float(invoice_dict["total"]) * 100),
        description=f"Invoice {invoice_dict['invoice_number']}",
        customer_name=invoice_dict["billing_name"],
        customer_phone=invoice_dict.get("billing_phone", ""),
        customer_email=invoice_dict.get("billing_email", ""),
        reference_id=invoice_dict["invoice_number"],
    )

    # Send WhatsApp notification
    if invoice_dict.get("billing_phone"):
        from app.messaging.whatsapp import send_whatsapp_template
        background_tasks.add_task(
            send_whatsapp_template,
            recipient_phone=invoice_dict["billing_phone"],
            template_name="INVOICE_SENT",
            template_params=[
                invoice_dict["invoice_number"],
                f"{invoice_dict['currency']} {float(invoice_dict['total']):,.2f}",
                str(invoice_dict.get("due_date", "N/A")),
                payment_link,
            ],
            case_id=invoice_dict.get("case_id"),
            risk_alert_id=None,
            db=db,
        )

    # Update status
    await update_invoice_status(invoice_id, "sent", db)
    return payment_link


def _invoice_to_dict(invoice: Invoice) -> dict:
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "case_id": invoice.case_id,
        "billing_name": invoice.billing_name,
        "billing_email": invoice.billing_email,
        "billing_phone": invoice.billing_phone,
        "line_items": invoice.line_items,
        "subtotal": float(invoice.subtotal),
        "discount_amount": float(invoice.discount_amount),
        "tax_amount": float(invoice.tax_amount),
        "total": float(invoice.total),
        "currency": invoice.currency,
        "status": invoice.status,
        "due_date": invoice.due_date,
        "paid_at": invoice.paid_at,
        "payment_gateway": invoice.payment_gateway,
        "gateway_payment_id": invoice.gateway_payment_id,
        "created_by": invoice.created_by,
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
    }

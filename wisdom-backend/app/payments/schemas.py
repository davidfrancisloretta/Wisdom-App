"""Payment Pydantic schemas."""
from datetime import date, datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel


class LineItem(BaseModel):
    item_type: str  # therapy_session, workshop, room_rental, donation_match
    description: str
    rate: float
    quantity: float = 1
    amount: Optional[float] = None  # auto-calculated if not provided


class InvoiceCreate(BaseModel):
    case_id: Optional[UUID] = None
    billing_name: str
    billing_email: Optional[str] = None
    billing_phone: Optional[str] = None
    line_items: list[LineItem]
    discount: float = 0
    apply_gst: bool = True
    currency: str = "INR"
    due_date: Optional[date] = None


class InvoiceOut(BaseModel):
    id: UUID
    invoice_number: str
    case_id: Optional[UUID] = None
    billing_name: str
    billing_email: Optional[str] = None
    billing_phone: Optional[str] = None
    line_items: Optional[dict] = None
    subtotal: float
    discount_amount: float
    tax_amount: float
    total: float
    currency: str
    status: str
    due_date: Optional[date] = None
    paid_at: Optional[datetime] = None
    payment_gateway: Optional[str] = None
    created_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class InvoiceStatusUpdate(BaseModel):
    status: str  # paid/cancelled


class PaymentOut(BaseModel):
    id: UUID
    invoice_id: Optional[UUID] = None
    gateway: str
    gateway_payment_id: str
    amount: float
    currency: str
    status: str
    method: Optional[str] = None
    captured_at: Optional[datetime] = None
    created_at: datetime
    model_config = {"from_attributes": True}


class RazorpayOrderCreate(BaseModel):
    amount_paise: int
    currency: str = "INR"
    receipt: str


class StripeIntentCreate(BaseModel):
    amount_cents: int
    currency: str = "usd"
    metadata: Optional[dict] = None

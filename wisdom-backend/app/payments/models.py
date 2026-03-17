"""Payments domain models — Invoices, Payments, Donations, Campaigns."""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    case_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("child_cases.id"), nullable=True)
    billing_name: Mapped[str] = mapped_column(String(255), nullable=False)
    billing_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    billing_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    line_items: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    subtotal: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    discount_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    total: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft/sent/paid/overdue/cancelled
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_gateway: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    gateway_payment_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    payments: Mapped[list["Payment"]] = relationship(back_populates="invoice")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=True)
    gateway: Mapped[str] = mapped_column(String(50), nullable=False)
    gateway_payment_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    gateway_order_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/captured/failed/refunded
    method: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # upi/card/netbanking/wallet
    captured_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    invoice: Mapped[Optional["Invoice"]] = relationship(back_populates="payments")


class DonationCampaign(Base):
    __tablename__ = "donation_campaigns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    goal_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    raised_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    room_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("rooms.id"), nullable=True)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    donations: Mapped[list["Donation"]] = relationship(back_populates="campaign")


class Donation(Base):
    __tablename__ = "donations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    donor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    donor_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    donor_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    campaign_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("donation_campaigns.id"), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    recurrence_interval: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # monthly/quarterly/annually
    gateway: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    gateway_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    receipt_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    receipt_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    campaign: Mapped[Optional["DonationCampaign"]] = relationship(back_populates="donations")

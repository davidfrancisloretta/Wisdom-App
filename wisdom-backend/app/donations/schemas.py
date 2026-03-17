"""Donation Pydantic schemas."""
from datetime import date, datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel


class CampaignCreate(BaseModel):
    title: str
    description: Optional[str] = None
    goal_amount: float
    room_id: Optional[UUID] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class CampaignUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    goal_amount: Optional[float] = None
    is_active: Optional[bool] = None
    room_id: Optional[UUID] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class CampaignOut(BaseModel):
    id: UUID
    title: str
    description: Optional[str] = None
    goal_amount: float
    raised_amount: float
    is_active: bool
    room_id: Optional[UUID] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    created_by: Optional[UUID] = None
    donor_count: int = 0
    progress_pct: float = 0
    model_config = {"from_attributes": True}


class OneTimeDonation(BaseModel):
    donor_name: str
    donor_email: Optional[str] = None
    donor_phone: Optional[str] = None
    campaign_id: Optional[UUID] = None
    amount: float
    currency: str = "INR"
    message: Optional[str] = None


class RecurringDonation(BaseModel):
    donor_name: str
    donor_email: str
    donor_phone: Optional[str] = None
    campaign_id: Optional[UUID] = None
    amount: float
    currency: str = "INR"
    recurrence_interval: str = "monthly"  # monthly/quarterly/annually


class DonationOut(BaseModel):
    id: UUID
    donor_name: str
    donor_email: Optional[str] = None
    donor_phone: Optional[str] = None
    campaign_id: Optional[UUID] = None
    amount: float
    currency: str
    is_recurring: bool
    recurrence_interval: Optional[str] = None
    status: str
    receipt_sent: bool
    created_at: datetime
    campaign_title: Optional[str] = None
    model_config = {"from_attributes": True}

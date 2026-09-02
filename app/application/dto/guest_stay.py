"""Mehmonning turish tarixi.

Mehmonlar sahifasida "qachon qaysi xonada, kim bilan turgan" degan savolga
javob beradi. Bandlovlar ro'yxatidan farqi shundaki, bu yerda mehmon
HAMROH sifatida qatnashgan turishlar ham bor — "kim bilan kelgan" savoli
aynan shularsiz to'liq javob olmaydi.
"""
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class StayCompanion(BaseModel):
    """Turishda birga bo'lgan odam."""

    guest_id: UUID | None = None
    name: str | None = None
    phone: str | None = None
    #: Bronни kim ochgan — asosiy mehmon
    is_primary: bool = False
    #: Ro'yxat so'ralayotgan mehmonning o'zimi
    is_self: bool = False


class GuestStayResponse(BaseModel):
    id: UUID
    reservation_number: str
    #: MAIN — bronni shu mehmon ochgan; COMPANION — hamroh bo'lib turgan
    role: str

    booking_type: str
    check_in_date: date
    check_out_date: date
    check_in_datetime: datetime | None = None
    check_out_datetime: datetime | None = None
    status: str

    room_id: UUID | None = None
    room_number: str | None = None
    room_type_name: str | None = None
    floor_number: int | None = None
    branch_name: str | None = None

    adults: int = 1
    children: int = 0
    total_amount: float = 0
    paid_amount: float = 0
    payment_status: str | None = None

    #: Shu turishda xonada bo'lganlar — asosiy mehmon va hamrohlar
    people: list[StayCompanion] = []

    created_at: datetime


class GuestStaySummary(BaseModel):
    """Tarix bo'yicha qisqa jamlanma."""

    total_stays: int = 0
    #: Bekor qilingan va kelmagan turishlar hisobga olinmaydi
    completed_stays: int = 0
    total_nights: int = 0
    total_paid: float = 0
    first_stay: date | None = None
    last_stay: date | None = None
    #: Eng ko'p turgan xona
    favourite_room: str | None = None


class GuestHistoryResponse(BaseModel):
    summary: GuestStaySummary
    stays: list[GuestStayResponse] = []

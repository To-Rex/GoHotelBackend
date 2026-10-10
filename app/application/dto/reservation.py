from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class ReservationPaymentItem(BaseModel):
    """Qisman (bo'lib) to'lovning bitta bo'lagi: summa + to'lov usuli."""

    amount: float = Field(gt=0)
    payment_method: Literal[
        # Kanonik to'rtlik — ilova shularni yuboradi
        "CASH", "CARD", "ONLINE", "BANK_TRANSFER",
        # Eski kodlar: mobil ilova va eski klientlar hali ham
        # yuborishi mumkin, shuning uchun qabul qilinadi
        "CREDIT_CARD", "DEBIT_CARD", "MOBILE_PAYMENT", "TRANSFER",
    ]


class ExpectedCompanionIn(BaseModel):
    """Kechikib keladigan hamroh — ismi/telefoni ixtiyoriy.

    DIQQAT: `ReservationCreateRequest` dan OLDIN turishi shart — prod'da
    Python 3.12, u maydon turlarini sinf yaratilayotganda hisoblaydi
    (3.14 dagi kechiktirilgan annotatsiyalar u yerda yo'q)."""

    name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=200)


class ReservationCreateRequest(BaseModel):
    guest_id: UUID
    room_id: UUID
    branch_id: UUID
    booking_type: Literal["DAILY", "HOURLY"] = Field(default="DAILY")
    check_in_date: date
    check_out_date: date
    check_in_datetime: datetime | None = None
    check_out_datetime: datetime | None = None
    adults: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)
    discount_amount: float = Field(default=0, ge=0)
    discount_percent: float = Field(default=0, ge=0, le=100)
    notes: str | None = None
    payment_amount: float = Field(default=0, ge=0)
    payment_method: Literal[
        # Kanonik to'rtlik — ilova shularni yuboradi
        "CASH", "CARD", "ONLINE", "BANK_TRANSFER",
        # Eski kodlar: mobil ilova va eski klientlar hali ham
        # yuborishi mumkin, shuning uchun qabul qilinadi
        "CREDIT_CARD", "DEBIT_CARD", "MOBILE_PAYMENT", "TRANSFER",
    ] | None = None
    # Qisman to'lov: bir nechta usul bilan bo'lib to'lash. Berilsa payment_amount /
    # payment_method o'rniga shu ro'yxat ishlatiladi (eski klientlar uchun eski
    # maydonlar o'zgarishsiz qoladi).
    payments: list[ReservationPaymentItem] = Field(default_factory=list)
    # Hamrohlar: shu xonada turadigan qolgan mehmonlarning ID'lari.
    # Asosiy mehmon bu ro'yxatga kirmaydi, ya'ni jami = 1 + len(ro'yxat)
    companion_guest_ids: list[UUID] = Field(default_factory=list, max_length=20)
    # Kechikib keladigan hamrohlar: joy band qilinadi, kelganda biriktiriladi
    expected_companions: list[ExpectedCompanionIn] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_hourly_booking(self):
        if self.booking_type == "HOURLY":
            if not self.check_in_datetime or not self.check_out_datetime:
                raise ValueError("check_in_datetime and check_out_datetime are required for hourly booking")
            if self.check_in_datetime >= self.check_out_datetime:
                raise ValueError("check_out_datetime must be after check_in_datetime")
        if self.payment_amount > 0 and not self.payment_method:
            raise ValueError("payment_method is required when payment_amount > 0")
        return self


class ReservationUpdateRequest(BaseModel):
    room_id: UUID | None = None
    booking_type: Literal["DAILY", "HOURLY"] | None = None
    check_in_date: date | None = None
    check_out_date: date | None = None
    check_in_datetime: datetime | None = None
    check_out_datetime: datetime | None = None
    adults: int | None = Field(None, ge=1)
    children: int | None = Field(None, ge=0)
    discount_amount: float | None = Field(None, ge=0)
    discount_percent: float | None = Field(None, ge=0, le=100)
    notes: str | None = None


class ReservationExtendRequest(BaseModel):
    """Bronni cho'zish so'rovi.

    `check_out` — bronning YANGI tugash payti. Soatlik bronda bu aniq
    vaqt (masalan 2026-09-03T21:00:00), kunlik bronda esa chiqish kuni
    boshlanishi (2026-09-05T00:00:00). Vaqt zonasisiz yuboriladi: loyihada
    bu ustunlar xodim tergan devor soatini saqlaydi.
    """

    check_out: datetime


class ReservationCancelRequest(BaseModel):
    reason: str | None = None
    #: Mehmonga qaytariladigan summa. Berilmasa mehmonxona sozlamasidagi
    #: foizdan hisoblanadi; berilsa xodim tanlagani ustun turadi (masalan
    #: kech bekor qilishda ko'proq ushlab qolish).
    refund_amount: float | None = Field(default=None, ge=0)
    refund_method: str | None = None


class MoveRoomRequest(BaseModel):
    new_room_id: UUID
    #: Qimmatroq xonaga o'tishda narx farqidan chegirma (so'm). Berilmasa —
    #: chegirmasiz (avvalgi xatti-harakat). Qoida: move_discount_policy
    discount_amount: float | None = Field(default=None, ge=0, le=1_000_000_000_000)


class CompanionAddRequest(BaseModel):
    """Turish davomida hamroh qo'shish — mehmon bazada oldin yaratiladi."""

    guest_id: UUID
    #: Kechikib kelishi kutilgan hamroh keldi — o'sha yozuv o'rniga
    expected_id: str | None = Field(default=None, max_length=64)


class SettlePaymentRequest(BaseModel):
    """Bron balansi bo'yicha hisob-kitob (xona almashtirishdan keyin):
    PAY — qo'shimcha to'lov (qisman ham mumkin), REFUND — ortiqcha
    to'langan summani qaytarish."""

    amount: float = Field(gt=0)
    payment_method: Literal[
        "CASH", "CREDIT_CARD", "DEBIT_CARD", "BANK_TRANSFER", "MOBILE_PAYMENT", "ONLINE"
    ]
    direction: Literal["PAY", "REFUND"]


class ReservationServiceAddRequest(BaseModel):
    hotel_service_id: UUID
    quantity: int = Field(default=1, ge=1)
    service_date: date | None = None
    notes: str | None = None


class ReservationResponse(BaseModel):
    id: UUID
    hotel_id: UUID
    branch_id: UUID
    reservation_number: str
    guest_id: UUID
    room_id: UUID
    booking_type: str
    check_in_date: date
    check_out_date: date
    check_in_datetime: datetime | None = None
    check_out_datetime: datetime | None = None
    adults: int
    children: int
    status: str
    total_amount: float
    paid_amount: float
    payment_status: str
    discount_amount: float
    discount_percent: float
    # Qimmatroq xonaga ko'chirishda berilgan chegirma (so'm), bron
    # chegirmasidan alohida
    move_discount_amount: float = 0
    # Faol jarimalar (kech chiqish, shikast) yig'indisi — jamiga kirgan
    penalty_amount: float = 0
    # Kunlik hisob: "12h" — 1 kecha = narx, "24h" — 1 kun = narx × 2
    daily_unit: str = "12h"
    notes: str | None
    cancelled_reason: str | None
    cancelled_at: datetime | None
    # Resepsiya "mehmon chiqmoqda" deb belgilagan vaqt (chiqish jarayonida)
    checkout_requested_at: datetime | None = None
    # Xona ko'chirishlar auditi (kim, qachon, qaysi xonadan qaysinisiga)
    room_moves: list | None = None
    # Hamrohlar: [{"guest_id": ..., "name": ...}, ...]
    companions: list | None = None
    # Kechikib keladigan hamrohlar: [{"id", "name", "phone", "note", ...}]
    expected_companions: list | None = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReservationDetailResponse(ReservationResponse):
    guest: dict | None = None
    room: dict | None = None
    services: list[dict] = []
    invoice: dict | None = None


class CalendarQueryParams(BaseModel):
    view: str = Field(default="daily", pattern=r"^(daily|weekly|monthly)$")
    date: date
    branch_id: UUID | None = None
    room_type_id: UUID | None = None


class AvailabilityQueryParams(BaseModel):
    check_in: date
    check_out: date
    branch_id: UUID | None = None
    room_type_id: UUID | None = None
    adults: int = Field(default=1, ge=1)


class AvailabilityRoomResponse(BaseModel):
    id: UUID
    room_number: str
    room_type_id: UUID
    room_type_name: str
    floor_id: UUID
    floor_number: int
    base_price: float
    current_status: str

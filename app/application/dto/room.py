from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class FloorCreateRequest(BaseModel):
    branch_id: UUID
    hotel_id: UUID | None = None
    floor_number: int = Field(..., ge=-10, le=200)
    name: str | None = None


class FloorUpdateRequest(BaseModel):
    floor_number: int | None = Field(None, ge=-10, le=200)
    name: str | None = None


class FloorResponse(BaseModel):
    id: UUID
    hotel_id: UUID
    branch_id: UUID
    floor_number: int
    name: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RoomTypeCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    capacity: int = Field(default=1, ge=1)
    base_price: float = Field(..., gt=0)
    amenities: list[str] = []


class RoomTypeUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    capacity: int | None = Field(None, ge=1)
    base_price: float | None = Field(None, gt=0)
    amenities: list[str] | None = None
    is_active: bool | None = None


class RoomTypeResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    capacity: int
    base_price: float
    amenities: list
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class HotelRoomTypeRequest(BaseModel):
    room_type_id: UUID


class RoomCreateRequest(BaseModel):
    branch_id: UUID
    floor_id: UUID
    room_type_id: UUID
    room_number: str = Field(..., min_length=1, max_length=20)
    base_price: float = Field(default=0, ge=0)
    capacity: int | None = Field(None, ge=1)
    notes: str | None = None


class RoomUpdateRequest(BaseModel):
    floor_id: UUID | None = None
    room_type_id: UUID | None = None
    base_price: float | None = Field(None, ge=0)
    capacity: int | None = Field(None, ge=1)
    notes: str | None = None


class RoomStatusUpdateRequest(BaseModel):
    status: str = Field(
        ..., pattern=r"^(AVAILABLE|RESERVED|OCCUPIED|CLEANING|MAINTENANCE|INSPECTION|OUT_OF_SERVICE)$"
    )
    notes: str | None = None


class RoomResponse(BaseModel):
    id: UUID
    hotel_id: UUID
    branch_id: UUID
    floor_id: UUID
    room_type_id: UUID
    room_number: str
    base_price: float
    capacity: int | None
    current_status: str
    notes: str | None
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
    # Xona joriy holatga qachon o'tgani. `updated_at` bu ish uchun yaramaydi —
    # u narx yoki izoh tahrirlansa ham yangilanadi. Tarix yozuvi topilmasa
    # None (masalan holati hech qachon o'zgarmagan eski xona).
    status_changed_at: datetime | None = None

    model_config = {"from_attributes": True}


class RoomDetailResponse(RoomResponse):
    room_type: RoomTypeResponse | None = None
    floor: FloorResponse | None = None


class RoomStatusHistoryResponse(BaseModel):
    id: UUID
    room_id: UUID
    status: str
    changed_by: UUID | None
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReservationOccupant(BaseModel):
    """Xonada turgan bir kishi — asosiy mehmon yoki hamroh.

    Hamrohlar bronda faqat ID va ism bilan saqlanadi (o'sha paytdagi holat
    qotib qolishi uchun). Bu yerda esa ularning kartochkasi ham qo'shiladi:
    tafsilot oynasida xodim hujjat raqamini yoki telefonini ko'rishi kerak
    bo'ladi, mehmonlar sahifasiga o'tib qidirmasdan.

    Mehmon bazadan topilmasa (o'chirilgan bo'lsa) faqat bronda saqlangan
    ism qoladi — yozuv yo'qolmaydi.
    """

    guest_id: UUID | None = None
    name: str | None = None
    # Ism va familiya alohida ham: tahrirlash shakli ularni bo'lingan holda
    # so'raydi, `name` ni bo'lakka ajratish esa qo'sh familiyalarda xato
    # qilardi
    first_name: str | None = None
    last_name: str | None = None
    is_primary: bool = False
    phone: str | None = None
    email: str | None = None
    passport_number: str | None = None
    id_document_type: str | None = None
    id_document_number: str | None = None
    nationality: str | None = None
    birth_date: date | None = None
    address: str | None = None
    notes: str | None = None
    #: Yuz biriktirilgan mehmon — kamera uni taniydi
    has_face: bool = False


class RoomReservationResponse(BaseModel):
    """Xona kartochkasidagi "Bandlovlar" ro'yxati uchun.

    Mehmon ismi shu yerda keladi — ro'yxatni ko'rsatish uchun mehmonlar
    bazasini alohida yuklash shart emas.
    """

    id: UUID
    reservation_number: str
    guest_id: UUID
    guest_name: str | None = None
    guest_phone: str | None = None
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
    notes: str | None = None
    cancelled_reason: str | None = None
    discount_percent: float = 0
    # Hamrohlar: [{"guest_id": ..., "name": ...}, ...] — xonada kim turgani
    companions: list | None = None
    created_at: datetime

    # --- Tafsilot oynasi uchun: kim, qachon, qayerda ---
    #
    # Ro'yxatning o'zi bu maydonlarsiz ham ishlaydi; ular bron ustiga
    # bosilganda ochiladigan to'liq ma'lumot oynasi uchun. Alohida endpoint
    # qilinmadi: ma'lumot bir xil so'rovdan keladi va bosilganda yana bir
    # bor kutib turishning hojati yo'q.
    updated_at: datetime | None = None
    cancelled_at: datetime | None = None
    checkout_requested_at: datetime | None = None
    created_by_name: str | None = None
    cancelled_by_name: str | None = None
    branch_name: str | None = None
    room_number: str | None = None
    room_type_name: str | None = None
    floor_number: int | None = None
    floor_name: str | None = None
    # Xona ko'chirishlar tarixi: [{from, to, by, at, ...}, ...]
    room_moves: list | None = None
    # Xonada turganlar — asosiy mehmon va hamrohlar, kartochkalari bilan.
    # `companions` maydoni o'z holicha qoladi: u bronda saqlangan xom yozuv.
    occupants: list[ReservationOccupant] | None = None

    model_config = {"from_attributes": True}

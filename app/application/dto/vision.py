"""Kamera agenti va qabulxona paneli uchun DTO'lar."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Agent -> server
# ---------------------------------------------------------------------------


class FaceQualityPayload(BaseModel):
    """Agentda o'lchangan sifat ko'rsatkichlari."""

    score: float = 0.0
    sharpness: float = 0.0
    brightness: float = 0.0
    contrast: float = 0.0
    face_pixels: int = 0
    issues: list[str] = Field(default_factory=list)


class RecognitionPayload(BaseModel):
    """Agent hisoblagan vektorlar.

    ``template`` — bir epizoddagi kadrlardan yig'ilgan yakuniy shablon;
    ``samples`` — unga kirgan alohida kadrlar. Serverda ikkalasi ham asqotadi:
    shablon bilan qidiriladi, namunalar esa mehmon keyin biriktirilganda
    sifatliroq profil yasash imkonini beradi.
    """

    model: str = "sface_2021dec"
    dim: int = 128
    #: base64(float32 little-endian)
    template: Optional[str] = None
    samples: list[str] = Field(default_factory=list)
    cohesion: float = 0.0
    sample_count: int = 0
    dropped: int = 0


class FaceEventRequest(BaseModel):
    """Bitta odam epizodi — kamera agenti yuboradigan asosiy hodisa."""

    track_uid: str = Field(min_length=8, max_length=64)
    camera_id: str = Field(min_length=1, max_length=64)
    camera_name: Optional[str] = Field(default=None, max_length=128)
    location: Optional[str] = Field(default=None, max_length=128)
    capture_id: Optional[str] = Field(default=None, max_length=64)
    device_id: Optional[str] = Field(default=None, max_length=128)
    timestamp: Optional[datetime] = None
    confidence: float = 0.0
    quality: Optional[FaceQualityPayload] = None
    recognition: Optional[RecognitionPayload] = None


# ---------------------------------------------------------------------------
# Server -> agent
# ---------------------------------------------------------------------------


class MatchedGuest(BaseModel):
    guest_id: UUID
    name: str
    phone: Optional[str] = None
    #: Mehmonning shu paytda ochiq broni bormi — agent buni ko'rsatmaydi,
    #: lekin panel darhol to'g'ri harakatni taklif qilishi uchun kerak.
    has_active_reservation: bool = False


class FaceEventResponse(BaseModel):
    """Agent kutadigan javob (``RecognitionStatus`` bilan mos)."""

    status: Literal["recognized", "uncertain", "unknown", "invalid", "duplicate"]
    sighting_id: Optional[UUID] = None
    guest: Optional[MatchedGuest] = None
    similarity: Optional[float] = None
    margin: Optional[float] = None
    candidates: int = 0
    learned: bool = False
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------


class SightingResponse(BaseModel):
    id: UUID
    status: str
    camera_id: str
    camera_name: Optional[str] = None
    location: Optional[str] = None
    seen_at: datetime
    similarity: float
    margin: float
    quality_score: float
    branch_id: Optional[UUID] = None
    guest_id: Optional[UUID] = None
    guest_name: Optional[str] = None
    guest_phone: Optional[str] = None
    #: Mehmonning oxirgi tashrifi — qabulxonaga darhol kontekst beradi.
    last_stay_at: Optional[datetime] = None
    visits: int = 0
    #: Ochiq yoki kutilayotgan broni bormi. Bu javob paneldagi TUGMANI
    #: belgilaydi: broni bo'lgan mehmonni kutib olish kerak, yangi bron
    #: yaratish emas.
    has_active_reservation: bool = False
    has_thumbnail: bool = False
    can_enroll: bool = False
    acknowledged: bool = False

    class Config:
        from_attributes = True


class SightingListResponse(BaseModel):
    items: list[SightingResponse]
    unacknowledged: int = 0
    engine: str = "agent"


class SightingGroupResponse(BaseModel):
    """Bitta odamning bir necha ko'rinishi — panel uchun bitta karta.

    Bir odam kamera oldidan uch marta o'tsa uchta epizod yoziladi. Ularni
    alohida ko'rsatish xodimni chalkashtiradi ("bularning qaysi biri?") va
    biriktirilmagan ikkitasi ro'yxatda qolib ketadi. Guruhlangani esa
    aniqroq ham: uch epizodning vektorlari birga o'rtachalanadi.
    """

    #: Guruhning barcha ko'rinish id'lari — biriktirishda hammasi ishlatiladi.
    sighting_ids: list[UUID]
    #: Eng sifatli ko'rinish — panelda shuning surati ko'rsatiladi.
    best_sighting_id: UUID
    count: int
    camera_id: str
    camera_name: Optional[str] = None
    location: Optional[str] = None
    branch_id: Optional[UUID] = None
    first_seen_at: datetime
    last_seen_at: datetime
    quality_score: float = 0.0
    #: A'zolarning guruh markaziga o'rtacha o'xshashligi. Past qiymat —
    #: guruhga boshqa odam qo'shilgan bo'lishi mumkin degan belgi.
    cohesion: float = 1.0
    has_thumbnail: bool = False


class SightingGroupListResponse(BaseModel):
    items: list[SightingGroupResponse]
    #: Guruhlanmagan (vektori yo'q) ko'rinishlar soni — diagnostika uchun.
    ungrouped: int = 0


class EnrollSightingRequest(BaseModel):
    guest_id: UUID
    #: Mehmon biometrik ma'lumot saqlashga rozilik berdi. Bu bayroqsiz
    #: biriktirish rad etiladi — rozilik huquqiy shart, texnik emas.
    consent: bool = False
    #: Guruhning qolgan ko'rinishlari. Ular ham shu mehmonga yoziladi va
    #: vektorlari shablonga qo'shiladi — bir necha epizoddan yig'ilgan
    #: shablon bittasidan sezilarli aniqroq bo'ladi.
    sighting_ids: list[UUID] = Field(default_factory=list)


class FaceProfileStatus(BaseModel):
    guest_id: UUID
    enrolled: bool
    profiles: int
    consent_at: Optional[datetime] = None
    last_matched_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Qurilmalar
# ---------------------------------------------------------------------------


class VisionDeviceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    branch_id: Optional[UUID] = None


class VisionDeviceResponse(BaseModel):
    id: UUID
    name: str
    device_id: Optional[str] = None
    branch_id: Optional[UUID] = None
    is_active: bool
    token_hint: str
    last_seen_at: Optional[datetime] = None
    events_received: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


class VisionDeviceCreatedResponse(VisionDeviceResponse):
    #: Token FAQAT shu javobda ochiq ko'rinadi — bazada xeshi saqlanadi.
    token: str


# ---------------------------------------------------------------------------
# Kameralar
# ---------------------------------------------------------------------------


class VisionCameraResponse(BaseModel):
    id: UUID
    camera_id: str
    name: Optional[str] = None
    location: Optional[str] = None
    branch_id: Optional[UUID] = None
    branch_name: Optional[str] = None
    device_id: UUID
    device_name: Optional[str] = None
    is_active: bool
    sightings_count: int = 0
    last_seen_at: Optional[datetime] = None
    created_at: datetime

    @property
    def is_assigned(self) -> bool:
        return self.branch_id is not None

    class Config:
        from_attributes = True


class VisionCameraUpdateRequest(BaseModel):
    """Kamerani filialga biriktirish yoki vaqtincha o'chirish.

    ``branch_id`` ni ``None`` qilib yuborish biriktirishni bekor qiladi —
    shundan keyin kamera suratlari filial bo'yicha filtrlangan ro'yxatlarda
    ko'rinmaydi, ya'ni yangi mehmonga biriktirib bo'lmaydi.
    """

    branch_id: Optional[UUID] = None
    name: Optional[str] = Field(default=None, max_length=128)
    is_active: Optional[bool] = None

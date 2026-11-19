"""Smena va kassa endpointlari.

Kassali rejim mehmonxona sozlamalarida yoqiladi (faqat ADMIN). Xodimlar
smenani ochadi, kassani topshiradi (ko'r sanash), smenani tugallaydi;
keyingi xodim parol bilan qabul qiladi; admin/menejer majburiy yopadi.
"""
from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.configurator_access import assert_can_manage_settings
from app.core.database import get_db
from app.core.exceptions import ForbiddenException, ValidationException
from app.application.services.shift_service import ShiftService
from app.presentation.middleware.auth import get_current_user
from app.presentation.api.v1._deps import require_active_hotel

router = APIRouter(dependencies=[Depends(require_active_hotel)])


def _hotel_id(current_user: dict) -> UUID:
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return UUID(str(hotel_id))


class ShiftSettingsRequest(BaseModel):
    mode: str = Field(..., pattern=r"^(simple|cash)$")
    day_close: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    # Kesim vaqtida kassa topshirish majburiymi (False — faqat eslatma)
    day_close_required: bool = True
    # Kassani topshirishda admin/menejer tasdig'i talab qilinsinmi.
    # Yuborilmasa (None) — saqlangan qiymat o'zgartirilmaydi.
    handover_approval: bool | None = None
    # Kassa faqat YOPILGAN KUNLAR bo'yicha topshirilsinmi: kunlik kesim
    # yopgan kunlarning hammasi birga, bugungi tushum — keyingi kesimdan
    # keyin. Yuborilmasa (None) — saqlangan qiymat o'zgartirilmaydi.
    handover_closed_days: bool | None = None
    # Kassani topshirish tartibi (uch holatli; yuqoridagi bayroq — uning
    # eski ko'rinishi): "free" — erkin; "closed_days" — faqat yopilgan
    # kunlar; "prev_shifts" — faqat oldingi smenalardan qolgan pul.
    # Yuborilmasa (None) — saqlangan qiymat o'zgartirilmaydi.
    handover_scope: str | None = Field(
        default=None, pattern=r"^(free|closed_days|prev_shifts)$"
    )


class OpenShiftRequest(BaseModel):
    opening_cash: float = Field(default=0, ge=0)


class CashCountRequest(BaseModel):
    counted_cash: float = Field(..., ge=0)
    notes: str | None = Field(default=None, max_length=1000)


class CloseCashRequest(CashCountRequest):
    """Kassani topshirish.

    `cut_at` — KUNLIK KESIM bo'yicha topshirish: `GET /shifts/expected-cash`
    javobidagi `cuts[].cut_at` qiymatlaridan biri. Faqat shu kesimgacha
    bo'lgan kassa topshiriladi, kesimdan keyingi tushum yangi sessiyada
    qoladi. Yuborilmasa — avvalgidek, hozirgacha bo'lgan hamma pul.

    Topshirish tartibi cheklangan bo'lsa (`handover_scope` sozlamasi) —
    `cut_at` MAJBURIY:
      "closed_days" — oxirgi kesim (`cuts` ning oxirgisi): yopilgan kunlarning
        hammasi birga, bugungi tushum topshirilmaydi;
      "prev_shifts" — `prev_shifts.cut_at` (sessiya boshlanishi): faqat
        oldingi smenalardan qolgan pul, xodimning o'z tushumi topshirilmaydi.
    """

    cut_at: datetime | None = None


class AcceptShiftRequest(BaseModel):
    password: str = Field(..., min_length=1)


class ForceCloseRequest(BaseModel):
    session_id: UUID
    counted_cash: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=1000)
    # Kassa keyingi xodim qabul qilishini kutsinmi (asosiy holat) yoki
    # pulni admin o'zi olib, sessiya butunlay yopilsinmi
    hand_over: bool = True


class CorrectShiftRequest(BaseModel):
    counted_cash: float = Field(..., ge=0)
    # Izoh majburiy — tuzatish sababi auditda qolishi shart
    note: str = Field(..., min_length=3, max_length=1000)


class ApproveHandoverRequest(BaseModel):
    """Tasdiqlash izohi (ixtiyoriy) — sessiya izohiga qo'shiladi."""

    notes: str | None = Field(default=None, max_length=500)


@router.get("/settings")
async def get_settings(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).get_settings(_hotel_id(current_user))


@router.put("/settings")
async def save_settings(
    data: ShiftSettingsRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    assert_can_manage_settings(current_user)
    return await ShiftService(session).save_settings(
        _hotel_id(current_user),
        data.mode,
        data.day_close,
        data.day_close_required,
        data.handover_approval,
        data.handover_closed_days,
        data.handover_scope,
    )


@router.get("/state")
async def get_state(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).get_state(_hotel_id(current_user), current_user)


@router.get("/expected-cash")
async def expected_cash(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Faol sessiya kassasida bo'lishi kerak bo'lgan summa (tarkibi bilan).

    `cuts` — kunlik kesim bo'yicha topshirish variantlari (eskidan yangiga):
    har biri shu kesimgacha topshirilsa qancha pul topshirilishini
    ko'rsatadi. Sessiya oxirgi kesimdan keyin ochilgan bo'lsa — bo'sh.

    `handover_scope` — topshirish tartibi (sozlama). "closed_days" da
    (`closed_days_only`) tanlov yo'q: faqat oxirgi kesimgacha topshiriladi
    (`cuts` bo'sh bo'lsa — hali topshirib bo'lmaydi). "prev_shifts" da
    `prev_shifts` keladi — oldingi smenalardan qolgan pul (`due_cash`; 0
    bo'lsa topshiradigan narsa yo'q) va kimdan qabul qilingani.
    `next_cut_at` — keyingi kunlik kesim momenti."""
    return await ShiftService(session).my_expected_cash(
        _hotel_id(current_user), current_user
    )


@router.get("/cash-overview")
async def cash_overview(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Admin/menejer: hozir har ochiq kassada qancha pul bo'lishi kerak
    (tarkibi bilan) va jami. Oddiy rejimda — bo'sh."""
    return await ShiftService(session).cash_overview(
        _hotel_id(current_user), current_user
    )


@router.post("/open")
async def open_shift(
    data: OpenShiftRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).open_session(
        _hotel_id(current_user), current_user, data.opening_cash
    )


@router.post("/continue")
async def continue_shift(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).continue_shift(_hotel_id(current_user), current_user)


@router.post("/close-cash")
async def close_cash(
    data: CloseCashRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).close_cash(
        _hotel_id(current_user),
        current_user,
        data.counted_cash,
        data.notes,
        data.cut_at,
    )


@router.post("/end")
async def end_shift(
    data: CashCountRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).end_shift(
        _hotel_id(current_user), current_user, data.counted_cash, data.notes
    )


@router.post("/accept")
async def accept_shift(
    data: AcceptShiftRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).accept_shift(
        _hotel_id(current_user), current_user, data.password
    )


@router.post("/force-close")
async def force_close(
    data: ForceCloseRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).force_close(
        _hotel_id(current_user),
        current_user,
        data.session_id,
        data.counted_cash,
        data.notes,
        data.hand_over,
    )


@router.put("/{session_id}/correct")
async def correct_shift(
    session_id: UUID,
    data: CorrectShiftRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Yopilgan sessiya sanalgan summasini tuzatish (admin/menejer, izoh shart)."""
    return await ShiftService(session).correct_session(
        _hotel_id(current_user),
        current_user,
        session_id,
        data.counted_cash,
        data.note,
    )


@router.post("/{session_id}/approve")
async def approve_handover(
    session_id: UUID,
    data: ApproveHandoverRequest | None = None,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Topshirilgan kassani TASDIQLASH (admin/menejer) — tasdiq rejimida.

    "Kassani topshirishda admin tasdig'i" sozlamasi yoqilganda xodim
    topshirgach sessiya `pending_approval` bilan tasdiq kutadi; shu
    endpoint uni yakunlaydi (pul kassadan chiqadi) va kassir ishda davom
    etgan bo'lsa yangi kassa 0 so'mdan ochiladi.
    """
    return await ShiftService(session).approve_handover(
        _hotel_id(current_user),
        current_user,
        session_id,
        data.notes if data else None,
    )


@router.get("/handovers")
async def get_handovers(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Smenadan smenaga o'tgan pullar (admin/menejer): har topshirilgan
    kassa — kimdan kimga, qancha, kutilgan va farq; kunlik kesimda yoki
    majburiy yopishda kassadan chiqqan pul ham."""
    if date_from and date_to and date_from > date_to:
        raise ValidationException(
            "Boshlanish sanasi tugash sanasidan keyin bo'lmasin", "INVALID_RANGE"
        )
    return await ShiftService(session).get_handovers(
        _hotel_id(current_user), current_user, date_from, date_to, limit
    )


@router.get("/history")
async def get_history(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).get_history(
        _hotel_id(current_user), current_user, limit
    )

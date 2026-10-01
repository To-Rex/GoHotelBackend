"""Xabarchi SMS integratsiyasi.

Mijozga SMS ikki hodisada ketadi: bron yaratilganda va to'lov qabul
qilinganda. Har FILIALGA alohida API kalit biriktiriladi — kalit bazada
SHIFRLANGAN saqlanadi (Fernet, kaliti SECRET_KEY dan hosil qilinadi:
bazani o'qigan odam kalitni ochola olmaydi, server esa o'z sirini
bilgani uchun ochaveradi).

Yuborish fire-and-forget: SMS xatosi bron yoki to'lov oqimini HECH
QACHON buzmaydi — muammo faqat logga tushadi. Kalit kiritilmagan
filialda hamma narsa avvalgidek, SMS'siz ishlayveradi.

Xabarchi API (o'z Android telefon + SIM orqali yuboradi):
    POST {SMS_API_BASE}/public/messages
    X-API-Key: xab_live_...          (ruxsat: sms.send)
    {"to": ["+998901234567"], "text": "...", "priority": "transactional"}
    → 201 [{"id": 42, "status": "queued", ...}]
    xato → {"code": "auth_error" | "quota_exceeded" | ..., "message": "..."}

SMS_API_BASE Xabarchi BACKEND'ining manzili (`.../api/v1`), veb-saytniki
emas — sayt domeni POST'ga 405 qaytaradi va SMS ketmaydi.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)


# --- Kalitni shifrlash --------------------------------------------------

def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_key(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt_key(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        # SECRET_KEY almashgan bo'lsa eski yozuv ochilmaydi — kalit
        # sozlamalardan qayta kiritiladi
        return None


def mask_key(key: str) -> str:
    """Ko'rsatish uchun niqoblangan kalit: boshi va oxiri, o'rtasi yashirin."""
    if len(key) <= 14:
        return key[:4] + "…"
    return key[:10] + "…" + key[-4:]


# --- Telefon raqami -----------------------------------------------------

def normalize_phone(raw: str | None) -> str | None:
    """O'zbekiston raqamini +998XXXXXXXXX ko'rinishiga keltiradi.

    Mehmon bazasida raqamlar har xil yozilgan (90 123 45 67,
    +998901234567, 998901234567) — SMS servisi esa faqat to'liq
    xalqaro formatni qabul qiladi. Keltirib bo'lmasa None: SMS
    shunchaki yuborilmaydi, xato ko'tarilmaydi.
    """
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) == 12 and digits.startswith("998"):
        return "+" + digits
    return None


def _fmt_amount(value: float) -> str:
    return f"{int(round(value)):,}".replace(",", " ")


# --- Yuborish -----------------------------------------------------------

class SmsError(RuntimeError):
    """SMS yuborilmadi. `code` — sabab (Xabarchi kodi yoki o'zimizniki),
    `str(exc)` — xodimga ko'rsatsa bo'ladigan tushuntirish."""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


#: Xabarchi xato kodlari → xodim tushunadigan sabab. Sozlamalardagi
#: "Sinov SMS" tugmasi aynan shu matnni ko'rsatadi.
_REASONS = {
    "auth_error": "API kalit noto'g'ri yoki o'chirilgan — Xabarchi'dan yangi kalit oling",
    "forbidden": "API kalitda SMS yuborish ruxsati (sms.send) yo'q",
    "quota_exceeded": "Xabarchi tarifidagi oylik SMS limiti tugagan",
    "subscription_inactive": "Xabarchi obunasi faol emas",
    "rate_limited": "Juda ko'p so'rov — bir daqiqadan so'ng qayta urinib ko'ring",
    "invalid_phone": "Telefon raqami noto'g'ri",
    "not_found": "Xabarchi'da tanlangan qurilma topilmadi",
}

_BAD_URL = (
    "SMS xizmati manzili noto'g'ri sozlangan: SMS_API_BASE Xabarchi "
    "backend'iga (API) qaratilishi kerak, veb-saytiga emas"
)


def api_url(base: str | None = None) -> str:
    """`.../api/v1/public/messages` — `/api/v1` yozilmagan bo'lsa qo'shiladi."""
    root = (base if base is not None else settings.SMS_API_BASE).strip().rstrip("/")
    if not root.endswith("/api/v1"):
        root += "/api/v1"
    return root + "/public/messages"


def _json(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return None


async def send_sms(api_key: str, phone: str, text: str) -> dict:
    """Bitta SMS'ni Xabarchi navbatiga qo'yadi va yaratilgan xabarni qaytaradi.

    Xabarchi 201 bilan xabar(lar) ro'yxatini qaytaradi (`status: queued`) —
    SMS'ni keyin hisobga ulangan Android telefon yuboradi. Muvaffaqiyatsiz
    javobda sababi aniq [SmsError] ko'tariladi: xodim "405" emas, "manzil
    noto'g'ri" yoki "kalit noto'g'ri" degan gapni ko'rishi kerak.
    """
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                api_url(),
                headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                json={"to": [phone], "text": text, "priority": "transactional"},
            )
    except httpx.HTTPError as exc:
        raise SmsError(
            "unreachable",
            f"SMS xizmatiga ulanib bo'lmadi ({type(exc).__name__})",
        ) from exc

    data = _json(resp)
    if resp.status_code >= 400:
        code = data.get("code") if isinstance(data, dict) else None
        if code:
            reason = _REASONS.get(code) or str(data.get("message") or code)
            raise SmsError(code, reason, resp.status_code)
        # Xabarchi xatolari doim {code, message} — boshqa narsa kelgan bo'lsa
        # so'rov API'ga emas, boshqa serverga (masalan, veb-saytga) tushgan
        if resp.status_code in (404, 405) or data is None:
            raise SmsError("bad_api_url", _BAD_URL, resp.status_code)
        raise SmsError(
            "api_error", f"SMS xizmati xatosi ({resp.status_code})", resp.status_code
        )
    # Muvaffaqiyat — xabarlar ro'yxati. HTML yoki boshqa shakl kelsa, bu ham
    # noto'g'ri manzil belgisi: "yuborildi" deb aldab qo'ymaymiz.
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise SmsError("bad_api_url", _BAD_URL, resp.status_code)
    return data[0]


#: Fonga yuborilgan vazifalar — GC yig'ishtirib ketmasligi uchun
_tasks: set[asyncio.Task] = set()


def _schedule(api_key: str, phone: str, text: str, what: str) -> None:
    """SMS'ni fonda yuboradi — chaqiruvchi javobni kutmaydi."""

    async def runner() -> None:
        try:
            message = await send_sms(api_key, phone, text)
            logger.info(
                "SMS navbatga qo'yildi (%s): %s, xabar #%s",
                what, phone, message.get("id"),
            )
        except SmsError as exc:
            # Kutilgan sabab (kalit, limit, manzil) — bir qatorda, izsiz
            logger.warning(
                "SMS yuborilmadi (%s): %s — [%s] %s", what, phone, exc.code, exc
            )
        except Exception:
            # SMS xatosi asosiy oqimni buzmaydi — faqat log
            logger.warning("SMS yuborilmadi (%s): %s", what, phone, exc_info=True)

    try:
        task = asyncio.get_running_loop().create_task(runner())
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except RuntimeError:
        # Event loop yo'q (sinxron test kontekstlari) — jim o'tamiz
        logger.debug("SMS rejalashtirilmadi: event loop yo'q")


# --- Hodisalar ----------------------------------------------------------

async def _branch_key_and_phone(session: AsyncSession, reservation):
    """Filial kaliti + mehmon telefoni; ikkalasi bo'lmasa (None, None, None)."""
    from app.infrastructure.database.models.branch import Branch
    from app.infrastructure.database.models.guest import Guest

    branch = await session.get(Branch, reservation.branch_id)
    stored = getattr(branch, "sms_api_key", None) if branch else None
    key = decrypt_key(stored) if stored else None
    if not key:
        return None, None, None
    guest = await session.get(Guest, reservation.guest_id)
    phone = normalize_phone(getattr(guest, "phone", None) if guest else None)
    if not phone:
        return None, None, None
    return key, phone, branch


async def notify_booking_created(session: AsyncSession, reservation) -> None:
    """Bron yaratilganda mijozga tasdiqlash SMS'i (kalit bo'lsa)."""
    try:
        key, phone, branch = await _branch_key_and_phone(session, reservation)
        if not key:
            return
        from app.infrastructure.database.models.room import Room

        room = await session.get(Room, reservation.room_id)
        name = (branch.name if branch else None) or "Mehmonxona"
        parts = [
            f"{name}: bron tasdiqlandi. №{reservation.reservation_number}",
        ]
        if room is not None:
            parts.append(f"xona {room.room_number}")
        check_in = reservation.check_in_date
        parts.append(f"kirish {check_in.strftime('%d.%m.%Y')}")
        text = ", ".join(parts) + "."
        paid = float(reservation.paid_amount or 0)
        if paid > 0:
            text += f" Qabul qilingan to'lov: {_fmt_amount(paid)} so'm."
        _schedule(key, phone, text, "bron")
    except Exception:
        logger.warning("Bron SMS'ini tayyorlab bo'lmadi", exc_info=True)


async def notify_payment(session: AsyncSession, reservation, amount: float) -> None:
    """To'lov qabul qilinganda mijozga kvitansiya SMS'i (kalit bo'lsa)."""
    try:
        if not amount or amount <= 0:
            return
        key, phone, branch = await _branch_key_and_phone(session, reservation)
        if not key:
            return
        name = (branch.name if branch else None) or "Mehmonxona"
        text = (
            f"{name}: to'lov qabul qilindi — {_fmt_amount(amount)} so'm. "
            f"Bron №{reservation.reservation_number}."
        )
        total = float(reservation.total_amount or 0)
        paid = float(reservation.paid_amount or 0)
        if total > 0 and paid + 0.01 >= total:
            text += " Hisob to'liq yopildi. Rahmat!"
        _schedule(key, phone, text, "to'lov")
    except Exception:
        logger.warning("To'lov SMS'ini tayyorlab bo'lmadi", exc_info=True)


async def notify_invoice_payment(session: AsyncSession, invoice_id, amount: float) -> None:
    """Hisob-faktura Moliya bo'limidan to'langanda — u bronga tegishli
    bo'lsa, o'sha bron mijoziga kvitansiya SMS'i. Bronsiz faktura (do'kon
    va h.k.) jim o'tadi."""
    try:
        from app.infrastructure.database.models.invoice import Invoice
        from app.infrastructure.database.models.reservation import Reservation

        invoice = await session.get(Invoice, invoice_id)
        reservation_id = getattr(invoice, "reservation_id", None) if invoice else None
        if not reservation_id:
            return
        reservation = await session.get(Reservation, reservation_id)
        if reservation is None:
            return
        await notify_payment(session, reservation, amount)
    except Exception:
        logger.warning("Faktura to'lovi SMS'ini tayyorlab bo'lmadi", exc_info=True)

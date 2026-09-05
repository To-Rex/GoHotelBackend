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
    X-API-Key: xab_live_...
    {"to": ["+998901234567"], "text": "...", "priority": "transactional"}
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

async def send_sms(api_key: str, phone: str, text: str) -> None:
    """Bitta SMS yuboradi; muvaffaqiyatsiz javobda xato ko'taradi."""
    url = settings.SMS_API_BASE.rstrip("/") + "/public/messages"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            url,
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            json={"to": [phone], "text": text, "priority": "transactional"},
        )
    if resp.status_code >= 400:
        detail = resp.text[:300]
        raise RuntimeError(f"SMS API {resp.status_code}: {detail}")


#: Fonga yuborilgan vazifalar — GC yig'ishtirib ketmasligi uchun
_tasks: set[asyncio.Task] = set()


def _schedule(api_key: str, phone: str, text: str, what: str) -> None:
    """SMS'ni fonda yuboradi — chaqiruvchi javobni kutmaydi."""

    async def runner() -> None:
        try:
            await send_sms(api_key, phone, text)
            logger.info("SMS yuborildi (%s): %s", what, phone)
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

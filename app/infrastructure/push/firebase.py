"""Firebase Cloud Messaging (FCM) orqali push notification yuborish.

Bu modul BUTUNLAY IXTIYORIY va XAVFSIZ ishlaydi:
  * `firebase-admin` paketi o'rnatilmagan bo'lsa — jimgina no-op.
  * Kalit fayli topilmasa yoki FIREBASE_ENABLED=false bo'lsa — jimgina no-op.
  * Yuborishda tarmoq/xato bo'lsa — log yoziladi, lekin xato tashlanmaydi.

Shu tufayli push integratsiyasi mavjud biznes oqimlarining birortasini ham
buza olmaydi. Firebase Admin SDK'ning `messaging.send*` chaqiruvlari bloklovchi
(sync) bo'lgani uchun ular `asyncio.to_thread` orqali event-loopni bloklamay
bajariladi.
"""
from __future__ import annotations

import asyncio
import logging
import os
from threading import Lock

from app.core.config import settings

logger = logging.getLogger(__name__)

_app = None  # firebase_admin.App | None
_init_done = False
_lock = Lock()


def _init():
    """Firebase ilovasini bir marta lazy-init qiladi. Muvaffaqiyatsiz bo'lsa None."""
    global _app, _init_done
    if _init_done:
        return _app
    with _lock:
        if _init_done:
            return _app
        _init_done = True
        if not settings.FIREBASE_ENABLED:
            logger.info("Firebase push o'chirilgan (FIREBASE_ENABLED=false)")
            return None
        cred_path = settings.FIREBASE_CREDENTIALS_FILE
        if not cred_path or not os.path.isfile(cred_path):
            logger.warning(
                "Firebase kalit fayli topilmadi: %s — push o'chirildi", cred_path
            )
            return None
        try:
            import firebase_admin
            from firebase_admin import credentials

            cred = credentials.Certificate(cred_path)
            _app = firebase_admin.initialize_app(cred)
            logger.info("Firebase push ishga tushdi (project=%s)", cred.project_id)
        except Exception:
            logger.exception("Firebase init muvaffaqiyatsiz — push o'chirildi")
            _app = None
        return _app


def is_configured() -> bool:
    """Firebase yuborishga tayyor bo'lsa True (health-check / diagnostika uchun)."""
    return _init() is not None


def _send_sync(tokens: list[str], title: str, body: str | None, data: dict | None) -> int:
    """Berilgan tokenlarga push yuboradi. Muvaffaqiyatli yuborilganlar sonini qaytaradi."""
    app = _init()
    if app is None or not tokens:
        return 0
    try:
        from firebase_admin import messaging
    except Exception:
        logger.exception("firebase_admin.messaging import qilinmadi")
        return 0

    # FCM data payload faqat string qiymatlarni qabul qiladi
    str_data = {str(k): str(v) for k, v in (data or {}).items() if v is not None}
    notification = messaging.Notification(title=title, body=body or "")

    # Yangi (send_each_for_multicast) va eski SDK versiyalarini ham qo'llab-quvvatlaymiz
    try:
        multicast = messaging.MulticastMessage(
            tokens=tokens, notification=notification, data=str_data
        )
        send_multicast = getattr(messaging, "send_each_for_multicast", None) or getattr(
            messaging, "send_multicast", None
        )
        if send_multicast is not None:
            resp = send_multicast(multicast)
            _log_invalid_tokens(tokens, resp)
            return int(getattr(resp, "success_count", 0))
    except Exception:
        logger.exception("FCM multicast yuborish muvaffaqiyatsiz")

    # Zaxira yo'l: har bir tokenga alohida yuboramiz
    success = 0
    for token in tokens:
        try:
            messaging.send(
                messaging.Message(token=token, notification=notification, data=str_data)
            )
            success += 1
        except Exception:
            logger.warning("FCM yuborish muvaffaqiyatsiz (token=%s...)", token[:12])
    return success


def _log_invalid_tokens(tokens: list[str], batch_response) -> None:
    """Yaroqsiz/ro'yxatdan o'chgan tokenlarni logga yozadi (tozalash keyinroq)."""
    responses = getattr(batch_response, "responses", None)
    if not responses:
        return
    for token, r in zip(tokens, responses):
        if not getattr(r, "success", True):
            exc = getattr(r, "exception", None)
            logger.info("FCM token rad etildi (token=%s..., sabab=%s)", token[:12], exc)


async def send_push(
    tokens: list[str] | str | None,
    title: str,
    body: str | None = None,
    data: dict | None = None,
) -> int:
    """Bir yoki bir nechta FCM tokenga push yuboradi (bloklamaydi, xato tashlamaydi).

    Muvaffaqiyatli yuborilgan xabarlar sonini qaytaradi (0 = hech narsa yuborilmadi).
    """
    if isinstance(tokens, str):
        tokens = [tokens]
    tokens = [t for t in (tokens or []) if t]
    if not tokens:
        return 0
    try:
        return await asyncio.to_thread(_send_sync, tokens, title, body, data)
    except Exception:
        logger.exception("send_push kutilmagan xato")
        return 0

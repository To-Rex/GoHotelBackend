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
import base64
import binascii
import json
import logging
import os
from threading import Lock

from app.core.config import settings

logger = logging.getLogger(__name__)

_app = None  # firebase_admin.App | None
_init_done = False
_lock = Lock()
# Diagnostika uchun: init nima sababdan o'chganini eslab qolamiz (health endpoint).
_last_error: str | None = None


def _load_credential():
    """firebase_admin credentials.Certificate uchun kirish qiymatini tayyorlaydi.

    Ustuvorlik: FIREBASE_CREDENTIALS_JSON (env orqali xom JSON yoki base64) >
    FIREBASE_CREDENTIALS_FILE (diskdagi fayl). Topilmasa (None, sabab) qaytadi.
    """
    global _last_error
    from firebase_admin import credentials

    raw = settings.FIREBASE_CREDENTIALS_JSON
    if raw and raw.strip():
        text = raw.strip()
        # Xom JSON bo'lmasa, base64 sifatida ochishga urinamiz (env-varlar ko'pincha
        # ko'p qatorli JSON'ni base64'da saqlaydi).
        if not text.lstrip().startswith("{"):
            try:
                text = base64.b64decode(text).decode("utf-8")
            except (binascii.Error, ValueError, UnicodeDecodeError):
                _last_error = "FIREBASE_CREDENTIALS_JSON o'qib bo'lmadi (JSON ham, base64 ham emas)"
                logger.error(_last_error)
                return None
        try:
            info = json.loads(text)
        except json.JSONDecodeError:
            _last_error = "FIREBASE_CREDENTIALS_JSON yaroqsiz JSON"
            logger.error(_last_error)
            return None
        return credentials.Certificate(info)

    cred_path = settings.FIREBASE_CREDENTIALS_FILE
    if cred_path and os.path.isfile(cred_path):
        return credentials.Certificate(cred_path)

    _last_error = (
        f"Firebase kaliti topilmadi (fayl: {cred_path!r}, "
        f"FIREBASE_CREDENTIALS_JSON: bo'sh)"
    )
    logger.warning("%s — push o'chirildi", _last_error)
    return None


def _init():
    """Firebase ilovasini bir marta lazy-init qiladi. Muvaffaqiyatsiz bo'lsa None."""
    global _app, _init_done, _last_error
    if _init_done:
        return _app
    with _lock:
        if _init_done:
            return _app
        _init_done = True
        if not settings.FIREBASE_ENABLED:
            _last_error = "FIREBASE_ENABLED=false"
            logger.info("Firebase push o'chirilgan (FIREBASE_ENABLED=false)")
            return None
        try:
            import firebase_admin

            cred = _load_credential()
            if cred is None:
                return None
            _app = firebase_admin.initialize_app(cred)
            _last_error = None
            logger.info("Firebase push ishga tushdi (project=%s)", cred.project_id)
        except Exception as exc:
            _last_error = f"Firebase init muvaffaqiyatsiz: {exc}"
            logger.exception("Firebase init muvaffaqiyatsiz — push o'chirildi")
            _app = None
        return _app


def diagnostics() -> dict:
    """Push konfiguratsiyasi holati (health/diagnostika endpointi uchun)."""
    configured = _init() is not None
    source = "none"
    if settings.FIREBASE_CREDENTIALS_JSON and settings.FIREBASE_CREDENTIALS_JSON.strip():
        source = "env(FIREBASE_CREDENTIALS_JSON)"
    elif settings.FIREBASE_CREDENTIALS_FILE and os.path.isfile(
        settings.FIREBASE_CREDENTIALS_FILE
    ):
        source = f"file({settings.FIREBASE_CREDENTIALS_FILE})"
    return {
        "enabled": bool(settings.FIREBASE_ENABLED),
        "configured": configured,
        "credential_source": source,
        "error": _last_error,
    }


def is_configured() -> bool:
    """Firebase yuborishga tayyor bo'lsa True (health-check / diagnostika uchun)."""
    return _init() is not None


def _send_sync(
    tokens: list[str],
    title: str,
    body: str | None,
    data: dict | None,
    data_only: bool = False,
) -> int:
    """Berilgan tokenlarga push yuboradi. Muvaffaqiyatli yuborilganlar sonini qaytaradi.

    `data_only=True` — Android'da xabar tizim tomonidan EMAS, ilovaning fon
    ishlovchisi tomonidan ko'rsatiladi. Bu yangi vazifa kabi "budilnik"
    xabarlari uchun: tizim chizsa tovush bir marta chalinadi, ilova esa
    yopilmaguncha jiringlaydigan qilib ko'rsatadi. Sarlavha/matn data
    ichida ketadi; iOS uchun alert APNS'da baribir saqlanadi.
    """
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
    if data_only:
        str_data.setdefault("title", title)
        if body:
            str_data.setdefault("body", body)
        notification = None
    else:
        notification = messaging.Notification(title=title, body=body or "")

    # Android: YUQORI prioritet — heads-up banner darhol ko'rinsin (Doze/battery
    # optimizatsiyada ham). iOS (APNS): alert + tovush.
    android_cfg = messaging.AndroidConfig(
        priority="high",
        notification=None
        if data_only
        else messaging.AndroidNotification(
            title=title,
            body=body or "",
            sound="default",
            default_sound=True,
            # Flutter default kanali — ko'p ilovalarda mavjud. Kanal bo'lmasa ham
            # tizim standart kanaldan foydalanadi.
            channel_id="high_importance_channel",
        ),
    )
    apns_cfg = messaging.APNSConfig(
        headers={"apns-priority": "10"},
        payload=messaging.APNSPayload(
            aps=messaging.Aps(
                alert=messaging.ApsAlert(title=title, body=body or ""),
                sound="default",
                content_available=True,
            )
        ),
    )

    # Yangi (send_each_for_multicast) va eski SDK versiyalarini ham qo'llab-quvvatlaymiz
    try:
        multicast = messaging.MulticastMessage(
            tokens=tokens,
            notification=notification,
            data=str_data,
            android=android_cfg,
            apns=apns_cfg,
        )
        send_multicast = getattr(messaging, "send_each_for_multicast", None) or getattr(
            messaging, "send_multicast", None
        )
        if send_multicast is not None:
            resp = send_multicast(multicast)
            _log_invalid_tokens(tokens, resp)
            success = int(getattr(resp, "success_count", 0))
            logger.info(
                "FCM multicast: %s/%s muvaffaqiyatli yuborildi", success, len(tokens)
            )
            return success
    except Exception:
        logger.exception("FCM multicast yuborish muvaffaqiyatsiz")

    # Zaxira yo'l: har bir tokenga alohida yuboramiz
    success = 0
    for token in tokens:
        try:
            messaging.send(
                messaging.Message(
                    token=token,
                    notification=notification,
                    data=str_data,
                    android=android_cfg,
                    apns=apns_cfg,
                )
            )
            success += 1
        except Exception:
            logger.warning("FCM yuborish muvaffaqiyatsiz (token=%s...)", token[:12])
    logger.info("FCM (fallback): %s/%s muvaffaqiyatli yuborildi", success, len(tokens))
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
    data_only: bool = False,
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
        return await asyncio.to_thread(_send_sync, tokens, title, body, data, data_only)
    except Exception:
        logger.exception("send_push kutilmagan xato")
        return 0

"""Fon rejalashtiruvchisi — bron chiqishini avtomatlashtirish uchun.

FastAPI lifespan'da bitta asyncio vazifasi ishga tushiriladi. Har
`AUTO_CHECKOUT_INTERVAL_SECONDS` soniyada yangi DB sessiya ochib, AutomationService
tik'ini bajaradi. Har tik alohida sessiyada — bir tikdagi xato keyingisiga
ta'sir qilmaydi. uvicorn bitta jarayonda ishlaydi (Procfile), shuning uchun
rejalashtiruvchi dublikat bo'lmaydi.
"""
from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.core.database import _get_session_factory

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None
_purge_task: asyncio.Task | None = None

#: Muddati o'tgan yuz ko'rinishlarini tozalash oralig'i. Soatiga bir marta
#: yetarli: yozuvlar 12 soat yashaydi va bir necha soat ortiqcha turishi
#: hech narsani buzmaydi, har daqiqada DELETE yugurtirish esa behuda.
SIGHTING_PURGE_INTERVAL_SECONDS = 3600


async def _run_loop() -> None:
    from app.application.services.automation_service import AutomationService

    interval = max(10, settings.AUTO_CHECKOUT_INTERVAL_SECONDS)
    logger.info("Auto-checkout scheduler started (interval=%ss)", interval)
    while True:
        try:
            await asyncio.sleep(interval)
            factory = _get_session_factory()
            async with factory() as session:
                await AutomationService(session).run_tick()
        except asyncio.CancelledError:
            logger.info("Auto-checkout scheduler stopping")
            break
        except Exception:
            # Bitta tik xatosi loop'ni to'xtatmaydi
            logger.exception("Auto-checkout scheduler tick failed")



async def _purge_sightings_loop() -> None:
    """Muddati o'tgan yuz ko'rinishlarini o'chiradi.

    Bu shunchaki joy tozalash emas, balki biometrik saqlash siyosatining
    bajarilishi: ko'rinishda mehmonning surati va vektori bo'lishi mumkin,
    va ular ``expires_at`` dan keyin turmasligi kerak. Shuning uchun bu
    vazifa avtomatlashtirish o'chirilgan bo'lsa ham ishlaydi.
    """
    from datetime import datetime, timezone

    from sqlalchemy import delete

    from app.infrastructure.database.models.face_sighting import FaceSighting

    logger.info(
        "Yuz ko'rinishlarini tozalash rejalashtiruvchisi ishga tushdi (interval=%ss)",
        SIGHTING_PURGE_INTERVAL_SECONDS,
    )
    while True:
        try:
            await asyncio.sleep(SIGHTING_PURGE_INTERVAL_SECONDS)
            factory = _get_session_factory()
            async with factory() as session:
                result = await session.execute(
                    delete(FaceSighting).where(
                        FaceSighting.expires_at < datetime.now(timezone.utc)
                    )
                )
                await session.commit()
                if result.rowcount:
                    logger.info(
                        "Muddati o'tgan %d ta yuz ko'rinishi o'chirildi", result.rowcount
                    )
        except asyncio.CancelledError:
            logger.info("Yuz ko'rinishlarini tozalash to'xtatilmoqda")
            break
        except Exception:
            logger.exception("Yuz ko'rinishlarini tozalash tiki muvaffaqiyatsiz")


def start_scheduler() -> None:
    global _task, _purge_task
    # Tozalash avtomatlashtirishdan MUSTAQIL: u ma'lumot saqlash muddatini
    # ta'minlaydi, bron chiqishini emas.
    if _purge_task is None:
        _purge_task = asyncio.create_task(_purge_sightings_loop())
    if not settings.AUTO_CHECKOUT_ENABLED:
        logger.info("Auto-checkout scheduler disabled (AUTO_CHECKOUT_ENABLED=false)")
        return
    if _task is not None:
        return
    _task = asyncio.create_task(_run_loop())


async def stop_scheduler() -> None:
    global _task, _purge_task
    for task in (_task, _purge_task):
        if task is None:
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _task = None
    _purge_task = None

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


def start_scheduler() -> None:
    global _task
    if not settings.AUTO_CHECKOUT_ENABLED:
        logger.info("Auto-checkout scheduler disabled (AUTO_CHECKOUT_ENABLED=false)")
        return
    if _task is not None:
        return
    _task = asyncio.create_task(_run_loop())


async def stop_scheduler() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None

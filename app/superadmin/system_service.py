"""Tizim holati — panelning "Tizim holati" sahifasi uchun.

Egasi bir qarashda ko'radi: baza (versiya, hajm, ulanishlar), MinIO
(ulanadimi, bucket'lar), push (Firebase sozlanganmi), rejalashtiruvchi
(avto-chiqish yoqilganmi), dastur versiyasi va ishlab turgan vaqti,
asosiy jadvallardagi yozuvlar soni. Hech narsa o'zgartirilmaydi.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

#: Jarayon ishga tushgan payt (modul yuklanganda)
STARTED_AT = time.time()

#: Sanaladigan jadvallar — egasi uchun ma'noli bo'lganlari
COUNT_TABLES = (
    "hotels", "branches", "users", "guests", "reservations", "payments",
    "shop_sales", "housekeeping_tasks", "notifications", "audit_logs",
    "face_sightings", "app_releases",
)


class SystemService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _database(self) -> dict:
        try:
            version = (await self.session.execute(text("SHOW server_version"))).scalar()
            size = (await self.session.execute(
                text("SELECT pg_size_pretty(pg_database_size(current_database()))")
            )).scalar()
            connections = (await self.session.execute(
                text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
            )).scalar()
            head = (await self.session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
            counts = {}
            for table in COUNT_TABLES:
                counts[table] = int(
                    (await self.session.execute(text(f'SELECT count(*) FROM "{table}"'))).scalar() or 0
                )
            open_shifts = int((await self.session.execute(
                text("SELECT count(*) FROM shift_sessions WHERE status IN ('ACTIVE','PENDING_HANDOVER')")
            )).scalar() or 0)
            live_sessions = int((await self.session.execute(
                text("SELECT count(*) FROM user_sessions WHERE revoked_at IS NULL AND expires_at > now()")
            )).scalar() or 0)
            return {
                "ok": True,
                "version": version,
                "size": size,
                "connections": int(connections or 0),
                "migration": head,
                "counts": counts,
                "open_shifts": open_shifts,
                "live_sessions": live_sessions,
            }
        except Exception as exc:  # noqa: BLE001 — holat sahifasi yiqilmasin
            return {"ok": False, "error": str(exc)[:300]}

    async def _storage(self) -> dict:
        from app.infrastructure.storage import minio as storage

        def probe() -> dict:
            client = storage.get_minio_client()
            buckets = []
            for name in (settings.MINIO_BUCKET_DOCUMENTS, settings.MINIO_BUCKET_GUESTS):
                buckets.append({"name": name, "exists": bool(client.bucket_exists(name))})
            return {"ok": True, "endpoint": settings.MINIO_ENDPOINT, "secure": settings.MINIO_SECURE, "buckets": buckets}

        try:
            return await asyncio.wait_for(asyncio.to_thread(probe), timeout=8)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "endpoint": settings.MINIO_ENDPOINT, "error": str(exc)[:300]}

    async def _push(self) -> dict:
        try:
            from app.superadmin.push_config_service import PushConfigService

            status = await PushConfigService(self.session).status()
            ok = any(bool(status.get(k)) for k in ("initialized", "active", "ready", "configured", "panel_key_readable"))
            return {"ok": ok, **status}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)[:300]}

    def _scheduler(self) -> dict:
        from app.core import scheduler

        return {
            "auto_checkout_enabled": bool(settings.AUTO_CHECKOUT_ENABLED),
            "interval_seconds": int(settings.AUTO_CHECKOUT_INTERVAL_SECONDS),
            "grace_minutes": int(settings.AUTO_CHECKOUT_GRACE_MINUTES),
            "running": getattr(scheduler, "_task", None) is not None,
        }

    async def snapshot(self) -> dict:
        now = time.time()
        return {
            "app": {
                "version": settings.APP_VERSION,
                "env": settings.APP_ENV,
                "uptime_seconds": int(now - STARTED_AT),
                "server_time": datetime.now(timezone.utc).isoformat(),
                "tz_offset_minutes": int(settings.APP_TZ_OFFSET_MINUTES),
            },
            "database": await self._database(),
            "storage": await self._storage(),
            "push": await self._push(),
            "scheduler": self._scheduler(),
        }

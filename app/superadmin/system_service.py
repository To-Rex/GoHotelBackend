"""Tizim holati — panelning "Tizim holati" sahifasi uchun.

Egasi bir qarashda ko'radi: baza (versiya, hajm, ulanishlar, bloklar),
MinIO (ulanadimi, bucket'lar), push (Firebase sozlanganmi), rejalashtiruvchi
(avto-chiqish yoqilganmi), dastur versiyasi va ishlab turgan vaqti,
asosiy jadvallardagi yozuvlar soni hamda ma'lumotlardagi konfliktlar.
Hech narsa o'zgartirilmaydi — faqat o'qish.
"""
from __future__ import annotations

import asyncio
import os
import platform
import shutil
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
    "invoices", "expenses", "shift_sessions", "user_sessions", "trusted_devices",
    "reservation_penalties", "guest_feedback", "shop_sales",
    "housekeeping_tasks", "notifications", "audit_logs", "face_sightings",
    "app_releases", "panel_users",
)

#: Ma'lumotlardagi mumkin bo'lgan konfliktlar — har biri bitta skalyar.
CONFLICT_SQL: dict[str, str] = {
    # PostgreSQL: kimnidir bloklab turgan so'rovlar
    "blocked_queries": (
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND cardinality(pg_blocking_pids(pid)) > 0"
    ),
    # 30 soniyadan uzoq davom etayotgan faol so'rovlar (o'zimizdan tashqari)
    "long_running_queries": (
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND state = 'active' "
        "AND pid <> pg_backend_pid() AND query_start IS NOT NULL "
        "AND now() - query_start > interval '30 seconds'"
    ),
    # 5 daqiqadan ko'p "idle in transaction" — ulanishni ushlab turadi
    "idle_in_transactions": (
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND state LIKE 'idle in transaction%' "
        "AND pid <> pg_backend_pid() AND now() - state_change > interval '5 minutes'"
    ),
    # Bir xonada ustma-ust kunlik bronlar (faol statuslar, oxirgi 30 kun)
    "double_bookings": (
        "SELECT count(*) FROM reservations a "
        "JOIN reservations b ON b.room_id = a.room_id AND b.id > a.id "
        "WHERE a.is_deleted = false AND b.is_deleted = false "
        "AND a.status IN ('PENDING','CONFIRMED','CHECKED_IN') "
        "AND b.status IN ('PENDING','CONFIRMED','CHECKED_IN') "
        "AND COALESCE(a.booking_type, 'DAILY') <> 'HOURLY' "
        "AND COALESCE(b.booking_type, 'DAILY') <> 'HOURLY' "
        "AND a.check_in_date < b.check_out_date "
        "AND b.check_in_date < a.check_out_date "
        "AND a.check_out_date >= current_date - interval '30 days'"
    ),
    # Bir xonada ustma-ust soatlik bronlar
    "hourly_overlaps": (
        "SELECT count(*) FROM reservations a "
        "JOIN reservations b ON b.room_id = a.room_id AND b.id > a.id "
        "WHERE a.is_deleted = false AND b.is_deleted = false "
        "AND a.booking_type = 'HOURLY' AND b.booking_type = 'HOURLY' "
        "AND a.status IN ('PENDING','CONFIRMED','CHECKED_IN') "
        "AND b.status IN ('PENDING','CONFIRMED','CHECKED_IN') "
        "AND a.check_in_datetime IS NOT NULL AND a.check_out_datetime IS NOT NULL "
        "AND b.check_in_datetime IS NOT NULL AND b.check_out_datetime IS NOT NULL "
        "AND a.check_in_datetime < b.check_out_datetime "
        "AND b.check_in_datetime < a.check_out_datetime "
        "AND a.check_out_datetime >= now() - interval '30 days'"
    ),
    # Xona "band", lekin unda CHECKED_IN mehmon yo'q — holat eskirgan
    "stale_occupied_rooms": (
        "SELECT count(*) FROM rooms r "
        "WHERE r.is_deleted = false AND r.current_status = 'OCCUPIED' "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM reservations res WHERE res.room_id = r.id "
        "  AND res.status = 'CHECKED_IN' AND res.is_deleted = false"
        ")"
    ),
    # Mehmon kirgan, xona esa band holatida emas
    "checked_in_rooms_not_occupied": (
        "SELECT count(*) FROM reservations res "
        "JOIN rooms r ON r.id = res.room_id "
        "WHERE res.status = 'CHECKED_IN' AND res.is_deleted = false "
        "AND r.is_deleted = false AND r.current_status <> 'OCCUPIED'"
    ),
    # Arxivdagi (o'chirilgan) xonaga faol bron bog'langan
    "active_in_archived_rooms": (
        "SELECT count(*) FROM reservations res "
        "JOIN rooms r ON r.id = res.room_id "
        "WHERE res.is_deleted = false AND r.is_deleted = true "
        "AND res.status IN ('PENDING','CONFIRMED','CHECKED_IN')"
    ),
    # Faol bo'lmagan / o'chirilgan xodimda ochiq smena
    "open_shifts_inactive_staff": (
        "SELECT count(*) FROM shift_sessions s "
        "JOIN users u ON u.id = s.user_id "
        "WHERE s.status IN ('ACTIVE','PENDING_HANDOVER') "
        "AND (u.status <> 'ACTIVE' OR u.is_deleted = true)"
    ),
    # Kassa topshirilgan, admin tasdig'i kutib qolgan
    "pending_handovers": (
        "SELECT count(*) FROM shift_sessions WHERE status = 'PENDING_HANDOVER'"
    ),
    # To'langan summa hisob-faktura jamidan katta
    "overpaid_invoices": (
        "SELECT count(*) FROM invoices "
        "WHERE is_deleted = false AND paid_amount > total_amount + 0.01"
    ),
    # Bron bo'yicha to'lov jamidan katta
    "overpaid_reservations": (
        "SELECT count(*) FROM reservations "
        "WHERE is_deleted = false AND status <> 'CANCELLED' "
        "AND paid_amount > total_amount + 0.01"
    ),
    # 24 soatdan beri yopilmagan xo'jalik vazifalari
    "stuck_tasks": (
        "SELECT count(*) FROM housekeeping_tasks "
        "WHERE is_deleted = false AND status IN ('OPEN','IN_PROGRESS') "
        "AND created_at < now() - interval '24 hours'"
    ),
    # Xona filiali boshqa mehmonxonaga tegishli
    "room_branch_mismatch": (
        "SELECT count(*) FROM rooms r JOIN branches b ON b.id = r.branch_id "
        "WHERE r.hotel_id <> b.hotel_id"
    ),
    # Xodim filiali boshqa mehmonxonaga tegishli
    "user_branch_mismatch": (
        "SELECT count(*) FROM users u JOIN branches b ON b.id = u.branch_id "
        "WHERE u.branch_id IS NOT NULL AND u.hotel_id <> b.hotel_id"
    ),
}


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class SystemService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _scalar(self, sql: str):
        try:
            return (await self.session.execute(text(sql))).scalar()
        except Exception:  # noqa: BLE001 — bitta tekshiruv sahifani yiqitmasin
            return None

    async def _table_count(self, table: str) -> int | None:
        return _int(await self._scalar(f'SELECT count(*) FROM "{table}"'))

    async def _database(self) -> dict:
        try:
            version = (await self.session.execute(text("SHOW server_version"))).scalar()
            size = (await self.session.execute(
                text("SELECT pg_size_pretty(pg_database_size(current_database()))")
            )).scalar()
            connections = (await self.session.execute(
                text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
            )).scalar()
            max_connections = (await self.session.execute(text("SHOW max_connections"))).scalar()
            active_connections = (await self.session.execute(
                text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND state = 'active'")
            )).scalar()
            postmaster_started = (await self.session.execute(
                text("SELECT pg_postmaster_start_time()")
            )).scalar()
            head = (await self.session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
            counts = {}
            for table in COUNT_TABLES:
                counts[table] = await self._table_count(table)
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
                "active_connections": int(active_connections or 0),
                "max_connections": _int(max_connections),
                "migration": head,
                "postmaster_started_at": postmaster_started.isoformat() if postmaster_started else None,
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

    async def _diagnostics(self) -> dict:
        conflicts = {key: _int(await self._scalar(sql)) for key, sql in CONFLICT_SQL.items()}

        try:
            disk = shutil.disk_usage(os.getcwd())
            disk_total, disk_used, disk_free = disk.total, disk.used, disk.free
            disk_percent = round(disk_used / disk_total * 100, 1) if disk_total else None
        except Exception:  # noqa: BLE001
            disk_total = disk_used = disk_free = disk_percent = None

        try:
            load_1m, load_5m, load_15m = os.getloadavg()
        except (AttributeError, OSError):
            load_1m = load_5m = load_15m = None

        return {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "conflicts": conflicts,
            "capacity": {
                "disk_total": disk_total,
                "disk_used": disk_used,
                "disk_free": disk_free,
                "disk_percent": disk_percent,
                "load_1m": round(load_1m, 2) if load_1m is not None else None,
                "load_5m": round(load_5m, 2) if load_5m is not None else None,
                "load_15m": round(load_15m, 2) if load_15m is not None else None,
            },
            "runtime": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "pid": os.getpid(),
            },
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
            "diagnostics": await self._diagnostics(),
        }

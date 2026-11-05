from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base


class ApiErrorLog(Base):
    """Panel so'rovlar jurnalidagi XATO yozuvlari (4xx/5xx).

    Jonli jurnal xotirada turadi va server qayta ko'tarilganda (har
    deploy'da) bo'shaydi — muammo yuz bergan paytdagi so'rov yo'qolib
    qolardi. Xatolar shu jadvalga ham yoziladi va
    `API_ERROR_LOG_RETENTION_DAYS` kun (standart 3) saqlanadi, keyin
    o'z-o'zidan o'chadi (app/superadmin/api_log.py).

    Ataylab hech qanday FK yo'q: mehmonxona, filial yoki foydalanuvchi
    o'chirilsa ham jurnal qoladi. Ustunlar — jurnal yozuvidagi matnlar
    aynan (sirlar allaqachon niqoblangan).
    """

    __tablename__ = "api_error_logs"

    #: Jurnal yozuvining kaliti (uuid hex) — xotiradagi nusxasi bilan bir xil,
    #: panel ikkalasini birlashtirganda takrorlanmasligi uchun
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    request_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: Ishlov berilmagan xatoning to'liq traceback'i (500/503/409 ga aylanganlar)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    hotel_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    branch_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    user_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

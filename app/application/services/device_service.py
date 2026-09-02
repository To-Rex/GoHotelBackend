"""Qurilma tasdiqlash.

Login va parol o'g'irlansa, ular istalgan kompyuterdan ishlayverardi. Endi
xodim faqat administrator tasdiqlagan qurilmadan kira oladi.

Qurilma brauzer yaratgan tasodifiy ID bilan taniladi. Bu mukammal emas —
ID nusxalanishi mumkin — lekin u parol bilan BIRGA ishlaydi, ya'ni
o'g'irlangan parolning o'zi yetarli bo'lmay qoladi.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.trusted_device import TrustedDevice

logger = logging.getLogger(__name__)

#: Bu turdagi foydalanuvchilar qurilma tekshiruvidan ozod.
#:
#: Sabab oddiy: tasdiqlaydigan odamning o'zi tasdiq kutib qolsa, tizimga
#: hech kim kira olmay qolardi — yangi mehmonxonada birinchi kirish ham
#: mumkin bo'lmasdi. Administrator baribir login, parol va (biriktirgan
#: bo'lsa) yuz tekshiruvidan o'tadi.
DEVICE_CHECK_EXEMPT_TYPES = ("ADMIN", "SUPER_ADMIN")

VALID_STATUSES = ("PENDING", "APPROVED", "BLOCKED")


class DeviceService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def ensure_allowed(
        self,
        user,
        device_id: str | None,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Shu qurilmadan kirishga ruxsat bormi. Bo'lmasa xato ko'taradi.

        Noma'lum qurilma jimgina rad etilmaydi — u ro'yxatga PENDING
        holatida yoziladi, ya'ni administrator uni ko'radi va bir bosishda
        tasdiqlay oladi. Aks holda xodim "nega kira olmayapman" deb
        qolardi, administrator esa nimani tasdiqlashni bilmasdi.
        """
        if user.user_type in DEVICE_CHECK_EXEMPT_TYPES:
            return
        if not user.hotel_id:
            # Mehmonxonasiz xodim bo'lmaydi, lekin bo'lsa — qurilma
            # ro'yxati ham bo'lmaydi, tekshirib bo'lmaydi
            return

        if not device_id:
            raise ForbiddenException(
                "Qurilma aniqlanmadi. Brauzer ma'lumotlarini saqlashga ruxsat "
                "bering yoki administratorga murojaat qiling",
                "DEVICE_UNKNOWN",
            )

        device = (
            await self.session.execute(
                select(TrustedDevice).where(
                    TrustedDevice.hotel_id == user.hotel_id,
                    TrustedDevice.device_id == device_id,
                )
            )
        ).scalar_one_or_none()

        now = datetime.now(timezone.utc)
        if device is None:
            device = TrustedDevice(
                hotel_id=user.hotel_id,
                device_id=device_id,
                status="PENDING",
                user_agent=user_agent,
                ip_address=ip_address,
                last_user_id=user.id,
                last_seen_at=now,
            )
            self.session.add(device)
            await self.session.flush()
            logger.info(
                "Yangi qurilma tasdiq kutmoqda: %s (%s)", device_id, user.username
            )
        else:
            # Har urinishda yangilanadi — administrator qurilmaning oxirgi
            # holatini va kim urinayotganini ko'radi
            device.last_seen_at = now
            device.last_user_id = user.id
            if user_agent:
                device.user_agent = user_agent
            if ip_address:
                device.ip_address = ip_address
            await self.session.flush()

        if device.status == "APPROVED":
            return

        # RAD ETISHDAN OLDIN SAQLAYMIZ.
        #
        # `get_db` har xatoda sessiyani rollback qiladi, ya'ni bu yerda
        # ko'targan xato yuqoridagi yozuvni ham bekor qilardi: qurilma
        # ro'yxatga umuman tushmasdi va administrator nimani
        # tasdiqlashini bilmasdi. Shuning uchun urinish alohida
        # commit bilan qayd etiladi.
        await self.session.commit()

        if device.status == "BLOCKED":
            raise ForbiddenException(
                "Bu qurilmadan kirish taqiqlangan. Administratorga murojaat qiling",
                "DEVICE_BLOCKED",
            )

        raise ForbiddenException(
            "Yangi qurilma. Administrator tasdiqlagach kira olasiz",
            "DEVICE_PENDING",
        )

    async def assert_session_allowed(
        self,
        hotel_id,
        device_id: str | None,
        user_type: str,
    ) -> None:
        """Ochiq sessiya davom etishi mumkinmi.

        Tekshiruv faqat kirishda bo'lsa yetarli emasdi: administrator
        qurilmani taqiqlasa yoki ro'yxatdan o'chirsa, o'sha qurilmada
        allaqachon kirgan xodim bemalol ishlab yuraverardi — token ikki
        soat, refresh bilan esa undan ham uzoq yashaydi.

        Endi har so'rovda tekshiriladi. So'rov bitta indeksli qidiruv, ya'ni
        qimmat emas.
        """
        if user_type in DEVICE_CHECK_EXEMPT_TYPES or not hotel_id or not device_id:
            return

        device = (
            await self.session.execute(
                select(TrustedDevice.status).where(
                    TrustedDevice.hotel_id == hotel_id,
                    TrustedDevice.device_id == device_id,
                )
            )
        ).scalar_one_or_none()

        if device is None:
            # Ro'yxatdan o'chirilgan — sessiya ham tugaydi
            raise ForbiddenException(
                "Bu qurilma ro'yxatdan o'chirilgan. Qaytadan kiring",
                "DEVICE_REVOKED",
            )
        if device == "BLOCKED":
            raise ForbiddenException(
                "Bu qurilmadan kirish taqiqlangan. Administratorga murojaat qiling",
                "DEVICE_BLOCKED",
            )
        if device != "APPROVED":
            raise ForbiddenException(
                "Qurilma tasdig'i bekor qilingan. Administrator tasdiqlagach ishlay olasiz",
                "DEVICE_PENDING",
            )

    async def list_devices(
        self, hotel_id: UUID, status: str | None = None
    ) -> list[TrustedDevice]:
        stmt = select(TrustedDevice).where(TrustedDevice.hotel_id == hotel_id)
        if status:
            stmt = stmt.where(TrustedDevice.status == status)
        # Tasdiq kutayotganlar birinchi — administrator aynan ular uchun
        # bu sahifaga kiradi
        stmt = stmt.order_by(
            (TrustedDevice.status != "PENDING"),
            TrustedDevice.last_seen_at.desc().nullslast(),
            TrustedDevice.first_seen_at.desc(),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def set_status(
        self,
        device_pk: UUID,
        hotel_id: UUID,
        status: str,
        user_id: UUID,
        label: str | None = None,
    ) -> TrustedDevice:
        if status not in VALID_STATUSES:
            raise ForbiddenException(f"Noto'g'ri holat: {status}", "INVALID_STATUS")
        device = (
            await self.session.execute(
                select(TrustedDevice).where(
                    TrustedDevice.id == device_pk,
                    TrustedDevice.hotel_id == hotel_id,
                )
            )
        ).scalar_one_or_none()
        if device is None:
            raise NotFoundException("Device not found", "DEVICE_NOT_FOUND")

        device.status = status
        if label is not None:
            device.label = label.strip() or None
        if status == "APPROVED":
            device.approved_by = user_id
            device.approved_at = datetime.now(timezone.utc)
        await self.session.flush()
        logger.info("Qurilma holati: %s -> %s", device.device_id, status)
        return device

    async def delete_device(self, device_pk: UUID, hotel_id: UUID) -> None:
        """Qurilmani ro'yxatdan o'chiradi.

        O'chirilgan qurilma keyingi urinishda YANGI sifatida qaytadi va
        yana tasdiq kutadi — ya'ni o'chirish ruxsatni bekor qilish demak.
        """
        device = (
            await self.session.execute(
                select(TrustedDevice).where(
                    TrustedDevice.id == device_pk,
                    TrustedDevice.hotel_id == hotel_id,
                )
            )
        ).scalar_one_or_none()
        if device is None:
            raise NotFoundException("Device not found", "DEVICE_NOT_FOUND")
        await self.session.delete(device)
        await self.session.flush()

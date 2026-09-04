"""Telefonda skanerlangan hujjatni qabulxona ekraniga uzatish.

Oqim: resepsiya xodimi telefonda mehmonning hujjatini suratga oladi →
server uni o'qiydi → yozuv shu yerda turadi → veb ekrani uni ko'rib
yangi bandlov oynasini o'zi ochadi.

Ikki qaror shu faylda:

1. **Mehmon HUJJAT RAQAMI bo'yicha qidiriladi**, ism bo'yicha emas.
   Ism ikki hujjatda turlicha yozilishi mumkin (lotin/kirill,
   qisqartma), raqam esa yagona. Solishtirishdan oldin ikkala tomon
   ham harf-raqamdan boshqasidan tozalanadi: "AA 1234567" va
   "AA1234567" bitta hujjat.

2. **Mehmon topilmasa yozuv baribir saqlanadi.** Bu yangi mijoz —
   maydonlari o'qilgan holda bandlov oynasiga tushadi va xodim ularni
   qaytadan terib o'tirmaydi.

Rasm saqlanmaydi (`document_scan` modelidagi izohga qarang).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ValidationException
from app.infrastructure.database.models.document_scan import DocumentScan
from app.infrastructure.database.models.guest import Guest

#: Skanerdan olinadigan maydonlar — javobning qolgani (tekshiruvlar,
#: ogohlantirishlar) `fields` ichida saqlanadi.
NAME_FIELDS = ("firstName", "lastName")

#: Qidiruvda ishlatiladigan eng qisqa raqam. Bundan qisqasi tasodifan
#: boshqa mehmonga tushib qolishi mumkin.
MIN_NUMBER_LENGTH = 5

#: Ro'yxatda ko'rinadigan oyna. Qo'ng'iroqdan uzunroq: xodim hujjatni
#: skanerlab, mehmon bilan gaplashib, keyin kompyuterga o'tadi.
DEFAULT_WINDOW_MINUTES = 60

#: Bir xil hujjat shu vaqt ichida qayta skanerlansa yangi yozuv
#: ochilmaydi — xodim rasm sifatsiz chiqdi deb qayta urinsa, veb
#: ekranida ikkita bir xil oyna ochilib ketardi.
DEDUPE_SECONDS = 90


def normalize_number(value: str | None) -> str:
    """Hujjat raqamini solishtirish uchun shaklga keltiradi."""
    return "".join(ch for ch in (value or "") if ch.isalnum()).upper()


def _normalized_column(column):
    """Ustundagi raqamdan ajratgichlarni olib tashlaydi va katta harfga."""
    expression = func.coalesce(column, "")
    for separator in (" ", "-", "/", ".", "№", "#"):
        expression = func.replace(expression, separator, "")
    return func.upper(expression)


def full_name_of(document: dict) -> str | None:
    parts = [str(document.get(name) or "").strip() for name in NAME_FIELDS]
    name = " ".join(part for part in reversed(parts) if part)
    return name or None


class DocumentScanService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------- qidiruv --

    async def find_guest(
        self, document_number: str | None, personal_number: str | None = None
    ) -> Guest | None:
        """Hujjat raqami bo'yicha mehmon.

        Mehmonlar bazasi bu loyihada GLOBAL, shuning uchun mehmonxona
        bo'yicha filtr yo'q (`guest_service` dagi izohga qarang).
        """
        conditions = []
        number = normalize_number(document_number)
        if len(number) >= MIN_NUMBER_LENGTH:
            conditions.append(_normalized_column(Guest.passport_number) == number)
            conditions.append(_normalized_column(Guest.id_document_number) == number)
        personal = normalize_number(personal_number)
        if len(personal) >= MIN_NUMBER_LENGTH:
            conditions.append(_normalized_column(Guest.id_document_number) == personal)
        if not conditions:
            return None

        # Bir xil raqamli bir nechta yozuv bo'lsa oxirgisi olinadi:
        # eskisi ko'pincha to'liqsiz karta bo'ladi
        stmt = (
            select(Guest)
            .where(or_(*conditions))
            .order_by(desc(Guest.created_at))
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalars().first()

    # -------------------------------------------------------- yozish --

    async def record(
        self,
        hotel_id: UUID,
        document: dict,
        *,
        scanned_by: UUID | None = None,
        device_id: str | None = None,
    ) -> dict:
        """Skaner natijasini saqlaydi va topilgan mehmonni qaytaradi."""
        if not isinstance(document, dict) or not document:
            raise ValidationException("Skaner natijasi bo'sh", "EMPTY_SCAN")

        number = str(document.get("documentNumber") or "").strip() or None
        personal = str(document.get("personalNumber") or "").strip() or None
        now = datetime.now(timezone.utc)

        # TAKROR: bitta hujjat qayta skanerlansa oldingi yozuv qaytadi.
        # Faqat YOPILMAGANI hisobga olinadi — xodim oynani yopib, ataylab
        # qayta skanerlagan bo'lsa yangi yozuv ochilishi kerak.
        key = normalize_number(number)
        if len(key) >= MIN_NUMBER_LENGTH:
            recent = (
                await self.session.execute(
                    select(DocumentScan)
                    .where(
                        DocumentScan.hotel_id == hotel_id,
                        DocumentScan.document_number == key,
                        DocumentScan.created_at
                        >= now - timedelta(seconds=DEDUPE_SECONDS),
                        DocumentScan.acknowledged_at.is_(None),
                    )
                    .order_by(desc(DocumentScan.created_at))
                    .limit(1)
                )
            ).scalars().first()
            if recent is not None:
                return self._as_dict(recent, duplicate=True)

        guest = await self.find_guest(number, personal)
        scan = DocumentScan(
            hotel_id=hotel_id,
            document_type=str(document.get("documentType") or "ID_CARD"),
            fields=document,
            full_name=full_name_of(document),
            document_number=key or None,
            guest_id=guest.id if guest else None,
            guest_name=(
                f"{guest.first_name or ''} {guest.last_name or ''}".strip() or None
                if guest
                else None
            ),
            verified=bool(document.get("verified")),
            device_id=device_id,
            scanned_by=scanned_by,
        )
        self.session.add(scan)
        await self.session.flush()
        # `created_at` server tomonida to'ldiriladi
        await self.session.refresh(scan)
        return self._as_dict(scan)

    # -------------------------------------------------------- o'qish --

    async def recent(
        self,
        hotel_id: UUID,
        *,
        minutes: int = DEFAULT_WINDOW_MINUTES,
        include_acknowledged: bool = False,
        limit: int = 20,
    ) -> list[dict]:
        """Oxirgi skanerlar — veb ekrani shuni o'qiydi."""
        stmt = select(DocumentScan).where(
            DocumentScan.hotel_id == hotel_id,
            DocumentScan.created_at
            >= datetime.now(timezone.utc) - timedelta(minutes=minutes),
        )
        if not include_acknowledged:
            stmt = stmt.where(DocumentScan.acknowledged_at.is_(None))
        stmt = stmt.order_by(desc(DocumentScan.created_at)).limit(limit)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._as_dict(row) for row in rows]

    async def acknowledge(
        self, scan_id: UUID, hotel_id: UUID, user_id: UUID | None
    ) -> dict:
        scan = (
            await self.session.execute(
                select(DocumentScan).where(
                    DocumentScan.id == scan_id,
                    DocumentScan.hotel_id == hotel_id,
                )
            )
        ).scalar_one_or_none()
        if scan is None:
            raise NotFoundException("Skaner yozuvi topilmadi", "SCAN_NOT_FOUND")
        if scan.acknowledged_at is None:
            scan.acknowledged_at = datetime.now(timezone.utc)
            scan.acknowledged_by = user_id
            await self.session.flush()
        return self._as_dict(scan)

    @staticmethod
    def _as_dict(scan: DocumentScan, duplicate: bool = False) -> dict:
        return {
            "id": str(scan.id),
            "document_type": scan.document_type,
            "document_number": scan.document_number,
            "full_name": scan.full_name,
            "guest_id": str(scan.guest_id) if scan.guest_id else None,
            "guest_name": scan.guest_name,
            # Mehmon bazada topildimi — veb oynasi shunga qarab uni
            # tanlaydi yoki yangi mijoz maydonlarini to'ldiradi
            "matched": scan.guest_id is not None,
            "verified": bool(scan.verified),
            "document": scan.fields or {},
            "created_at": scan.created_at.isoformat() if scan.created_at else None,
            "acknowledged": scan.acknowledged_at is not None,
            #: Qurilmaga aytiladi: bu yozuv yangi emas, oldingisi qaytdi
            "duplicate": duplicate,
        }

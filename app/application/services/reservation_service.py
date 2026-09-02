from uuid import UUID
from datetime import date, datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    NotFoundException,
    ConflictException,
    ValidationException,
    BadRequestException,
    ForbiddenException,
)
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.room_status_history import RoomStatusHistory
from app.infrastructure.database.models.invoice import Invoice, InvoiceLineItem
from app.infrastructure.database.models.payment import Payment
from app.infrastructure.database.models.housekeeping import HousekeepingTask
from app.infrastructure.database.repositories.reservation_repo import ReservationRepository
from app.infrastructure.database.repositories.room_repo import RoomRepository
from app.infrastructure.database.repositories.guest_repo import GuestRepository
from app.infrastructure.database.repositories.finance_repo import InvoiceRepository
from app.infrastructure.database.repositories.user_repo import UserRepository
from app.infrastructure.database.models.service import HotelService
from app.infrastructure.database.models.hotel import Hotel
from app.application.services.discount_policy import check_discount
from app.application.services.notification_service import NotificationService
from app.shared.utils import generate_code
from sqlalchemy import func, select


# Bron tahriri (xona almashtirish) uchun vaqt oynasi sozlamasi:
# hotels.settings JSONB ichida saqlanadi. 0 — cheklovsiz.
RESERVATION_EDIT_KEY = "reservation_edit"
DEFAULT_EDIT_WINDOW_MINUTES = 10


# Yangi bandlov sozlamalari (hotels.settings["booking"]) — hamrohlarni
# ro'yxatga olish majburiymi. Standart: majburiy emas (avvalgi xatti-harakat).
BOOKING_SETTINGS_KEY = "booking"


def require_all_guests(hotel_settings: dict | None) -> bool:
    """Xonadagi HAR BIR kishi mehmon sifatida kiritilishi shartmi.

    Yoqilgan bo'lsa: mehmonlar soni nechta bo'lsa, shuncha mehmon
    (asosiy + hamrohlar) ko'rsatilishi kerak.
    """
    raw = (hotel_settings or {}).get(BOOKING_SETTINGS_KEY)
    if not isinstance(raw, dict):
        return False
    return raw.get("require_all_guests") is True


def resolve_edit_window_minutes(hotel_settings: dict | None) -> int:
    raw = (hotel_settings or {}).get(RESERVATION_EDIT_KEY) or {}
    value = raw.get("window_minutes")
    if isinstance(value, (int, float)) and 0 <= value <= 1440:
        return int(value)
    return DEFAULT_EDIT_WINDOW_MINUTES


# Xona holatiga qarab bron qilish taqiqlari.
#
# Ikki daraja bor, chunki ikki holat bir xil emas. Ta'mir, tekshiruv va
# xizmatdan chiqarish — xona umuman ishlatilmaydi va bu qachon tugashi
# noma'lum; shuning uchun kelgusi sanalarga ham bron qilinmaydi, avval holat
# almashtirilishi kerak. Tozalash esa qisqa va o'z-o'zidan tugaydi, kelgusi
# sanalarga to'sqinlik qilmaydi — faqat mehmon AYNAN HOZIR kirmoqchi bo'lsa
# to'sadi.
# Bron bekor qilinganda mehmonga qaytariladigan pul.
#
# Mehmonxonalar bu masalada bir xil emas: biri to'lovni to'liq qaytaradi,
# biri jarima ushlab qoladi. Shuning uchun foiz sozlamada saqlanadi
# (hotels.settings -> cancellation_policy), standarti esa 0 — ya'ni
# sozlanmagan mehmonxonada pul TO'LIQ qaytariladi. Bu ataylab: tizim
# o'zboshimchalik bilan mijozning pulini ushlab qolmasligi kerak.
CANCELLATION_POLICY_KEY = "cancellation_policy"
DEFAULT_CANCELLATION_FEE_PERCENT = 0.0


def resolve_cancellation_fee_percent(hotel_settings: dict | None) -> float:
    """Bekor qilishda ushlab qolinadigan foiz (0-100)."""
    policy = (hotel_settings or {}).get(CANCELLATION_POLICY_KEY) or {}
    try:
        value = float(policy.get("fee_percent", DEFAULT_CANCELLATION_FEE_PERCENT))
    except (TypeError, ValueError):
        return DEFAULT_CANCELLATION_FEE_PERCENT
    return min(max(value, 0.0), 100.0)


def compute_cancellation_refund(
    paid: float, fee_percent: float, requested: float | None = None
) -> tuple[float, float]:
    """Qaytariladigan va ushlab qolinadigan summa.

    `requested` berilsa xodim tanlagani ustun turadi — masalan kech bekor
    qilishda ko'proq ushlab qolish yoki aksincha, xayrixohlik bilan to'liq
    qaytarish. Lekin to'langan puldan ko'p qaytarib bo'lmaydi: aks holda
    bekor qilish orqali kassadan pul chiqarish yo'li ochilardi.

    Manfiy `paid` (ma'lumot buzilgan bo'lsa) nol deb qaraladi — bekor qilish
    shu sababdan yiqilmasligi kerak.
    """
    paid = max(round(float(paid or 0), 2), 0.0)
    if requested is None:
        refund = round(paid - paid * min(max(fee_percent, 0.0), 100.0) / 100.0, 2)
    else:
        refund = round(float(requested), 2)
        if refund < 0:
            raise ValidationException(
                "Qaytariladigan summa manfiy bo'la olmaydi", "INVALID_REFUND"
            )
        if refund > paid + 0.01:
            raise ValidationException(
                f"Qaytariladigan summa to'langan puldan oshib ketdi "
                f"(to'langan: {paid:.0f} So'm)",
                "REFUND_EXCEEDS_PAID",
            )
        refund = min(refund, paid)
    return refund, round(paid - refund, 2)


ROOM_STATUS_BLOCKED_ALWAYS = ("MAINTENANCE", "INSPECTION", "OUT_OF_SERVICE")
ROOM_STATUS_BLOCKED_NOW = ("CLEANING",)

ROOM_STATUS_LABELS_UZ = {
    "CLEANING": "tozalanmoqda",
    "MAINTENANCE": "ta'mirda",
    "INSPECTION": "tekshiruvda",
    "OUT_OF_SERVICE": "xizmatdan tashqari",
}


class ReservationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ReservationRepository(session)
        self.room_repo = RoomRepository(session)
        self.guest_repo = GuestRepository(session)
        self.invoice_repo = InvoiceRepository(session)
        self.user_repo = UserRepository(session)

    async def _get_hotel_code(self, hotel_id: UUID) -> str:
        from app.infrastructure.database.models.hotel import Hotel
        stmt = select(Hotel).where(Hotel.id == hotel_id)
        result = await self.session.execute(stmt)
        hotel = result.scalar_one_or_none()
        if not hotel:
            raise NotFoundException("Hotel not found", "HOTEL_NOT_FOUND")
        return hotel.code

    async def _get_room_base_price(self, room_id: UUID, hotel_id: UUID) -> float:
        room = await self.room_repo.get_by_id(room_id, hotel_id)
        if not room:
            return 0
        from app.infrastructure.database.models.room_type import RoomType
        rt_stmt = select(RoomType).where(RoomType.id == room.room_type_id)
        rt_result = await self.session.execute(rt_stmt)
        room_type = rt_result.scalar_one_or_none()
        return float(room_type.base_price) if room_type else 0

    async def _calculate_price(
        self,
        base_price: float,
        booking_type: str,
        check_in_date: date,
        check_out_date: date,
        check_in_datetime: datetime | None = None,
        check_out_datetime: datetime | None = None,
    ) -> tuple[float, float]:
        if booking_type == "HOURLY" and check_in_datetime and check_out_datetime:
            delta = check_out_datetime - check_in_datetime
            hours = delta.total_seconds() / 3600
            if hours < 1:
                hours = 1
            # Soatlik bron narxi davomiylikka BOG'LIQ EMAS — kunlik narx
            # to'liq olinadi (1 soat ham, 24 soat ham bir xil narx)
            room_charge = float(round(base_price))
            return room_charge, hours

        nights = (check_out_date - check_in_date).days
        if nights < 1:
            nights = 1
        room_charge = base_price * nights
        return room_charge, nights

    async def _create_invoice(
        self,
        hotel_id: UUID,
        reservation_id: UUID,
        guest_id: UUID,
        room_id: UUID,
        base_price: float,
        room_charge: float,
        discount_amount: float,
        total_amount: float,
        booking_type: str,
        duration: float,
        created_by: UUID,
        status: str = "DRAFT",
    ) -> Invoice:
        hotel_code = await self._get_hotel_code(hotel_id)
        invoice_number = generate_code("INV", hotel_code)

        room = await self.room_repo.get_by_id(room_id, hotel_id)
        room_number = room.room_number if room else ""
        duration_label = "hour(s)" if booking_type == "HOURLY" else "night(s)"

        invoice = Invoice(
            hotel_id=hotel_id,
            reservation_id=reservation_id,
            guest_id=guest_id,
            invoice_number=invoice_number,
            invoice_date=date.today(),
            due_date=date.today() + timedelta(days=7),
            subtotal=room_charge,
            tax_amount=0,
            discount_amount=discount_amount,
            total_amount=max(total_amount, 0),
            paid_amount=0,
            status=status,
            created_by=created_by,
        )
        self.session.add(invoice)
        await self.session.flush()

        room_line = InvoiceLineItem(
            invoice_id=invoice.id,
            hotel_id=hotel_id,
            description=f"Room charge: {room_number} ({duration} {duration_label} @ {base_price})",
            line_type="ROOM_CHARGE",
            quantity=duration,
            unit_price=base_price,
            total_price=room_charge,
            created_at=datetime.now(timezone.utc),
        )
        self.session.add(room_line)
        return invoice

    async def _create_payment(
        self,
        hotel_id: UUID,
        invoice: Invoice,
        amount: float,
        payment_method: str,
        created_by: UUID,
        notes: str = "Payment at reservation creation",
    ) -> Payment:
        hotel_code = await self._get_hotel_code(hotel_id)
        payment_number = generate_code("PAY", hotel_code)

        payment = Payment(
            hotel_id=hotel_id,
            invoice_id=invoice.id,
            payment_number=payment_number,
            amount=amount,
            payment_method=payment_method,
            payment_date=date.today(),
            notes=notes,
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
        )
        self.session.add(payment)

        new_paid = float(invoice.paid_amount) + amount
        invoice.paid_amount = new_paid
        if new_paid >= float(invoice.total_amount):
            invoice.status = "PAID"
        else:
            invoice.status = "PARTIALLY_PAID"

        await self.session.flush()
        return payment

    async def _compute_discount(self, room_charge: float, discount_amount: float, discount_percent: float) -> tuple[float, float]:
        # Bazadan kelgan qiymatlar Decimal bo'ladi — float arifmetikasi bilan
        # aralashsa TypeError beradi, shuning uchun kirishda bir xillashtiriladi
        final_discount_amount = float(discount_amount or 0)
        final_discount_percent = float(discount_percent or 0)
        if final_discount_percent > 0:
            # Chegirma ham butun so'mga yaxlitlanadi — tiyinli qoldiq qolmasligi uchun
            final_discount_amount = float(round(room_charge * final_discount_percent / 100))
        return final_discount_amount, final_discount_percent

    async def move_room(
        self,
        hotel_id: UUID,
        reservation_id: UUID,
        new_room_id: UUID,
        current: dict,
    ) -> Reservation:
        """Bronni boshqa xonaga ko'chirish.

        Qoidalar:
          - faqat PENDING/CONFIRMED/CHECKED_IN bronlar;
          - oddiy xodim uchun bron yaratilgandan keyingi N daqiqa ichida
            (sozlamalardan, default 10; 0 — cheklovsiz); ADMIN/SUPER_ADMIN
            istalgan payt ko'chira oladi;
          - yangi xona bronning QOLGAN davri uchun bo'sh bo'lishi shart;
          - narx: kirilmagan bronda butun davr yangi xona narxida; CHECKED_IN
            da yashab bo'lingan kunlar eski narxda, qolganlari yangi narxda
            (soatlik bron — yangi xonaning yaxlit narxi). Farq bron balansida
            qo'shimcha to'lov/kamayish sifatida ko'rinadi;
          - tozalash vazifasi AVTOMATIK ochilmaydi;
          - har ko'chirish room_moves auditiga yoziladi (o'chirilmaydi).
        """
        reservation = await self.repo.get_by_id(reservation_id, hotel_id)
        if not reservation:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
        if reservation.status not in ("PENDING", "CONFIRMED", "CHECKED_IN"):
            raise ValidationException(
                f"Faqat faol bronni ko'chirish mumkin (holat: {reservation.status})",
                "INVALID_STATUS",
            )
        if reservation.room_id == new_room_id:
            raise ValidationException("Bron allaqachon shu xonada", "SAME_ROOM")

        # Tahrir oynasi: oddiy xodim faqat belgilangan daqiqalar ichida
        is_admin = current.get("user_type") in ("ADMIN", "SUPER_ADMIN")
        if not is_admin:
            from app.infrastructure.database.models.hotel import Hotel
            hotel = await self.session.get(Hotel, hotel_id)
            window = resolve_edit_window_minutes(hotel.settings if hotel else None)
            if window > 0 and reservation.created_at:
                created = reservation.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                elapsed_min = (
                    datetime.now(timezone.utc) - created
                ).total_seconds() / 60
                if elapsed_min > window:
                    raise ForbiddenException(
                        f"Tahrirlash muddati tugagan ({window} daqiqa). "
                        "Administratorga murojaat qiling.",
                        "EDIT_WINDOW_EXPIRED",
                    )

        old_room = await self.room_repo.get_by_id(reservation.room_id, hotel_id)
        new_room = await self.room_repo.get_by_id(new_room_id, hotel_id)
        if not new_room:
            raise NotFoundException("Room not found", "ROOM_NOT_FOUND")
        booking_type = reservation.booking_type or "DAILY"
        now_utc = datetime.now(timezone.utc)
        local_today = (
            now_utc + timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)
        ).date()

        # Yangi xona holati bu bronni qabul qila oladimi. Ko'chirishda
        # mehmon odatda darhol kiradi, shuning uchun tozalanayotgan xona ham
        # to'siladi — agar bron davri hozirni qamrasa.
        self._assert_room_bookable(
            new_room,
            booking_type,
            reservation.check_in_date,
            reservation.check_out_date,
            reservation.check_in_datetime,
            reservation.check_out_datetime,
        )

        # Qolgan davr uchun bandlik tekshiruvi
        eff_check_in = reservation.check_in_date
        eff_in_dt = reservation.check_in_datetime
        if reservation.status == "CHECKED_IN":
            if booking_type == "DAILY":
                eff_check_in = min(
                    max(reservation.check_in_date, local_today),
                    reservation.check_out_date - timedelta(days=1),
                )
            elif reservation.check_in_datetime and reservation.check_out_datetime:
                if now_utc < reservation.check_out_datetime:
                    eff_in_dt = max(reservation.check_in_datetime, now_utc)

        available = await self.repo.check_room_availability(
            new_room_id,
            eff_check_in,
            reservation.check_out_date,
            exclude_reservation_id=reservation.id,
            booking_type=booking_type,
            check_in_datetime=eff_in_dt,
            check_out_datetime=reservation.check_out_datetime,
        )
        if not available:
            raise ConflictException(
                f"{new_room.room_number}-xona bu davr uchun band",
                "ROOM_ALREADY_BOOKED",
            )

        # --- Narx qayta hisobi ---
        old_base = await self._get_room_base_price(reservation.room_id, hotel_id)
        new_base = await self._get_room_base_price(new_room_id, hotel_id)

        if booking_type == "HOURLY":
            # Soatlik narx davomiylikka bog'liq emas — yangi xonaning yaxlit narxi
            new_charge = float(round(new_base))
        else:
            nights = max((reservation.check_out_date - reservation.check_in_date).days, 1)
            if reservation.status == "CHECKED_IN":
                stayed = (local_today - reservation.check_in_date).days
                stayed = max(0, min(stayed, nights))
                new_charge = float(old_base) * stayed + float(new_base) * (nights - stayed)
            else:
                new_charge = float(new_base) * nights

        discount_amount, _ = await self._compute_discount(
            new_charge,
            reservation.discount_amount or 0,
            reservation.discount_percent or 0,
        )
        new_room_total = max(new_charge - discount_amount, 0)

        # Invoice bilan sinxronlash: xizmat qatorlari saqlanadi, xona qatori yangilanadi
        service_total = 0.0
        invoice = await self.invoice_repo.get_by_reservation(reservation_id, hotel_id)
        if invoice:
            line_items = await self.invoice_repo.get_line_items(invoice.id)
            duration_label = "hour(s)" if booking_type == "HOURLY" else "night(s)"
            duration = 1 if booking_type == "HOURLY" else max(
                (reservation.check_out_date - reservation.check_in_date).days, 1
            )
            for li in line_items:
                if li.line_type == "ROOM_CHARGE":
                    li.description = (
                        f"Room charge: {new_room.room_number} "
                        f"({duration} {duration_label} @ {new_base})"
                    )
                    li.quantity = duration
                    li.unit_price = new_base
                    li.total_price = new_charge
                elif li.line_type == "SERVICE_CHARGE":
                    service_total += float(li.total_price or 0)
            invoice.subtotal = new_charge
            invoice.discount_amount = discount_amount
            invoice.total_amount = max(new_room_total + service_total, 0)

        old_total = float(reservation.total_amount or 0)
        new_total = max(new_room_total + service_total, 0)
        reservation.total_amount = new_total

        # To'lov holati yangi jamiga ko'ra
        paid = float(reservation.paid_amount or 0)
        if paid <= 0:
            reservation.payment_status = "UNPAID"
        elif paid >= new_total:
            reservation.payment_status = "PAID"
        else:
            reservation.payment_status = "PARTIALLY_PAID"

        # Xonalar holati (tozalash vazifasi OCHILMAYDI — resepsiya xohlasa qo'lda ochadi)
        if old_room and old_room.current_status in ("OCCUPIED", "RESERVED"):
            old_room.current_status = "AVAILABLE"
        new_room.current_status = (
            "OCCUPIED" if reservation.status == "CHECKED_IN" else "RESERVED"
        )

        # Audit yozuvi — kim, qachon, qayerdan qayerga, narx o'zgarishi
        mover = await self.user_repo.get_by_id(UUID(str(current["id"])))
        entry = {
            "from_room_id": str(reservation.room_id),
            "from_room_number": old_room.room_number if old_room else None,
            "to_room_id": str(new_room_id),
            "to_room_number": new_room.room_number,
            "old_total": old_total,
            "new_total": new_total,
            "moved_by": str(current["id"]),
            "moved_by_name": f"{mover.first_name} {mover.last_name}" if mover else None,
            "moved_at": now_utc.isoformat(),
        }
        reservation.room_moves = [*(reservation.room_moves or []), entry]
        reservation.room_id = new_room_id

        await self.session.flush()
        # Javob serializatsiyasida eskirgan atributlar muammo bermasligi uchun
        await self.session.refresh(reservation)
        return reservation

    async def settle_payment(
        self,
        hotel_id: UUID,
        reservation_id: UUID,
        amount: float,
        payment_method: str,
        direction: str,
        user_id: UUID,
    ) -> Reservation:
        """Bron balansi bo'yicha hisob-kitob (asosan xona almashtirishdan keyin).

        PAY — qo'shimcha to'lov: qisman yoki to'liq, qarzdan oshmasligi kerak.
        Xohlasa umuman to'lamaydi — bron PARTIALLY_PAID/UNPAID (qarz) qoladi.
        REFUND — arzonroq xonaga o'tishda ortiqcha to'langanni qaytarish:
        MANFIY Payment yoziladi, shu tufayli kunlik tushum, kassa-smena va
        moliya hisobotlarida qaytarim minus bilan o'z-o'zidan aks etadi.
        """
        reservation = await self.repo.get_by_id(reservation_id, hotel_id)
        if not reservation:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
        if reservation.status in ("CANCELLED", "NO_SHOW"):
            raise ValidationException(
                "Bekor qilingan bron bo'yicha hisob-kitob qilinmaydi", "INVALID_STATUS"
            )

        amount = float(amount)
        total = float(reservation.total_amount or 0)
        paid = float(reservation.paid_amount or 0)
        invoice = await self.invoice_repo.get_by_reservation(reservation_id, hotel_id)

        if direction == "PAY":
            remaining = total - paid
            if remaining <= 0:
                raise ValidationException(
                    "Bu bronda to'lanadigan qarz yo'q", "NO_BALANCE_DUE"
                )
            if amount > remaining + 0.01:
                raise ValidationException(
                    f"Summa qarzdan oshib ketdi (qarz: {remaining:.0f} So'm)",
                    "AMOUNT_EXCEEDS_BALANCE",
                )
            if not invoice:
                # To'lovsiz yaratilgan bronda hisob-faktura hali yo'q — hozir ochiladi
                base_price = await self._get_room_base_price(reservation.room_id, hotel_id)
                booking_type = reservation.booking_type or "DAILY"
                duration = (
                    1
                    if booking_type == "HOURLY"
                    else max(
                        (reservation.check_out_date - reservation.check_in_date).days, 1
                    )
                )
                discount_amount = float(reservation.discount_amount or 0)
                invoice = await self._create_invoice(
                    hotel_id=hotel_id,
                    reservation_id=reservation.id,
                    guest_id=reservation.guest_id,
                    room_id=reservation.room_id,
                    base_price=base_price,
                    room_charge=total + discount_amount,
                    discount_amount=discount_amount,
                    total_amount=total,
                    booking_type=booking_type,
                    duration=duration,
                    created_by=user_id,
                    status="ISSUED",
                )
            await self._create_payment(
                hotel_id=hotel_id,
                invoice=invoice,
                amount=amount,
                payment_method=payment_method,
                created_by=user_id,
                notes="Qo'shimcha to'lov (bron balansi bo'yicha)",
            )
            new_paid = paid + amount
        elif direction == "REFUND":
            overpaid = paid - total
            if overpaid <= 0:
                raise ValidationException(
                    "Qaytariladigan ortiqcha to'lov yo'q", "NO_OVERPAYMENT"
                )
            if amount > overpaid + 0.01:
                raise ValidationException(
                    f"Summa ortiqcha to'lovdan oshib ketdi (ortiqcha: {overpaid:.0f} So'm)",
                    "AMOUNT_EXCEEDS_OVERPAYMENT",
                )
            if not invoice:
                raise ValidationException(
                    "Bron uchun hisob-faktura topilmadi", "INVOICE_NOT_FOUND"
                )
            hotel_code = await self._get_hotel_code(hotel_id)
            refund = Payment(
                hotel_id=hotel_id,
                invoice_id=invoice.id,
                payment_number=generate_code("PAY", hotel_code),
                # Manfiy summa — hisobotlar va kassa kutilgan summasida
                # qaytarim sifatida avtomatik aks etadi
                amount=-amount,
                payment_method=payment_method,
                payment_date=date.today(),
                notes="Qaytarim (xona almashtirish balansi)",
                created_by=user_id,
                created_at=datetime.now(timezone.utc),
            )
            self.session.add(refund)
            new_paid = paid - amount
            invoice.paid_amount = new_paid
            if new_paid >= float(invoice.total_amount):
                invoice.status = "PAID"
            elif new_paid > 0:
                invoice.status = "PARTIALLY_PAID"
            else:
                invoice.status = "ISSUED"
        else:
            raise ValidationException("Noto'g'ri yo'nalish", "INVALID_DIRECTION")

        reservation.paid_amount = new_paid
        if new_paid <= 0:
            reservation.payment_status = "UNPAID"
        elif new_paid >= total:
            reservation.payment_status = "PAID"
        else:
            reservation.payment_status = "PARTIALLY_PAID"

        await self.session.flush()
        await self.session.refresh(reservation)
        return reservation

    def _assert_room_bookable(
        self,
        room: Room,
        booking_type: str,
        check_in: date,
        check_out: date,
        check_in_dt: datetime | None = None,
        check_out_dt: datetime | None = None,
    ) -> None:
        """Xona holati bu bronga yo'l qo'yadimi.

        Ilgari faqat "har qanday vaqt uchun taqiqlangan" holatlar
        tekshirilardi. Tozalanayotgan xonaga esa hozirning o'zi uchun bron
        qilish mumkin edi: mehmon kalitni olib, hali tozalanmagan xonaga
        kirardi.

        Vaqtlar mahalliy devor soati bo'yicha solishtiriladi. `check_in_dt`
        bazaga foydalanuvchi kiritgan devor soati sifatida tushadi (mintaqa
        siljishisiz), shuning uchun "hozir" ham xuddi shunday olinadi —
        `local_today` allaqachon shu usulda hisoblanadi.
        """
        status = room.current_status
        label = ROOM_STATUS_LABELS_UZ.get(status, status)

        if status in ROOM_STATUS_BLOCKED_ALWAYS:
            raise ConflictException(
                f"{room.room_number}-xona {label} — holat o'zgartirilmaguncha "
                "hech qanday sanaga bron qilib bo'lmaydi",
                "ROOM_NOT_AVAILABLE",
            )

        if status not in ROOM_STATUS_BLOCKED_NOW:
            return

        # Bron davri hozirgi paytni qamrab oladimi
        now_wall = datetime.now(timezone.utc) + timedelta(
            minutes=settings.APP_TZ_OFFSET_MINUTES
        )
        if booking_type == "HOURLY" and check_in_dt and check_out_dt:
            start, end = check_in_dt, check_out_dt
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            covers_now = start <= now_wall < end
        else:
            today = now_wall.date()
            covers_now = check_in <= today < check_out

        if covers_now:
            raise ConflictException(
                f"{room.room_number}-xona hozir {label} — tozalash "
                "yakunlangach bron qilish mumkin. Kelgusi sanalarga hozir ham "
                "bron qilsa bo'ladi.",
                "ROOM_NOT_AVAILABLE",
            )

    async def create_reservation(
        self, hotel_id: UUID, branch_id: UUID, data: dict, created_by: UUID
    ) -> Reservation:
        # Mehmonlar globallashgan: boshqa mehmonxonada ro'yxatga olingan
        # mehmon uchun ham shu mehmonxonada bron qilish mumkin
        guest = await self.guest_repo.get_by_id_unscoped(data["guest_id"])
        if not guest:
            raise NotFoundException("Guest not found", "GUEST_NOT_FOUND")

        room = await self.room_repo.get_by_id(data["room_id"], hotel_id)
        if not room:
            raise NotFoundException("Room not found", "ROOM_NOT_FOUND")

        booking_type = data.get("booking_type", "DAILY")
        check_in = data["check_in_date"]
        check_out = data["check_out_date"]
        check_in_dt = data.get("check_in_datetime")
        check_out_dt = data.get("check_out_datetime")

        # Holat tekshiruvi sanalar o'qilgandan keyin: tozalanayotgan xona
        # kelgusi sanalarga ochiq, faqat hozirgi payt uchun yopiq
        self._assert_room_bookable(
            room, booking_type, check_in, check_out, check_in_dt, check_out_dt
        )

        # O'tgan sanaga bron qilib bo'lmaydi (bugun mumkin). Sana mahalliy
        # vaqt bo'yicha aniqlanadi — server UTC da ishlaydi, tungi soatlarda
        # date.today() mahalliy kundan bir kun orqada bo'lib qolmasligi uchun.
        local_today = (
            datetime.now(timezone.utc) + timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)
        ).date()
        if check_in < local_today:
            raise ValidationException(
                "Cannot create a reservation for a past date",
                "PAST_DATE",
            )

        if booking_type == "HOURLY":
            if not check_in_dt or not check_out_dt:
                raise ValidationException(
                    "check_in_datetime and check_out_datetime are required for hourly bookings",
                    "MISSING_DATETIME",
                )
            if check_in_dt >= check_out_dt:
                raise ValidationException("Check-out datetime must be after check-in datetime", "INVALID_DATETIME")
            if check_in >= check_out:
                check_out = check_in + timedelta(days=1)
        elif check_in >= check_out:
            raise ValidationException("Check-out date must be after check-in date", "INVALID_DATES")

        available = await self.repo.check_room_availability(
            room.id, check_in, check_out,
            booking_type=booking_type,
            check_in_datetime=check_in_dt,
            check_out_datetime=check_out_dt,
        )
        if not available:
            raise ConflictException(
                f"Room {room.room_number} is already booked for these dates",
                "ROOM_ALREADY_BOOKED",
            )

        base_price = await self._get_room_base_price(data["room_id"], hotel_id)
        room_charge, duration = await self._calculate_price(
            base_price, booking_type, check_in, check_out, check_in_dt, check_out_dt
        )

        # Chegirma qoidasi — mehmonxona sozlamasidan. Tekshiruv shu yerda,
        # ya'ni brauzerni chetlab o'tib qoidadan oshirib bo'lmaydi
        policy_hotel = await self.session.get(Hotel, hotel_id)
        check_discount(
            policy_hotel.settings if policy_hotel else None,
            booking_type,
            duration,
            room_charge,
            data.get("discount_amount", 0),
            data.get("discount_percent", 0),
        )

        discount_amount, discount_percent = await self._compute_discount(
            room_charge,
            data.get("discount_amount", 0),
            data.get("discount_percent", 0),
        )

        total_amount = max(room_charge - discount_amount, 0)

        # Qisman (bo'lib) to'lov: `payments` ro'yxati berilsa — har bir bo'lak
        # (summa + usul) alohida Payment bo'lib yoziladi. Berilmasa eski
        # payment_amount + payment_method juftligi bitta to'lov sifatida
        # ishlatiladi (eski klientlar uchun xatti-harakat aynan saqlanadi).
        payment_items = [
            {"amount": float(p.get("amount", 0)), "payment_method": p.get("payment_method")}
            for p in (data.get("payments") or [])
            if float(p.get("amount", 0)) > 0
        ]
        if not payment_items:
            legacy_amount = float(data.get("payment_amount", 0))
            if legacy_amount > 0:
                payment_items = [
                    {"amount": legacy_amount, "payment_method": data.get("payment_method")}
                ]
        payment_amount = sum(p["amount"] for p in payment_items)

        hotel_code = await self._get_hotel_code(hotel_id)
        reservation_number = generate_code("RES", hotel_code)

        if payment_amount > 0:
            paid = min(payment_amount, total_amount)
            if paid >= total_amount:
                payment_status = "PAID"
            else:
                payment_status = "PARTIALLY_PAID"
        else:
            paid = 0
            payment_status = "UNPAID"

        # --- Hamrohlar ---
        # Har bir hamroh bazadagi haqiqiy mehmon bo'lishi shart: bu yerda
        # faqat ID keladi, mehmonning o'zi frontendda oldin yaratiladi.
        companions = await self._resolve_companions(
            data.get("companion_guest_ids") or [],
            main_guest_id=data["guest_id"],
            adults=int(data.get("adults", 1) or 1),
            hotel_id=hotel_id,
        )

        reservation = Reservation(
            hotel_id=hotel_id,
            branch_id=branch_id,
            reservation_number=reservation_number,
            guest_id=data["guest_id"],
            room_id=data["room_id"],
            booking_type=booking_type,
            check_in_date=check_in,
            check_out_date=check_out,
            check_in_datetime=check_in_dt,
            check_out_datetime=check_out_dt,
            adults=data.get("adults", 1),
            children=data.get("children", 0),
            discount_amount=discount_amount,
            discount_percent=discount_percent,
            notes=data.get("notes"),
            total_amount=total_amount,
            paid_amount=paid,
            payment_status=payment_status,
            status="CONFIRMED",
            created_by=created_by,
            companions=companions or None,
        )
        reservation = await self.repo.create(reservation)

        room.current_status = "RESERVED"
        await self.room_repo.update(room, current_status="RESERVED")

        history = RoomStatusHistory(
            hotel_id=hotel_id,
            room_id=room.id,
            status="RESERVED",
            changed_by=created_by,
            notes=f"Reservation {reservation_number} created",
        )
        self.session.add(history)

        if payment_amount > 0:
            invoice_status = "ISSUED"
            invoice = await self._create_invoice(
                hotel_id=hotel_id,
                reservation_id=reservation.id,
                guest_id=data["guest_id"],
                room_id=data["room_id"],
                base_price=base_price,
                room_charge=room_charge,
                discount_amount=discount_amount,
                total_amount=total_amount,
                booking_type=booking_type,
                duration=duration,
                created_by=created_by,
                status=invoice_status,
            )

            # Har bir to'lov bo'lagi alohida Payment sifatida yoziladi. Umumiy
            # summa total_amount dan oshsa (paid cheklangan) — bo'laklar tartib
            # bilan yoziladi va oxirgisi qolgan summaga qisqartiriladi.
            remaining = paid
            for item in payment_items:
                if remaining <= 0:
                    break
                part = min(item["amount"], remaining)
                await self._create_payment(
                    hotel_id=hotel_id,
                    invoice=invoice,
                    amount=part,
                    payment_method=item["payment_method"],
                    created_by=created_by,
                )
                remaining -= part

        await self.session.flush()
        return reservation

    async def update_reservation(
        self, reservation_id: UUID, hotel_id: UUID, data: dict
    ) -> Reservation:
        reservation = await self.repo.get_by_id(reservation_id, hotel_id)
        if not reservation:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")

        if reservation.status in ("CHECKED_OUT", "CANCELLED", "NO_SHOW"):
            raise ValidationException(
                f"Cannot update reservation in status: {reservation.status}",
                "RESERVATION_LOCKED",
            )

        room_id = data.get("room_id")
        check_in = data.get("check_in_date") or reservation.check_in_date
        check_out = data.get("check_out_date") or reservation.check_out_date

        if room_id or "check_in_date" in data or "check_out_date" in data:
            target_room_id = room_id or reservation.room_id
            available = await self.repo.check_room_availability(
                target_room_id, check_in, check_out, exclude_reservation_id=reservation_id
            )
            if not available:
                raise ConflictException("Room is not available for the selected dates", "ROOM_CONFLICT")

            if room_id and room_id != reservation.room_id:
                new_room = await self.room_repo.get_by_id(room_id, hotel_id)
                if not new_room:
                    raise NotFoundException("Room not found", "ROOM_NOT_FOUND")
                # Xona ALMASHTIRILMOQDA — bu ham xonani yangidan egallash,
                # demak holat tekshiruvi shu yerda ham kerak. Ilgari faqat
                # xona mavjudligi tekshirilardi va tahrirlash yo'li bilan
                # bronni ta'mirdagi xonaga ko'chirib qo'yish mumkin edi.
                self._assert_room_bookable(
                    new_room,
                    data.get("booking_type") or reservation.booking_type or "DAILY",
                    check_in,
                    check_out,
                    data.get("check_in_datetime") or reservation.check_in_datetime,
                    data.get("check_out_datetime") or reservation.check_out_datetime,
                )

        updatable = [
            "room_id", "booking_type", "check_in_date", "check_out_date",
            "check_in_datetime", "check_out_datetime", "adults", "children",
            "discount_amount", "discount_percent", "notes",
        ]
        update_data = {k: v for k, v in data.items() if k in updatable and v is not None}
        return await self.repo.update(reservation, **update_data)

    async def _resolve_companions(
        self,
        raw_ids: list,
        *,
        main_guest_id: UUID,
        adults: int,
        hotel_id: UUID,
    ) -> list[dict]:
        """Hamrohlar ro'yxatini tayyorlash va tekshirish.

        Takrorlar va asosiy mehmon tashlanadi — bir odam ikki marta
        sanalmasligi kerak. Soni mehmonlar sonidan oshsa xato: xonaga
        sig'maydigan ro'yxat jimgina saqlanib qolmasin.
        """
        seen: set[UUID] = set()
        ids: list[UUID] = []
        for raw in raw_ids:
            try:
                gid = raw if isinstance(raw, UUID) else UUID(str(raw))
            except (ValueError, AttributeError, TypeError):
                raise ValidationException(
                    "Hamroh mehmon ID'si noto'g'ri", "INVALID_COMPANION"
                )
            if gid == main_guest_id or gid in seen:
                continue
            seen.add(gid)
            ids.append(gid)

        if len(ids) + 1 > max(adults, 1):
            raise ValidationException(
                f"Hamrohlar soni mehmonlar sonidan ko'p: {len(ids) + 1} > {adults}",
                "TOO_MANY_COMPANIONS",
            )

        companions: list[dict] = []
        for gid in ids:
            # Mehmonlar bazasi global — boshqa mehmonxonada ro'yxatga olingan
            # hamroh ham qabul qilinadi (asosiy mehmon bilan bir xil qoida)
            guest = await self.guest_repo.get_by_id_unscoped(gid)
            if not guest:
                raise NotFoundException(
                    "Hamroh mehmon topilmadi", "COMPANION_NOT_FOUND"
                )
            name = " ".join(
                part for part in (guest.first_name, guest.last_name) if part
            ).strip()
            companions.append({"guest_id": str(gid), "name": name or None})

        # Majburiy rejim: xonadagi har bir kishi ro'yxatga olinishi shart
        hotel = await self.session.get(Hotel, hotel_id)
        if require_all_guests(hotel.settings if hotel else None):
            total = len(companions) + 1
            if total < max(adults, 1):
                raise ValidationException(
                    f"Xonadagi har bir mehmon ro'yxatga olinishi kerak: "
                    f"{adults} kishidan {total} tasi kiritilgan",
                    "GUESTS_REQUIRED",
                )
        return companions

    async def get_reservations(
        self,
        hotel_id: UUID | None,
        skip: int = 0,
        limit: int = 100,
        status: str | None = None,
        branch_id: UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[Reservation]:
        from sqlalchemy import select as sa_select
        stmt = sa_select(Reservation).where(
            Reservation.is_deleted.is_(False),
        )
        if hotel_id is not None:
            stmt = stmt.where(Reservation.hotel_id == hotel_id)
        if status:
            stmt = stmt.where(Reservation.status == status)
        if branch_id:
            stmt = stmt.where(Reservation.branch_id == branch_id)
        if date_from:
            stmt = stmt.where(Reservation.check_out_date > date_from)
        if date_to:
            stmt = stmt.where(Reservation.check_in_date < date_to)
        stmt = stmt.order_by(Reservation.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_reservation(self, reservation_id: UUID, hotel_id: UUID | None) -> Reservation:
        reservation = await self.repo.get_with_details(reservation_id, hotel_id)
        if not reservation:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
        return reservation

    async def get_calendar(
        self,
        hotel_id: UUID | None,
        view: str,
        date_param: date,
        branch_id: UUID | None = None,
        room_type_id: UUID | None = None,
        skip: int = 0,
        limit: int = 200,
    ) -> list[Reservation]:
        if view == "daily":
            start_date = date_param
            end_date = date_param + timedelta(days=1)
        elif view == "weekly":
            weekday = date_param.weekday()
            start_date = date_param - timedelta(days=weekday)
            end_date = start_date + timedelta(days=7)
        elif view == "monthly":
            start_date = date_param.replace(day=1)
            if date_param.month == 12:
                end_date = date_param.replace(year=date_param.year + 1, month=1, day=1)
            else:
                end_date = date_param.replace(month=date_param.month + 1, day=1)
        else:
            raise ValidationException(f"Invalid calendar view: {view}", "INVALID_VIEW")

        return await self.repo.get_calendar_reservations(
            hotel_id, start_date, end_date, branch_id, room_type_id, skip, limit
        )

    async def check_availability(
        self,
        hotel_id: UUID | None,
        check_in: date,
        check_out: date,
        branch_id: UUID | None = None,
        room_type_id: UUID | None = None,
    ) -> list[dict]:
        from sqlalchemy import select as sa_select
        stmt = sa_select(Room).where(
            Room.is_deleted.is_(False),
            Room.current_status.in_(["AVAILABLE", "CLEANING", "RESERVED", "OCCUPIED"]),
        )
        if hotel_id is not None:
            stmt = stmt.where(Room.hotel_id == hotel_id)
        if branch_id:
            stmt = stmt.where(Room.branch_id == branch_id)
        if room_type_id:
            stmt = stmt.where(Room.room_type_id == room_type_id)

        result = await self.session.execute(stmt)
        rooms = list(result.scalars().all())

        available_rooms = []
        for room in rooms:
            is_available = await self.repo.check_room_availability(room.id, check_in, check_out)
            if is_available:
                available_rooms.append(room)

        from app.infrastructure.database.models.room_type import RoomType
        from app.infrastructure.database.models.floor import Floor

        results = []
        for room in available_rooms:
            rt_stmt = sa_select(RoomType).where(RoomType.id == room.room_type_id)
            rt_result = await self.session.execute(rt_stmt)
            room_type = rt_result.scalar_one_or_none()

            floor_stmt = sa_select(Floor).where(Floor.id == room.floor_id)
            floor_result = await self.session.execute(floor_stmt)
            floor = floor_result.scalar_one_or_none()

            results.append({
                "id": str(room.id),
                "room_number": room.room_number,
                "room_type_id": str(room.room_type_id),
                "room_type_name": room_type.name if room_type else "",
                "floor_id": str(room.floor_id) if room.floor_id else "",
                "floor_number": floor.floor_number if floor else 0,
                "base_price": float(room_type.base_price) if room_type else 0,
                "current_status": room.current_status,
            })

        return results

    async def check_in(
        self, reservation_id: UUID, hotel_id: UUID, user_id: UUID
    ) -> Reservation:
        reservation = await self.repo.get_by_id(reservation_id, hotel_id)
        if not reservation:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")

        if reservation.status != "CONFIRMED":
            raise ValidationException(
                f"Cannot check in reservation with status: {reservation.status}",
                "INVALID_STATUS",
            )

        room = await self.room_repo.get_by_id(reservation.room_id, hotel_id)
        if not room:
            raise NotFoundException("Room not found", "ROOM_NOT_FOUND")

        if room.current_status != "RESERVED":
            raise ValidationException(
                f"Room is not in RESERVED status (current: {room.current_status})",
                "ROOM_NOT_READY",
            )

        # Mahalliy sana bo'yicha tekshiramiz — server UTC da ishlaydi va tungi
        # 00:00-05:00 (mahalliy) oralig'ida date.today() hali "kecha"da bo'lib,
        # bugungi mehmonni check-in qilishga to'sqinlik qilardi.
        today = (
            datetime.now(timezone.utc) + timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)
        ).date()
        if reservation.check_in_date > today:
            raise ValidationException(
                f"Check-in date is {reservation.check_in_date}, not yet arrived",
                "NOT_CHECK_IN_DATE",
            )

        room.current_status = "OCCUPIED"
        await self.room_repo.update(room, current_status="OCCUPIED")

        reservation.status = "CHECKED_IN"
        await self.repo.update(reservation, status="CHECKED_IN")

        history = RoomStatusHistory(
            hotel_id=hotel_id,
            room_id=room.id,
            status="OCCUPIED",
            changed_by=user_id,
            notes=f"Check-in for reservation {reservation.reservation_number}",
        )
        self.session.add(history)
        await self.session.flush()

        return reservation

    async def _ensure_cleaning_task(
        self,
        reservation: Reservation,
        hotel_id: UUID,
        room: Room,
        created_by: UUID,
        assigned_to: UUID | None = None,
        active_only: bool = False,
    ) -> HousekeepingTask | None:
        """Bron uchun tozalash tunini yaratadi (agar hali mavjud bo'lmasa).

        Idempotent: shu bronga bog'langan bekor qilinmagan CLEANING tun bo'lsa,
        yangisi yaratilmaydi (avtomatik va qo'lda chiqish yo'llari dublikat
        yaratmasligi uchun).

        active_only=True bo'lsa faqat FAOL (OPEN/IN_PROGRESS) vazifa mavjudligi
        tekshiriladi — avvalroq COMPLETED bo'lgan vazifa yangisini yaratishga
        to'sqinlik qilmaydi. Bu bekor qilish oqimi uchun: farrosh vazifani
        mehmon hali ichkaridaligida yakunlagan bo'lsa ham, bekor qilishda xona
        CLEANING holatidan chiqishi uchun yangi ochiq vazifa kerak.
        """
        status_filter = (
            HousekeepingTask.status.in_(["OPEN", "IN_PROGRESS"])
            if active_only
            else HousekeepingTask.status != "CANCELLED"
        )
        existing = await self.session.execute(
            select(HousekeepingTask).where(
                HousekeepingTask.reservation_id == reservation.id,
                HousekeepingTask.task_type == "CLEANING",
                status_filter,
            )
        )
        if existing.scalars().first():
            return None

        task = HousekeepingTask(
            hotel_id=hotel_id,
            branch_id=reservation.branch_id,
            room_id=room.id,
            reservation_id=reservation.id,
            task_type="CLEANING",
            status="OPEN",
            priority="HIGH",
            assigned_to=assigned_to,
            notes=f"Auto-created cleaning for reservation {reservation.reservation_number}",
            scheduled_date=date.today(),
            created_by=created_by,
        )
        self.session.add(task)
        await self.session.flush()
        return task

    async def check_out(
        self,
        reservation_id: UUID,
        hotel_id: UUID,
        user_id: UUID,
        transition_room: bool = True,
        create_cleaning_task: bool = True,
        allowed_statuses: tuple[str, ...] = ("CHECKED_IN",),
    ) -> dict:
        """Bronni CHECKED_OUT holatiga o'tkazadi (hisob-faktura bilan).

        transition_room / create_cleaning_task standart holda True — qo'lda
        chiqish endpointi uchun avvalgi xatti-harakat aynan saqlanadi. Avtomatik
        oqim bularni False qilib chaqiradi (xona holati va tozalash tuni allaqachon
        bosqichli ravishda boshqarilgan bo'ladi).

        allowed_statuses standart holda faqat ("CHECKED_IN",) — qo'lda chiqishda
        avvalgi qat'iy tekshiruv saqlanadi. Avtomatik oqim vaqti o'tgan, lekin
        hech qachon kirish qilinmagan CONFIRMED bronlarni yopish uchun buni
        kengaytirib chaqiradi.
        """
        reservation = await self.repo.get_by_id(reservation_id, hotel_id)
        if not reservation:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")

        if reservation.status not in allowed_statuses:
            raise ValidationException(
                f"Cannot check out reservation with status: {reservation.status}",
                "INVALID_STATUS",
            )

        room = await self.room_repo.get_by_id(reservation.room_id, hotel_id)
        if not room:
            raise NotFoundException("Room not found", "ROOM_NOT_FOUND")

        from app.infrastructure.database.models.room_type import RoomType
        rt_stmt = select(RoomType).where(RoomType.id == room.room_type_id)
        rt_result = await self.session.execute(rt_stmt)
        room_type = rt_result.scalar_one_or_none()
        base_price = float(room_type.base_price) if room_type else 0

        booking_type = reservation.booking_type or "DAILY"
        room_charge, duration = await self._calculate_price(
            base_price, booking_type,
            reservation.check_in_date, reservation.check_out_date,
            reservation.check_in_datetime, reservation.check_out_datetime,
        )

        discount_amount, _ = await self._compute_discount(
            room_charge,
            reservation.discount_amount or 0,
            reservation.discount_percent or 0,
        )
        total_amount = max(room_charge - discount_amount, 0)

        hotel_code = await self._get_hotel_code(hotel_id)
        duration_label = "hour(s)" if booking_type == "HOURLY" else "night(s)"

        existing_invoice = await self.invoice_repo.get_by_reservation(reservation_id, hotel_id)

        if existing_invoice:
            invoice = existing_invoice
            invoice.subtotal = room_charge
            invoice.discount_amount = discount_amount
            invoice.tax_amount = 0

            line_items = await self.invoice_repo.get_line_items(invoice.id)
            has_room_charge = any(
                li.line_type == "ROOM_CHARGE" for li in line_items
            )

            if not has_room_charge:
                room_number = room.room_number if room else ""
                room_line = InvoiceLineItem(
                    invoice_id=invoice.id,
                    hotel_id=hotel_id,
                    description=f"Room charge: {room_number} ({duration} {duration_label} @ {base_price})",
                    line_type="ROOM_CHARGE",
                    quantity=duration,
                    unit_price=base_price,
                    total_price=room_charge,
                    created_at=datetime.now(timezone.utc),
                )
                self.session.add(room_line)
        else:
            invoice_number = generate_code("INV", hotel_code)
            invoice = Invoice(
                hotel_id=hotel_id,
                reservation_id=reservation_id,
                guest_id=reservation.guest_id,
                invoice_number=invoice_number,
                invoice_date=date.today(),
                due_date=date.today() + timedelta(days=7),
                subtotal=room_charge,
                tax_amount=0,
                discount_amount=discount_amount,
                total_amount=max(total_amount, 0),
                paid_amount=0,
                status="ISSUED",
                created_by=user_id,
            )
            self.session.add(invoice)
            await self.session.flush()

            room_line = InvoiceLineItem(
                invoice_id=invoice.id,
                hotel_id=hotel_id,
                description=f"Room charge: {room.room_number} ({duration} {duration_label} @ {base_price})",
                line_type="ROOM_CHARGE",
                quantity=duration,
                unit_price=base_price,
                total_price=room_charge,
                created_at=datetime.now(timezone.utc),
            )
            self.session.add(room_line)

        services = await self.repo.get_reservation_services(reservation_id, hotel_id)
        for svc in services:
            service_line = InvoiceLineItem(
                invoice_id=invoice.id,
                hotel_id=hotel_id,
                description=f"Service: {svc['service_name']} (x{svc['quantity']})",
                line_type="SERVICE_CHARGE",
                reference_type="reservation_service",
                reference_id=UUID(svc["id"]),
                quantity=svc["quantity"],
                unit_price=svc["unit_price"],
                total_price=svc["total_price"],
                created_at=datetime.now(timezone.utc),
            )
            self.session.add(service_line)
            total_amount += svc["total_price"]

        invoice.total_amount = max(total_amount, 0)
        if not existing_invoice:
            invoice.status = "ISSUED"
        elif invoice.status == "DRAFT":
            invoice.status = "ISSUED"

        reservation.total_amount = float(invoice.total_amount)
        if reservation.payment_status == "UNPAID" and float(invoice.paid_amount) == 0:
            pass
        elif float(invoice.paid_amount) >= float(invoice.total_amount):
            reservation.payment_status = "PAID"
        elif float(invoice.paid_amount) > 0:
            reservation.payment_status = "PARTIALLY_PAID"

        await self.session.flush()

        reservation.status = "CHECKED_OUT"
        await self.repo.update(reservation, status="CHECKED_OUT")

        # Xonani CLEANING ga o'tkazish — qo'lda chiqishda True (avvalgidek).
        # Avtomatik oqimda xona holati bosqichma-bosqich alohida boshqariladi.
        if transition_room:
            room.current_status = "CLEANING"
            await self.room_repo.update(room, current_status="CLEANING")

            history = RoomStatusHistory(
                hotel_id=hotel_id,
                room_id=room.id,
                status="CLEANING",
                changed_by=user_id,
                notes=f"Check-out for reservation {reservation.reservation_number}",
            )
            self.session.add(history)

        # Tozalash tuni — FAOL vazifa bo'lmasa yaratiladi (idempotent).
        #
        # active_only=True muhim: aks holda filtr "CANCELLED emas" bo'lib,
        # allaqachon YAKUNLANGAN vazifa yangisining yaratilishiga to'sqinlik
        # qilardi. Yuqorida xona endigina CLEANING ga o'tkazildi, ya'ni uni
        # o'sha holatdan chiqaradigan ochiq vazifa kerak — vazifa yaratilmasa
        # xona yetim qolib, CLEANING da abadiy tiqilib qolardi.
        if create_cleaning_task:
            await self._ensure_cleaning_task(
                reservation, hotel_id, room, user_id, active_only=True
            )

        await self.session.flush()

        return {
            "reservation_id": str(reservation.id),
            "reservation_number": reservation.reservation_number,
            "invoice_id": str(invoice.id),
            "invoice_number": invoice.invoice_number,
            "total_amount": float(invoice.total_amount),
            "nights": duration,
            "room_charge": room_charge,
            "status": "CHECKED_OUT",
        }

    async def _find_cleaner(self, hotel_id: UUID, branch_id: UUID) -> UUID | None:
        """Eng kam yuklamali farroshni topadi (automation_service bilan bir xil
        mantiq): farrosh = housekeeping.* ruxsatiga ega EMPLOYEE. Iloji bo'lsa
        o'sha filialdan. Topilmasa None — vazifa biriktirilmay yaratiladi."""
        employees = await self.user_repo.get_employees(hotel_id, limit=500)
        candidates = []
        for e in employees:
            if getattr(e, "is_deleted", False):
                continue
            perms = await self.user_repo.get_user_permissions(e.id)
            if any(str(p.get("code", "")).startswith("housekeeping.") for p in perms):
                candidates.append(e)
        if not candidates:
            return None

        same_branch = [e for e in candidates if e.branch_id == branch_id]
        pool = same_branch or candidates
        pool_ids = [e.id for e in pool]

        counts: dict[UUID, int] = {pid: 0 for pid in pool_ids}
        rows = await self.session.execute(
            select(HousekeepingTask.assigned_to, func.count())
            .where(
                HousekeepingTask.assigned_to.in_(pool_ids),
                HousekeepingTask.status.in_(["OPEN", "IN_PROGRESS"]),
            )
            .group_by(HousekeepingTask.assigned_to)
        )
        for assigned_to, cnt in rows.all():
            if assigned_to in counts:
                counts[assigned_to] = cnt
        return min(pool_ids, key=lambda pid: counts.get(pid, 0))

    async def cancellation_quote(
        self, reservation_id: UUID, hotel_id: UUID
    ) -> dict:
        """Bekor qilinsa qancha qaytariladi — o'zgartirmasdan hisoblab beradi.

        Xodim tasdiqlashdan OLDIN summani ko'rishi kerak: pul qaytarish
        orqaga qaytarib bo'lmaydigan amal.
        """
        reservation = await self.repo.get_by_id(reservation_id, hotel_id)
        if not reservation:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")

        from app.infrastructure.database.models.hotel import Hotel

        hotel = await self.session.get(Hotel, hotel_id)
        percent = resolve_cancellation_fee_percent(hotel.settings if hotel else None)
        paid = float(reservation.paid_amount or 0)
        refund, fee = compute_cancellation_refund(paid, percent)
        return {
            "paid_amount": paid,
            "fee_percent": percent,
            "fee_amount": fee,
            "refund_amount": refund,
        }

    async def cancel_reservation(
        self,
        reservation_id: UUID,
        hotel_id: UUID,
        user_id: UUID,
        reason: str | None = None,
        refund_amount: float | None = None,
        refund_method: str | None = None,
    ) -> Reservation:
        reservation = await self.repo.get_by_id(reservation_id, hotel_id)
        if not reservation:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")

        if reservation.status in ("CHECKED_OUT", "CANCELLED", "NO_SHOW"):
            raise ValidationException(
                f"Cannot cancel reservation in status: {reservation.status}",
                "RESERVATION_LOCKED",
            )

        was_checked_in = reservation.status == "CHECKED_IN"

        room = await self.room_repo.get_by_id(reservation.room_id, hotel_id)
        if room and room.current_status == "RESERVED":
            room.current_status = "AVAILABLE"
            await self.room_repo.update(room, current_status="AVAILABLE")

            history = RoomStatusHistory(
                hotel_id=hotel_id,
                room_id=room.id,
                status="AVAILABLE",
                changed_by=user_id,
                notes=f"Reservation {reservation.reservation_number} cancelled",
            )
            self.session.add(history)
        elif room and was_checked_in and room.current_status == "OCCUPIED":
            # Mehmon ichkarida edi — xona tozalashga o'tadi; tozalash vazifasi
            # yakunlangach housekeeping oqimi uni o'zi AVAILABLE qiladi
            room.current_status = "CLEANING"
            await self.room_repo.update(room, current_status="CLEANING")

            history = RoomStatusHistory(
                hotel_id=hotel_id,
                room_id=room.id,
                status="CLEANING",
                changed_by=user_id,
                notes=f"Reservation {reservation.reservation_number} cancelled",
            )
            self.session.add(history)

        invoice = await self.invoice_repo.get_by_reservation(reservation_id, hotel_id)

        # --- Pul qaytarish ---
        #
        # Summa chaqiruvchidan kelishi mumkin (xodim o'zgartirgan bo'lsa),
        # aks holda mehmonxona sozlamasidagi foizdan hisoblanadi. Hisob-faktura
        # VOID qilinishidan OLDIN yoziladi: qaytarim o'sha fakturaga bog'lanadi
        # va moliya hisobotida ko'rinishi kerak.
        paid = float(reservation.paid_amount or 0)
        from app.infrastructure.database.models.hotel import Hotel

        hotel = await self.session.get(Hotel, hotel_id)
        fee_percent = resolve_cancellation_fee_percent(hotel.settings if hotel else None)
        refund, kept = compute_cancellation_refund(paid, fee_percent, refund_amount)

        refund_note = None
        if refund > 0.009 and invoice is not None:
            hotel_code = await self._get_hotel_code(hotel_id)
            refund_note = (
                f"Bekor qilishda qaytarim: {refund:.0f} So'm"
                + (f", ushlab qolindi: {kept:.0f} So'm" if kept > 0.009 else "")
            )
            self.session.add(
                Payment(
                    hotel_id=hotel_id,
                    invoice_id=invoice.id,
                    payment_number=generate_code("PAY", hotel_code),
                    # Manfiy summa — hisobotlar va kassa kutilgan summasida
                    # qaytarim sifatida avtomatik aks etadi
                    amount=-refund,
                    payment_method=refund_method or "CASH",
                    payment_date=date.today(),
                    notes=refund_note,
                    created_by=user_id,
                    created_at=datetime.now(timezone.utc),
                )
            )
            new_paid = round(paid - refund, 2)
            reservation.paid_amount = new_paid
            invoice.paid_amount = new_paid
            # Bron bekor qilingan — qolgan summa jarima, ya'ni "to'langan"
            # emas. REFUNDED holati aynan shuni bildiradi.
            reservation.payment_status = "REFUNDED"
            await self.session.flush()

        # Bron bekor qilinganda unga bog'liq hisob-faktura ham bekor qilinadi
        # (VOID) — aks holda Moliya bo'limida faol bo'lib qolaverardi.
        # Qabul qilingan to'lov yozuvlari (payments) audit uchun saqlanadi.
        if invoice and invoice.status not in ("VOID", "REFUNDED"):
            invoice.status = "VOID"
            void_note = f"Reservation {reservation.reservation_number} cancelled"
            invoice.notes = (
                f"{invoice.notes}\n{void_note}" if invoice.notes else void_note
            )

        # Qaytarim izohi bekor qilish sababiga yoziladi — keyin "qancha
        # qaytarilgan edi?" degan savol tug'ilganda javob shu yerda turadi
        final_reason = reason or "Cancelled by user"
        if refund_note:
            final_reason = f"{final_reason} ({refund_note})"
        reservation = await self.repo.cancel_reservation(
            reservation, final_reason, user_id
        )

        # Mehmon KIRGAN bron bekor qilinganda farroshga avtomatik tozalash
        # vazifasi yaratiladi (eng kam yuklamali farroshga biriktiriladi;
        # farrosh topilmasa vazifa biriktirilmagan holda ochiq qoladi).
        # Hali kirilmagan (PENDING/CONFIRMED) bron bekor qilinsa xona
        # ishlatilmagan — tozalash talab etilmaydi. active_only=True: avvalroq
        # yakunlangan vazifa yangi ochiq vazifa yaratishga to'sqinlik qilmaydi,
        # aks holda xona CLEANING holatida tiqilib qolardi.
        if room and was_checked_in:
            cleaner_id = await self._find_cleaner(hotel_id, reservation.branch_id)
            task = await self._ensure_cleaning_task(
                reservation, hotel_id, room, user_id,
                assigned_to=cleaner_id, active_only=True,
            )
            if task and cleaner_id:
                # Push/notification xatosi bekor qilish oqimini hech qachon buzmaydi
                try:
                    await NotificationService(self.session).notify(
                        hotel_id=hotel_id,
                        user_id=cleaner_id,
                        title="Bron bekor qilindi — tozalash vazifasi",
                        body=(
                            f"{room.room_number}-xona bo'shatildi, "
                            "tozalash vazifasi sizga biriktirildi"
                        ),
                        entity_type="task",
                        entity_id=task.id,
                    )
                except Exception:
                    pass

        await self.session.flush()
        return reservation

    async def request_checkout(
        self,
        reservation_id: UUID,
        hotel_id: UUID,
        user_id: UUID,
        assign_to: UUID | None = None,
    ) -> Reservation:
        """Chiqish jarayonini boshlash: resepsiya "mehmon chiqmoqda" deb
        belgilaydi yoki farrosh "xonani tozalash"ni bosadi.

        Bron darhol yopilmaydi: xona CLEANING holatiga o'tadi, farroshga
        tozalash vazifasi boradi (push bilan). Farrosh vazifani yakunlagach
        housekeeping oqimi bron holatini avtomatik CHECKED_OUT qiladi.
        assign_to berilsa (farroshning o'zi bosganda) vazifa aynan unga
        biriktiriladi. Takroriy chaqiruv xavfsiz (idempotent).
        """
        reservation = await self.repo.get_by_id(reservation_id, hotel_id)
        if not reservation:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
        # CONFIRMED ham qabul qilinadi: kirish rasmiylashtirilmagan bo'lsa-da,
        # xona ishlatilgan bo'lishi mumkin — farrosh tozalab yakunlagach bron
        # baribir yopilishi kerak
        if reservation.status not in ("CHECKED_IN", "CONFIRMED"):
            raise ValidationException(
                "Checkout can be requested only for active reservations "
                f"(status: {reservation.status})",
                "INVALID_STATUS",
            )

        room = await self.room_repo.get_by_id(reservation.room_id, hotel_id)
        if not room:
            raise NotFoundException("Room not found", "ROOM_NOT_FOUND")

        if reservation.checkout_requested_at is None:
            reservation.checkout_requested_at = datetime.now(timezone.utc)

        # Mehmon chiqyapti — xona tozalash holatiga o'tadi (band yoki band
        # qilingan holatdan)
        if room.current_status in ("OCCUPIED", "RESERVED"):
            room.current_status = "CLEANING"
            await self.room_repo.update(room, current_status="CLEANING")
            self.session.add(
                RoomStatusHistory(
                    hotel_id=hotel_id,
                    room_id=room.id,
                    status="CLEANING",
                    changed_by=user_id,
                    notes=f"Checkout requested for {reservation.reservation_number}",
                )
            )

        cleaner_id = assign_to or await self._find_cleaner(hotel_id, reservation.branch_id)
        task = await self._ensure_cleaning_task(
            reservation, hotel_id, room, user_id,
            assigned_to=cleaner_id, active_only=True,
        )
        notify_task = task
        notify_target = cleaner_id if task else None
        if task is None:
            # Faol vazifa allaqachon mavjud (masalan, avtomatik ogohlantirish
            # yaratgan) — o'sha vazifaning farroshiga xabar beramiz;
            # biriktirilmagan bo'lsa hozir biriktiramiz
            result = await self.session.execute(
                select(HousekeepingTask)
                .where(
                    HousekeepingTask.reservation_id == reservation.id,
                    HousekeepingTask.task_type == "CLEANING",
                    HousekeepingTask.status.in_(["OPEN", "IN_PROGRESS"]),
                )
                .order_by(HousekeepingTask.created_at.desc())
            )
            notify_task = result.scalars().first()
            if notify_task is not None:
                if assign_to:
                    # Farrosh o'zi bosdi — vazifa aynan unga biriktiriladi
                    notify_task.assigned_to = assign_to
                elif notify_task.assigned_to is None and cleaner_id:
                    notify_task.assigned_to = cleaner_id
                notify_target = notify_task.assigned_to

        if notify_task is not None and notify_target:
            # Push xatosi asosiy oqimni hech qachon buzmasin
            try:
                await NotificationService(self.session).notify(
                    hotel_id=hotel_id,
                    user_id=notify_target,
                    title="Mijoz chiqmoqda",
                    body=(
                        f"{room.room_number}-xona — mijoz chiqmoqda, xonani "
                        "tozalab tekshiring. Vazifa yakunlangach bron avtomatik yopiladi"
                    ),
                    entity_type="task",
                    entity_id=notify_task.id,
                    send_push=True,
                )
            except Exception:
                pass

        await self.session.flush()
        # updated_at (server tomonda onupdate) flush'dan keyin eskirgan bo'ladi —
        # javob serializatsiyasi commit'dan keyin sinxron kontekstda unga murojaat
        # qilib MissingGreenlet xatosiga yiqilmasligi uchun hozir yangilab olamiz
        await self.session.refresh(reservation)
        return reservation

    async def mark_no_show(
        self, reservation_id: UUID, hotel_id: UUID, user_id: UUID
    ) -> Reservation:
        reservation = await self.repo.get_by_id(reservation_id, hotel_id)
        if not reservation:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")

        if reservation.status != "CONFIRMED":
            raise ValidationException(
                f"Cannot mark no-show for status: {reservation.status}",
                "INVALID_STATUS",
            )

        room = await self.room_repo.get_by_id(reservation.room_id, hotel_id)
        if room and room.current_status == "RESERVED":
            room.current_status = "AVAILABLE"
            await self.room_repo.update(room, current_status="AVAILABLE")

            history = RoomStatusHistory(
                hotel_id=hotel_id,
                room_id=room.id,
                status="AVAILABLE",
                changed_by=user_id,
                notes=f"Reservation {reservation.reservation_number} marked no-show",
            )
            self.session.add(history)

        reservation.status = "NO_SHOW"
        await self.repo.update(reservation, status="NO_SHOW")
        await self.session.flush()
        return reservation

    async def add_service(
        self, reservation_id: UUID, hotel_id: UUID, data: dict
    ) -> dict:
        reservation = await self.repo.get_by_id(reservation_id, hotel_id)
        if not reservation:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")

        if reservation.status in ("CANCELLED", "CHECKED_OUT", "NO_SHOW"):
            raise ValidationException(
                f"Cannot add service to reservation in status: {reservation.status}",
                "RESERVATION_LOCKED",
            )

        hotel_service_id = data["hotel_service_id"]
        hs_stmt = select(HotelService).where(
            HotelService.id == hotel_service_id, HotelService.hotel_id == hotel_id
        )
        hs_result = await self.session.execute(hs_stmt)
        hotel_service = hs_result.scalar_one_or_none()
        if not hotel_service:
            raise NotFoundException("Hotel service not found", "HOTEL_SERVICE_NOT_FOUND")

        quantity = data.get("quantity", 1)
        service_date_val = data.get("service_date") or date.today()
        unit_price = float(hotel_service.price)
        notes = data.get("notes")

        rs = await self.repo.add_service(
            reservation_id, hotel_id, hotel_service_id, quantity, unit_price, service_date_val, notes
        )

        return {
            "id": str(rs.id),
            "hotel_service_id": str(hotel_service_id),
            "quantity": rs.quantity,
            "unit_price": float(rs.unit_price),
            "total_price": float(rs.total_price),
            "service_date": str(rs.service_date),
            "notes": rs.notes,
        }

    async def get_reservation_services(
        self, reservation_id: UUID, hotel_id: UUID | None
    ) -> list[dict]:
        if hotel_id is None:
            from sqlalchemy import select as sa_select
            stmt = sa_select(Reservation).where(
                Reservation.id == reservation_id,
                Reservation.is_deleted.is_(False),
            )
            result = await self.session.execute(stmt)
            reservation = result.scalar_one_or_none()
        else:
            reservation = await self.repo.get_by_id(reservation_id, hotel_id)
        if not reservation:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
        return await self.repo.get_reservation_services(reservation_id, hotel_id)

    async def remove_service(
        self, service_id: UUID, reservation_id: UUID, hotel_id: UUID
    ) -> None:
        from app.infrastructure.database.models.service import ReservationService
        stmt = select(ReservationService).where(
            ReservationService.id == service_id,
            ReservationService.reservation_id == reservation_id,
            ReservationService.hotel_id == hotel_id,
        )
        result = await self.session.execute(stmt)
        rs = result.scalar_one_or_none()
        if not rs:
            raise NotFoundException("Service entry not found", "SERVICE_NOT_FOUND")
        await self.session.delete(rs)
        await self.session.flush()

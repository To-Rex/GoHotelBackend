from uuid import UUID
from datetime import date, datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundException, ConflictException, ValidationException, BadRequestException
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
from app.shared.utils import generate_code
from sqlalchemy import select


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
            hourly_rate = base_price / 24
            room_charge = round(hourly_rate * hours, 2)
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
            notes="Payment at reservation creation",
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
        final_discount_amount = discount_amount
        final_discount_percent = discount_percent
        if discount_percent > 0:
            final_discount_amount = round(room_charge * discount_percent / 100, 2)
        return final_discount_amount, final_discount_percent

    async def create_reservation(
        self, hotel_id: UUID, branch_id: UUID, data: dict, created_by: UUID
    ) -> Reservation:
        guest = await self.guest_repo.get_by_id(data["guest_id"], hotel_id)
        if not guest:
            raise NotFoundException("Guest not found", "GUEST_NOT_FOUND")

        room = await self.room_repo.get_by_id(data["room_id"], hotel_id)
        if not room:
            raise NotFoundException("Room not found", "ROOM_NOT_FOUND")

        if room.current_status in ("MAINTENANCE", "INSPECTION", "OUT_OF_SERVICE"):
            raise ConflictException(
                f"Room {room.room_number} is not available (status: {room.current_status})",
                "ROOM_NOT_AVAILABLE",
            )

        booking_type = data.get("booking_type", "DAILY")
        check_in = data["check_in_date"]
        check_out = data["check_out_date"]
        check_in_dt = data.get("check_in_datetime")
        check_out_dt = data.get("check_out_datetime")

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

        discount_amount, discount_percent = await self._compute_discount(
            room_charge,
            data.get("discount_amount", 0),
            data.get("discount_percent", 0),
        )

        total_amount = max(room_charge - discount_amount, 0)
        payment_amount = float(data.get("payment_amount", 0))
        payment_method = data.get("payment_method")

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

            await self._create_payment(
                hotel_id=hotel_id,
                invoice=invoice,
                amount=paid,
                payment_method=payment_method,
                created_by=created_by,
            )

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

        updatable = [
            "room_id", "booking_type", "check_in_date", "check_out_date",
            "check_in_datetime", "check_out_datetime", "adults", "children",
            "discount_amount", "discount_percent", "notes",
        ]
        update_data = {k: v for k, v in data.items() if k in updatable and v is not None}
        return await self.repo.update(reservation, **update_data)

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

        today = date.today()
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
    ) -> HousekeepingTask | None:
        """Bron uchun tozalash tunini yaratadi (agar hali mavjud bo'lmasa).

        Idempotent: shu bronga bog'langan bekor qilinmagan CLEANING tun bo'lsa,
        yangisi yaratilmaydi (avtomatik va qo'lda chiqish yo'llari dublikat
        yaratmasligi uchun).
        """
        existing = await self.session.execute(
            select(HousekeepingTask).where(
                HousekeepingTask.reservation_id == reservation.id,
                HousekeepingTask.task_type == "CLEANING",
                HousekeepingTask.status != "CANCELLED",
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

        # Tozalash tuni — mavjud bo'lmasa yaratiladi (idempotent).
        if create_cleaning_task:
            await self._ensure_cleaning_task(reservation, hotel_id, room, user_id)

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

    async def cancel_reservation(
        self, reservation_id: UUID, hotel_id: UUID, user_id: UUID, reason: str | None = None
    ) -> Reservation:
        reservation = await self.repo.get_by_id(reservation_id, hotel_id)
        if not reservation:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")

        if reservation.status in ("CHECKED_OUT", "CANCELLED", "NO_SHOW"):
            raise ValidationException(
                f"Cannot cancel reservation in status: {reservation.status}",
                "RESERVATION_LOCKED",
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
                notes=f"Reservation {reservation.reservation_number} cancelled",
            )
            self.session.add(history)

        reservation = await self.repo.cancel_reservation(
            reservation, reason or "Cancelled by user", user_id
        )
        await self.session.flush()
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

"""
Anna Hostel (code=123) — Main Branch filiali uchun qavatlar va xonalar qo'shish.
Faqat mavjud mehmonxona va filialga qo'shadi, yangi yaratmaydi.

Usage: python -m scripts.seed_anna_hostel
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.models import (
    Hotel, Branch, Floor, RoomType, HotelRoomType, Room,
)
from app.domain.enums import RoomStatus


async def seed():
    async with async_session_factory() as session:

        # ============================================
        # 1. Mavjud Anna Hostel (code=123)
        # ============================================
        r = await session.execute(select(Hotel).where(Hotel.code == "123"))
        hotel = r.scalar_one_or_none()
        if hotel is None:
            print("❌ Anna Hostel (code=123) topilmadi!")
            return
        print(f"✓ Mehmonxona: {hotel.name} (code={hotel.code})")
        hotel_id = hotel.id

        # ============================================
        # 2. MAIN BRANCH
        # ============================================
        r = await session.execute(
            select(Branch).where(
                Branch.hotel_id == hotel_id,
                Branch.is_main_branch == True,
            )
        )
        main_branch = r.scalar_one_or_none()
        if main_branch is None:
            print("❌ Main Branch topilmadi!")
            return
        print(f"✓ Filial: {main_branch.name} (code={main_branch.code})")
        branch_id = main_branch.id

        # ============================================
        # 3. QAVATLAR (FLOORS) — 3 ta qavat
        # ============================================
        floor_list: list[Floor] = []
        r = await session.execute(
            select(Floor).where(Floor.branch_id == branch_id)
        )
        existing_floors = {f.floor_number for f in r.scalars().all()}

        for fn in range(1, 4):
            if fn in existing_floors:
                r = await session.execute(
                    select(Floor).where(
                        Floor.branch_id == branch_id,
                        Floor.floor_number == fn,
                    )
                )
                f = r.scalar_one()
                floor_list.append(f)
                print(f"  {fn}-qavat allaqachon mavjud")
            else:
                f = Floor(
                    hotel_id=hotel_id,
                    branch_id=branch_id,
                    floor_number=fn,
                    name=f"{fn}-qavat",
                )
                session.add(f)
                await session.flush()
                floor_list.append(f)
                print(f"  {fn}-qavat yaratildi")
        print(f"✓ Jami qavatlar: {len(floor_list)} ta")

        # ============================================
        # 4. XONA TURI — mavjudini ol, yo'qsa yarat
        # ============================================
        rt_name = "Standart (ikki kishilik)"
        r = await session.execute(
            select(RoomType).where(RoomType.name == rt_name)
        )
        room_type = r.scalar_one_or_none()
        if room_type is None:
            room_type = RoomType(
                name=rt_name,
                base_price=380000,
                capacity=2,
                amenities=["WiFi", "TV", "Konditsioner", "Mini bar"],
                is_active=True,
            )
            session.add(room_type)
            await session.flush()
            print(f"✓ '{rt_name}' xona turi yaratildi")
        else:
            print(f"✓ '{rt_name}' xona turi mavjud")

        # Link room type to hotel
        r = await session.execute(
            select(HotelRoomType).where(
                HotelRoomType.hotel_id == hotel_id,
                HotelRoomType.room_type_id == room_type.id,
            )
        )
        if r.scalar_one_or_none() is None:
            session.add(HotelRoomType(hotel_id=hotel_id, room_type_id=room_type.id))
            print(f"✓ Xona turi mehmonxonaga bog'landi")

        # ============================================
        # 5. XONALAR (ROOMS) — 2 ta
        # ============================================
        r = await session.execute(
            select(Room).where(Room.branch_id == branch_id, Room.is_deleted == False)
        )
        existing_room_numbers = {rm.room_number for rm in r.scalars().all()}

        room_data = [
            (floor_list[0], "101", 380000, "Ko'cha tomonga"),
            (floor_list[0], "102", 380000, "Hovli tomonga"),
        ]

        rooms_created = 0
        for floor_, room_number, price, notes in room_data:
            if room_number in existing_room_numbers:
                print(f"  Xona {room_number} allaqachon mavjud")
                continue

            room = Room(
                hotel_id=hotel_id,
                branch_id=branch_id,
                floor_id=floor_.id,
                room_type_id=room_type.id,
                room_number=room_number,
                base_price=price,
                capacity=2,
                current_status=RoomStatus.AVAILABLE.value,
                notes=notes,
            )
            session.add(room)
            await session.flush()
            rooms_created += 1
            print(f"  Xona {room_number} yaratildi ({notes})")

        print(f"✓ Jami xonalar: {rooms_created} ta yaratildi")

        # ============================================
        await session.commit()

    print(f"""
{'=' * 50}
  ✅ BAJARILDI
{'=' * 50}
  Mehmonxona:  {hotel.name} (code={hotel.code})
  Filial:      {main_branch.name} (code={main_branch.code})
  Qavatlar:    {len(floor_list)} ta (1, 2, 3-qavat)
  Xonalar:     {rooms_created} ta yangi
""")


if __name__ == "__main__":
    asyncio.run(seed())

"""Bitta kamera agenti uchun qurilma tokeni yaratadi.

Ishlatish:
    python -m scripts.create_vision_device --hotel <HOTEL_ID> --name "Qabulxona PC"
    python -m scripts.create_vision_device --list --hotel <HOTEL_ID>
    python -m scripts.create_vision_device --revoke <DEVICE_ID>

Nega alohida token: agent oylab uzluksiz ishlaydi, xodim JWT'si esa ikki soatda
tugaydi. Token muddatsiz, mehmonxonaga bog'langan va bazada faqat SHA-256 xeshi
saqlanadi — ya'ni bu yerda BIR MARTA ko'rsatiladi va boshqa hech qayerdan
o'qib bo'lmaydi. Yo'qolsa yangisini yarating va eskisini bekor qiling.

Token agent mashinasida Windows Credential Manager'ga qo'yiladi:
    gohotels-vision secrets set api_token
va config.yaml'da `api_token: ${keyring:api_token}` bo'lib turadi — u yerda
ochiq matnda hech qachon yozilmaydi.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import secrets
import sys
from uuid import UUID

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.infrastructure.database.models.face_sighting import VisionDevice
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.session import async_session_factory


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create(hotel_id: UUID, name: str, branch_id: UUID | None) -> int:
    async with async_session_factory() as session:
        hotel = await session.get(Hotel, hotel_id)
        if hotel is None:
            print(f"error: {hotel_id} mehmonxonasi topilmadi", file=sys.stderr)
            return 1

        token = secrets.token_urlsafe(32)
        device = VisionDevice(
            hotel_id=hotel_id,
            branch_id=branch_id,
            name=name,
            token_hash=_hash_token(token),
            token_hint=token[-4:],
        )
        session.add(device)
        await session.commit()

        print()
        print(f"  Qurilma yaratildi : {device.name}")
        print(f"  Mehmonxona        : {hotel.name}")
        print(f"  Qurilma ID        : {device.id}")
        print()
        print(f"  TOKEN: {token}")
        print()
        print("  Bu token qayta ko'rsatilmaydi. Agent mashinasida saqlang:")
        print("      gohotels-vision secrets set api_token")
        print()
        return 0


async def list_devices(hotel_id: UUID | None) -> int:
    async with async_session_factory() as session:
        query = select(VisionDevice).order_by(VisionDevice.created_at.desc())
        if hotel_id is not None:
            query = query.where(VisionDevice.hotel_id == hotel_id)
        devices = (await session.execute(query)).scalars().all()

    if not devices:
        print("Qurilmalar yo'q.")
        return 0

    print(f"{'ID':38} {'NOM':24} {'HOLAT':8} {'TOKEN':6} OXIRGI ALOQA")
    for device in devices:
        state = "faol" if device.is_active else "bekor"
        seen = device.last_seen_at.isoformat(timespec="seconds") if device.last_seen_at else "-"
        print(
            f"{str(device.id):38} {device.name[:24]:24} {state:8} "
            f"...{device.token_hint:3} {seen}"
        )
    return 0


async def revoke(device_id: UUID) -> int:
    async with async_session_factory() as session:
        device = await session.get(VisionDevice, device_id)
        if device is None:
            print(f"error: {device_id} qurilmasi topilmadi", file=sys.stderr)
            return 1
        device.is_active = False
        await session.commit()
        print(f"'{device.name}' qurilmasi bekor qilindi — tokeni endi ishlamaydi.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hotel", type=UUID, help="mehmonxona ID")
    parser.add_argument("--name", help="qurilma nomi, masalan 'Qabulxona PC'")
    parser.add_argument("--branch", type=UUID, default=None, help="filial ID (ixtiyoriy)")
    parser.add_argument("--list", action="store_true", help="qurilmalar ro'yxati")
    parser.add_argument("--revoke", type=UUID, help="qurilma tokenini bekor qilish")
    args = parser.parse_args()

    if args.revoke:
        return asyncio.run(revoke(args.revoke))
    if args.list:
        return asyncio.run(list_devices(args.hotel))
    if not args.hotel or not args.name:
        parser.error("--hotel va --name kerak (yoki --list / --revoke)")
    return asyncio.run(create(args.hotel, args.name, args.branch))


if __name__ == "__main__":
    raise SystemExit(main())

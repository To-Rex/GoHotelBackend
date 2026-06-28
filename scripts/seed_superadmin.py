"""
Seed default Super Admin user into the database.
Run: python -m scripts.seed_superadmin
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.models.user import User
from app.infrastructure.auth.password import hash_password

DEFAULT_SUPERADMIN = {
    "username": "admin",
    "password": "admin123",
    "first_name": "Super",
    "last_name": "Admin",
}


async def seed():
    async with async_session_factory() as session:
        stmt = select(User).where(User.username == DEFAULT_SUPERADMIN["username"])
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            print(f"Super Admin already exists: {existing.username} (id={existing.id})")
            return

        admin = User(
            user_type="SUPER_ADMIN",
            username=DEFAULT_SUPERADMIN["username"],
            password_hash=hash_password(DEFAULT_SUPERADMIN["password"]),
            first_name=DEFAULT_SUPERADMIN["first_name"],
            last_name=DEFAULT_SUPERADMIN["last_name"],
            status="ACTIVE",
        )
        session.add(admin)
        await session.commit()
        print(f"Super Admin created: {admin.username} / {DEFAULT_SUPERADMIN['password']}")


if __name__ == "__main__":
    asyncio.run(seed())

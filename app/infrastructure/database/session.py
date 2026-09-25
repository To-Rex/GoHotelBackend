from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.config import settings
from app.core.database import engine_options

# Ulanish sozlamalari asosiy engine bilan bir xil (pre_ping, timeoutlar) —
# app/core/database.py:engine_options
engine = create_async_engine(str(settings.DATABASE_URL), **engine_options())

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    """Dependency that provides an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

from contextvars import ContextVar
from uuid import UUID

current_hotel_id: ContextVar[UUID | None] = ContextVar("current_hotel_id", default=None)
current_user_id: ContextVar[UUID | None] = ContextVar("current_user_id", default=None)
current_user_type: ContextVar[str | None] = ContextVar("current_user_type", default=None)


def set_tenant_context(hotel_id: UUID | None, user_id: UUID, user_type: str) -> None:
    current_hotel_id.set(hotel_id)
    current_user_id.set(user_id)
    current_user_type.set(user_type)


def get_current_hotel_id() -> UUID | None:
    return current_hotel_id.get()


def get_current_user_id() -> UUID:
    return current_user_id.get()

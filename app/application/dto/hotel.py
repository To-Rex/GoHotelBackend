from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.shared.validators import validate_email, validate_phone


class HotelCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    code: str = Field(..., min_length=2, max_length=10, pattern=r"^[A-Z0-9]+$")
    description: str | None = None
    stars: int = Field(default=3, ge=1, le=5)
    phone: str | None = None
    email: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None

    _validate_phone = field_validator("phone")(validate_phone)
    _validate_email = field_validator("email")(validate_email)


class HotelUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    stars: int | None = Field(None, ge=1, le=5)
    phone: str | None = None
    email: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    status: str | None = None


class HotelStatusUpdate(BaseModel):
    status: str = Field(..., pattern=r"^(ACTIVE|SUSPENDED|CLOSED)$")


class HotelResponse(BaseModel):
    id: UUID
    name: str
    code: str
    description: str | None
    stars: int
    phone: str | None
    email: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    country: str | None
    postal_code: str | None
    status: str
    settings: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class HotelBriefResponse(BaseModel):
    id: UUID
    name: str
    code: str
    stars: int
    status: str

    model_config = {"from_attributes": True}

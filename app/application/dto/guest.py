from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.shared.validators import validate_email, validate_phone


class GuestCreateRequest(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone: str | None = None
    email: str | None = None
    passport_number: str | None = None
    nationality: str | None = None
    birth_date: date | None = None
    id_document_type: str | None = None
    id_document_number: str | None = None
    address: str | None = None
    notes: str | None = None

    _validate_phone = field_validator("phone")(validate_phone)
    _validate_email = field_validator("email")(validate_email)


class GuestUpdateRequest(BaseModel):
    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    phone: str | None = None
    email: str | None = None
    passport_number: str | None = None
    nationality: str | None = None
    birth_date: date | None = None
    id_document_type: str | None = None
    id_document_number: str | None = None
    address: str | None = None
    notes: str | None = None


class GuestResponse(BaseModel):
    id: UUID
    hotel_id: UUID
    first_name: str
    last_name: str
    phone: str | None
    email: str | None
    passport_number: str | None
    nationality: str | None
    birth_date: date | None
    id_document_type: str | None
    id_document_number: str | None
    address: str | None
    notes: str | None
    is_deleted: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GuestSearchRequest(BaseModel):
    query: str = Field(..., min_length=2, description="Search by name, phone, email, or passport")

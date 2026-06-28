from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BranchCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    code: str = Field(..., min_length=2, max_length=20, pattern=r"^[A-Z0-9]+$")
    hotel_id: UUID | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    email: str | None = None


class BranchUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str | None = None


class BranchResponse(BaseModel):
    id: UUID
    hotel_id: UUID
    name: str
    code: str
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    country: str | None
    postal_code: str | None
    phone: str | None
    email: str | None
    is_main_branch: bool
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AmenityCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    icon: str | None = Field(None, max_length=50)


class AmenityUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    icon: str | None = Field(None, max_length=50)
    is_active: bool | None = None


class AmenityResponse(BaseModel):
    id: UUID
    name: str
    icon: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RoomAmenityRequest(BaseModel):
    amenity_id: UUID


class HotelAmenityRequest(BaseModel):
    amenity_id: UUID

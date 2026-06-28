from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class EmployeeCreateRequest(BaseModel):
    hotel_id: UUID
    branch_id: UUID
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6)
    email: str | None = None
    phone: str | None = None
    hire_date: date | None = None


class EmployeeUpdateRequest(BaseModel):
    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    email: str | None = None
    phone: str | None = None
    branch_id: UUID | None = None
    status: str | None = None


class AdminCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    email: str | None = None
    phone: str | None = None


class PermissionAssignRequest(BaseModel):
    permission_ids: list[UUID] = Field(..., min_length=1)


class PermissionGrantRequest(BaseModel):
    permission_id: UUID
    expires_at: datetime | None = None


class UserResponse(BaseModel):
    id: UUID
    user_type: str
    hotel_id: UUID | None
    branch_id: UUID | None
    username: str
    first_name: str
    last_name: str
    email: str | None
    phone: str | None
    status: str
    hire_date: date | None
    termination_date: date | None
    is_deleted: bool
    last_login_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserPermissionsResponse(BaseModel):
    user_id: UUID
    permissions: list[dict]

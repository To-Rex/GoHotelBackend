from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class RoomBrief(BaseModel):
    id: UUID
    room_number: str

    model_config = {"from_attributes": True}


class UserBrief(BaseModel):
    id: UUID
    first_name: str
    last_name: str

    model_config = {"from_attributes": True}


class BranchBrief(BaseModel):
    id: UUID
    name: str

    model_config = {"from_attributes": True}


class TaskCreateRequest(BaseModel):
    branch_id: UUID
    room_id: UUID
    task_type: str = Field(..., pattern=r"^(CLEANING|DEEP_CLEANING|MAINTENANCE|INSPECTION|TURN_DOWN)$")
    priority: str = Field(default="MEDIUM", pattern=r"^(LOW|MEDIUM|HIGH|URGENT)$")
    assigned_to: UUID | None = None
    notes: str | None = None
    scheduled_date: date | None = None


class TaskUpdateRequest(BaseModel):
    task_type: str | None = None
    priority: str | None = None
    assigned_to: UUID | None = None
    notes: str | None = None
    scheduled_date: date | None = None


class TaskStatusUpdateRequest(BaseModel):
    status: str = Field(..., pattern=r"^(OPEN|IN_PROGRESS|COMPLETED|CANCELLED)$")
    notes: str | None = None


class TaskAssignRequest(BaseModel):
    assigned_to: UUID


class TaskResponse(BaseModel):
    id: UUID
    hotel_id: UUID
    branch_id: UUID
    room_id: UUID
    task_type: str
    status: str
    priority: str
    assigned_to: UUID | None
    notes: str | None
    scheduled_date: date | None
    started_at: datetime | None
    completed_at: datetime | None
    created_by: UUID
    created_at: datetime
    photo_count: int = 0
    room: RoomBrief | None = None
    assigned_user: UserBrief | None = None
    branch: BranchBrief | None = None

    model_config = {"from_attributes": True}

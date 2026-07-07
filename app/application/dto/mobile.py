from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ChecklistItemOut(BaseModel):
    id: str
    title: str
    is_completed: bool

    model_config = {"from_attributes": True}


class MobileTaskResponse(BaseModel):
    id: str
    room_number: str
    floor: str
    room_type: str
    guest: str | None = None
    guest_status: str | None = None
    status: str
    progress: int
    deadline: str | None = None
    note: str | None = None
    is_urgent: bool
    checklist: list[ChecklistItemOut] = []

    model_config = {"from_attributes": True}


class ProblemCreateRequest(BaseModel):
    category: str
    description: str
    task_id: str | None = None
    room_number: str | None = None


class ProblemResponse(BaseModel):
    success: bool
    message: str
    report_id: str | None = None


class ProgressUpdateRequest(BaseModel):
    progress: int = Field(..., ge=0, le=100)


class ReportSubmitResponse(BaseModel):
    success: bool
    message: str
    task: MobileTaskResponse | None = None


class MobileNotificationResponse(BaseModel):
    id: str
    type: str
    title: str
    message: str
    room_number: str | None = None
    timestamp: str
    is_read: bool
    has_actions: bool

    model_config = {"from_attributes": True}

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SoftDeleteMixin:
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class FullMixin(UUIDPrimaryKeyMixin, TimestampMixin):
    pass


class BranchScoped:
    """Filialga tegishli jadval.

    Shu belgili modellarga so'rov filiali bo'yicha filtr avtomatik
    qo'shiladi va yangi yozuvga filial avtomatik yoziladi:
    app/infrastructure/tenant/branch_scope.py.

    Ustun shu yerda ham e'lon qilingan: filtr (`with_loader_criteria`)
    sharti avval mixin'ning o'zida tuziladi. Modellar o'z `branch_id`
    ustunini e'lon qiladi — u ustun turadi.
    """

    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )


class BranchShared:
    """Filialga tegishli, lekin bo'sh filial — "hamma filialga umumiy"
    (global xona turi, bir nechta filialga xizmat qiladigan kamera
    kompyuteri, hali biriktirilmagan kamera)."""

    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )


from typing import Generic, Sequence, TypeVar

from pydantic import BaseModel

from app.core.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

T = TypeVar("T")


class PaginationParams(BaseModel):
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    def get_offset(self) -> int:
        return (self.page - 1) * self.page_size

    def get_limit(self) -> int:
        return min(self.page_size, MAX_PAGE_SIZE)


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def create(
        cls, items: Sequence[T], total: int, params: PaginationParams
    ) -> "PaginatedResponse[T]":
        return cls(
            items=list(items),
            total=total,
            page=params.page,
            page_size=params.page_size,
            total_pages=(total + params.page_size - 1) // params.page_size
            if total > 0
            else 0,
        )

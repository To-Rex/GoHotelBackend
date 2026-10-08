"""Soxta sessiyalar uchun filial yordamchilari.

Filiallar ajratilgach sozlamalar filialda (`branches.settings`) turadi va
`settings_owner()` filialni so'raydi: `session.get(Branch, ...)` yoki
"asosiy filial" so'rovi (`SELECT ... FROM branches`). Bazasiz testlardagi
soxta sessiyalar shu ikkisiga mehmonxonaning yagona filialini qaytaradi —
uning sozlamasi mehmonxona sozlamasi bilan BIR XIL lug'at (o'qishda
avvalgidek; yozish filial obyektiga tushadi).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace


def fake_branch(hotel_id, settings, branch_id=None):
    return SimpleNamespace(
        id=branch_id or uuid.uuid4(),
        hotel_id=hotel_id,
        name="Asosiy filial",
        is_main_branch=True,
        settings=settings,
    )


class BranchResult:
    """`session.execute(select(Branch)...)` natijasi."""

    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value

    def scalar(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def all(self):
        return [self.value] if self.value is not None else []


def is_branch_query(statement) -> bool:
    sql = str(statement)
    return "FROM branches" in sql and "branches.name" in sql

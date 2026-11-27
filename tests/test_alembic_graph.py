"""Alembic zanjiri butun: har migratsiya noyob identifikatorga ega, zanjirning
bitta uchi bor.

Ishga tushirish: pytest tests/test_alembic_graph.py

Nega kerak: identifikatorlar qo'lda yoziladi. Ikki faylda bir xil identifikator
yoki ikkita "uch" (head) bo'lsa `alembic upgrade head` yiqiladi — deploy esa
buni yashiradi (Procfile: `alembic upgrade head || echo`), ya'ni yangi jadval
yaratilmay qoladi va xato faqat ishlab turgan tizimda bilinadi.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"
_REVISION = re.compile(r"^revision(?:\s*:\s*[^=]+)?\s*=\s*['\"]([^'\"]+)['\"]", re.M)
_DOWN = re.compile(r"^down_revision(?:\s*:\s*[^=]+)?\s*=\s*(.+?)$", re.M)
_QUOTED = re.compile(r"['\"]([^'\"]+)['\"]")


def _graph() -> tuple[Counter, dict[str, list[str]]]:
    ids: Counter = Counter()
    parents: dict[str, list[str]] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        revision = _REVISION.search(text)
        assert revision, f"{path.name}: `revision` topilmadi"
        down = _DOWN.search(text)
        ids[revision.group(1)] += 1
        parents[revision.group(1)] = _QUOTED.findall(down.group(1)) if down else []
    return ids, parents


def test_revision_ids_are_unique():
    ids, _ = _graph()
    assert [rid for rid, count in ids.items() if count > 1] == []


def test_chain_has_single_head_and_no_dangling_parents():
    ids, parents = _graph()
    referenced = {parent for group in parents.values() for parent in group}
    assert referenced - set(ids) == set()                     # har ota-migratsiya mavjud
    assert len(set(ids) - referenced) == 1                    # bitta uch — `upgrade head` ishlaydi

"""Prod Python 3.12 da ishga tushish kafolati (statik tekshiruv).

Lokal muhitda Python 3.14 bor: unda annotatsiyalar kechiktirib hisoblanadi
(PEP 649), ya'ni sinf o'zidan KEYIN e'lon qilingan turni maydon sifatida
ishlata oladi. Prod esa Python 3.12 (`.python-version`) — u yerda annotatsiya
sinf/funksiya yaratilayotganda hisoblanadi va bunday kod `NameError` bilan
backendni butunlay to'xtatadi (2026-10-10: `ExpectedCompanionIn` → 502).

Testlar 3.14 da o'tib ketadi, shuning uchun bu xato faqat shu statik
tekshiruv bilan ushlanadi: modul darajasidagi sinf maydonlari va funksiya
imzolarida ayni modulda KEYINROQ e'lon qilingan sinf nomi ishlatilmasin.
`from __future__ import annotations` bor modullar va satr annotatsiyalar
tekshirilmaydi (ular baribir kechiktiriladi).
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


def _names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _signature_annotations(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    a = fn.args
    params = [*a.posonlyargs, *a.args, *a.kwonlyargs]
    if a.vararg:
        params.append(a.vararg)
    if a.kwarg:
        params.append(a.kwarg)
    out = [p.annotation for p in params if p.annotation is not None]
    if fn.returns is not None:
        out.append(fn.returns)
    return out


def _forward_refs(path: Path, root: Path = APP) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.ImportFrom)
            and stmt.module == "__future__"
            and any(alias.name == "annotations" for alias in stmt.names)
        ):
            return []

    defined_at = {
        stmt.name: stmt.lineno for stmt in tree.body if isinstance(stmt, ast.ClassDef)
    }
    problems: list[str] = []

    def check(annotation: ast.AST, line: int, where: str) -> None:
        for name in _names(annotation):
            declared = defined_at.get(name)
            if declared is not None and declared > line:
                problems.append(
                    f"{path.relative_to(root.parent)}:{line} {where} ishlatadi `{name}` "
                    f"(u {declared}-qatorda, keyinroq e'lon qilingan)"
                )

    for stmt in tree.body:
        if isinstance(stmt, ast.ClassDef):
            for item in stmt.body:
                if isinstance(item, ast.AnnAssign):
                    check(item.annotation, item.lineno, f"{stmt.name} maydoni")
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for ann in _signature_annotations(item):
                        check(ann, item.lineno, f"{stmt.name}.{item.name}")
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for ann in _signature_annotations(stmt):
                check(ann, stmt.lineno, f"{stmt.name}()")
    return problems


def test_no_forward_class_references_without_future_annotations():
    problems: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        problems.extend(_forward_refs(path))
    assert not problems, "Python 3.12 da NameError beradi:\n" + "\n".join(problems)


def test_detector_catches_the_original_bug(tmp_path):
    """O'zini tekshirish: 502 ga sabab bo'lgan shakl ushlanadi."""
    root = tmp_path / "app"
    root.mkdir()
    bad = root / "dto.py"
    source = [
        "from pydantic import BaseModel",
        "class Create(BaseModel):",
        "    items: list[Item] = []",
        "class Item(BaseModel):",
        "    name: str",
    ]
    bad.write_text("\n".join(source) + "\n", encoding="utf-8")
    found = _forward_refs(bad, root)
    assert found and "`Item`" in found[0]

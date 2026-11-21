"""Excel (.xlsx) hisobot — saqlangan TO'LIQ qatorlardan (openpyxl).

Har varaq: sarlavha qatori (qalin, rangli fon), ustun kengligi matnga
moslanadi, filtr va muzlatilgan sarlavha; summa ustunlari raqam formatida
(1 250 000), sanalar — Excel sanasi. Pastda raqamli ustunlar jami.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.application.services.ai.render import (
    column_kind,
    label_for,
    to_local_date,
    to_number,
    value_label,
)

MAX_ROWS_PER_SHEET = 20000
_BAD_SHEET_CHARS = re.compile(r"[\[\]\*\?/\\:]")
_HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
_TOTAL_FILL = PatternFill("solid", fgColor="EAF1FB")
_THIN = Side(style="thin", color="D0D7E2")


def _sheet_title(name: str, used: set[str]) -> str:
    base = _BAD_SHEET_CHARS.sub(" ", (name or "Hisobot")).strip()[:28] or "Hisobot"
    title, n = base, 2
    while title.lower() in used:
        title = f"{base[:25]} {n}"
        n += 1
    used.add(title.lower())
    return title


def _cell_value(v, kind: str):
    if v is None or v == "":
        return None
    if kind in ("money", "number"):
        n = to_number(v)
        if n is not None:
            return int(n) if float(n).is_integer() else n
    if kind == "date":
        d = to_local_date(v)
        if d is not None:
            return d
    if kind == "datetime" and isinstance(v, str):
        try:
            moment = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            moment = None
        if moment is not None:
            from app.core.config import settings  # aylanma importdan qochish

            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            local = moment.astimezone(timezone.utc) + timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)
            return local.replace(tzinfo=None)
    if isinstance(v, bool):
        return "Ha" if v else "Yo'q"
    text = str(value_label(v))
    # Formula sifatida o'qilmasin (=, +, -, @ bilan boshlangan matn)
    if text[:1] in ("=", "+", "-", "@") and to_number(text) is None:
        text = "'" + text
    return text[:32000]


def build_workbook(sheets: list[dict], title: str | None = None) -> tuple[bytes, int]:
    """sheets: [{name, rows: [dict], columns: [key], labels: {key: label}}] -> (xlsx baytlari, jami qatorlar)."""
    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    total_rows = 0
    for sheet in sheets:
        rows = (sheet.get("rows") or [])[:MAX_ROWS_PER_SHEET]
        cols = sheet.get("columns") or (list(rows[0].keys()) if rows else [])
        labels = sheet.get("labels") or {}
        ws = wb.create_sheet(_sheet_title(sheet.get("name") or title or "Hisobot", used))
        kinds = {c: column_kind(rows, c) for c in cols}
        ws.append([label_for(c, labels) for c in cols])
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = _HEADER_FILL
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cell.border = Border(bottom=_THIN)
        ws.row_dimensions[1].height = 30
        for r in rows:
            ws.append([_cell_value(r.get(c), kinds[c]) for c in cols])
        total_rows += len(rows)
        # Formatlar va kenglik
        for idx, c in enumerate(cols, start=1):
            letter = get_column_letter(idx)
            kind = kinds[c]
            fmt = None
            if kind == "money":
                fmt = "#,##0"
            elif kind == "number":
                fmt = "#,##0.##"
            elif kind == "date":
                fmt = "DD.MM.YYYY"
            elif kind == "datetime":
                fmt = "DD.MM.YYYY HH:MM"
            width = len(label_for(c, labels))
            for row_cells in ws.iter_rows(min_row=2, max_row=min(ws.max_row, 400), min_col=idx, max_col=idx):
                cell = row_cells[0]
                if fmt and isinstance(cell.value, (int, float, date, datetime)):
                    cell.number_format = fmt
                val = cell.value
                shown = val.strftime("%d.%m.%Y %H:%M") if isinstance(val, datetime) else (
                    val.strftime("%d.%m.%Y") if isinstance(val, date) else (
                        f"{val:,.0f}" if isinstance(val, (int, float)) else str(val or "")
                    )
                )
                width = max(width, min(len(shown), 60))
            if fmt and ws.max_row > 400:
                for row_cells in ws.iter_rows(min_row=401, max_row=ws.max_row, min_col=idx, max_col=idx):
                    if isinstance(row_cells[0].value, (int, float, date, datetime)):
                        row_cells[0].number_format = fmt
            ws.column_dimensions[letter].width = max(8, min(width + 2, 62))
        if rows and cols:
            ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{len(rows) + 1}"
            ws.freeze_panes = "A2"
            # Jami qatori — summa va raqam ustunlari uchun (formula: filtrlansa ham to'g'ri)
            numeric = [i for i, c in enumerate(cols, start=1) if kinds[c] in ("money",)]
            if numeric:
                total_row = len(rows) + 2
                ws.cell(row=total_row, column=1, value="Jami").font = Font(bold=True)
                for i in numeric:
                    letter = get_column_letter(i)
                    cell = ws.cell(row=total_row, column=i, value=f"=SUBTOTAL(109,{letter}2:{letter}{len(rows) + 1})")
                    cell.number_format = "#,##0"
                    cell.font = Font(bold=True)
                for i in range(1, len(cols) + 1):
                    ws.cell(row=total_row, column=i).fill = _TOTAL_FILL
    if not wb.worksheets:
        wb.create_sheet("Hisobot")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), total_rows


def safe_filename(title: str | None) -> str:
    base = re.sub(r"[^\w\s\-'.]+", " ", title or "hisobot", flags=re.UNICODE).strip()
    base = re.sub(r"\s+", "_", base)[:80] or "hisobot"
    return f"{base}.xlsx"


__all__ = ["build_workbook", "safe_filename"]

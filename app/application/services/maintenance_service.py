"""Ma'lumotlarni tozalash (reset) xizmati — Sozlamalar sahifasidagi
"Xavfli hudud" uchun.

Barcha o'chirishlar BITTA mehmonxona doirasida (hotel_id bo'yicha) bajariladi —
boshqa mehmonxonalar ma'lumotlariga tegilmaydi. Ikki rejim:

- operational: bronlar, moliya, xo'jalik vazifalari, smenalar va
  kassa sessiyalari, tarix va bildirishnomalar o'chiriladi. Xodimlar, ruxsatlar
  va mehmonxona tuzilmasi (filial/qavat/xona/turlar/xizmatlar) saqlanadi.

MEHMONLAR HECH QACHON O'CHIRILMAYDI. Mehmonlar bazasi butun tizim uchun
umumiy (global): bu mehmonxona yaratgan mehmonning boshqa mehmonxonada
broni yoki hisob-fakturasi bo'lishi mumkin — o'chirishga urinish tashqi
kalit (RESTRICT) tufayli butun tozalashni yiqitardi. Bundan tashqari bu
qasddan qilingan qaror: mehmon tarixi mehmonxonaga emas, mehmonning
o'ziga tegishli va mehmonxona to'xtatilsa/tozalansa ham yashashda davom
etadi. Mehmonning hujjat fayllari va yuz profili ham shu sababdan
saqlanadi.
- full: yuqoridagilarga qo'shimcha barcha EMPLOYEE xodimlar (ruxsat
  biriktiruvlari va sessiyalari bilan) ham o'chiriladi. ADMIN/SUPER_ADMIN
  hisoblari hech qachon o'chirilmaydi — tizimga kirish saqlanib qoladi.
"""
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

# hotel_id ustuniga ega operatsion jadvallar — o'chirish tartibi muhim (FK):
# avval bolalar, keyin ota jadvallar
HOTEL_SCOPED_TABLES = [
    "problems",
    "housekeeping_tasks",
    "reservation_services",
    "payments",
    "invoice_line_items",
    "journal_entry_lines",
    "journal_entries",
    "invoices",
    "reservations",
    "notifications",
    # Qabulxona jurnallari — qo'ng'iroqlar va telefon skanerlari ham
    # operatsion tarix (mehmonga ishorasi SET NULL, o'zi bemalol o'chadi)
    "incoming_calls",
    "document_scans",
    "audit_logs",
    "reports",
    "room_status_history",
    "expenses",
    # Do'kon: sotuvlar, hisobdan chiqarishlar, partiyalar va mahsulotlar.
    #
    # Tartib muhim, chunki tashqi kalitlar RESTRICT:
    #   shop_sale_items.product_id -> shop_products  (sotuv qatorlari avval)
    #   shop_batches/writeoffs.product_id -> shop_products (CASCADE, lekin
    #   sonini alohida ko'rsatish uchun ataylab o'zimiz o'chiramiz)
    #
    # Mahsulotlar ham o'chiriladi: sotuvlar o'chib, partiyalar qolsa
    # ombordagi qoldiq o'chirilgan sotuvlar bilan kamaytirilgan holda
    # qolib ketardi — ya'ni "yangidek" holat emas, buzuq holat bo'lardi.
    "shop_sales",
    "shop_writeoffs",
    "shop_batches",
    "shop_products",
    # Smenalar va kassa sessiyalari — operatsion tarixning bir qismi. Ular
    # xodimlarga RESTRICT bilan bog'langan, shuning uchun bu qatorsiz "to'liq
    # tozalash" xodimlarni o'chirishga yetganda tashqi kalit xatosi bilan
    # to'xtardi: kassali rejimda ishlagan har qanday xodimning sessiyasi bor.
    "shift_sessions",
]

# Faqat operatsion fayllar; mehmonxonaning o'z fayllari ('Hotel') va
# mehmon hujjatlari ('guest' — mehmon bilan birga yashaydi) saqlanadi
FILE_ENTITY_TYPES = ["task", "task_report", "problem"]


class MaintenanceService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def reset_data(self, hotel_id: UUID, include_employees: bool) -> dict[str, int]:
        """Operatsion ma'lumotlarni (ixtiyoriy ravishda xodimlar bilan)
        tozalaydi va o'chirilgan yozuvlar sonini jadval bo'yicha qaytaradi."""
        s = self.session
        h = {"h": str(hotel_id)}
        deleted: dict[str, int] = {}

        # checklist_items da hotel_id yo'q — vazifalar orqali o'chiriladi
        result = await s.execute(
            text(
                "DELETE FROM checklist_items WHERE task_id IN "
                "(SELECT id FROM housekeeping_tasks WHERE hotel_id = :h)"
            ),
            h,
        )
        deleted["checklist_items"] = result.rowcount or 0

        # shop_sale_items da hotel_id yo'q — sotuv orqali o'chiriladi.
        # (FK CASCADE ham o'chirardi, lekin unda soni hisobotda ko'rinmasdi)
        result = await s.execute(
            text(
                "DELETE FROM shop_sale_items WHERE sale_id IN "
                "(SELECT id FROM shop_sales WHERE hotel_id = :h)"
            ),
            h,
        )
        deleted["shop_sale_items"] = result.rowcount or 0

        # invoice_items ham hisob-faktura orqali (hotel_id ustuni kafolatlanmagan)
        result = await s.execute(
            text(
                "DELETE FROM invoice_items WHERE invoice_id IN "
                "(SELECT id FROM invoices WHERE hotel_id = :h)"
            ),
            h,
        )
        deleted["invoice_items"] = result.rowcount or 0

        for table in HOTEL_SCOPED_TABLES:
            result = await s.execute(
                text(f"DELETE FROM {table} WHERE hotel_id = :h"), h
            )
            deleted[table] = result.rowcount or 0

        files_stmt = text(
            "DELETE FROM file_attachments WHERE hotel_id = :h AND entity_type IN :et"
        ).bindparams(bindparam("et", expanding=True))
        result = await s.execute(files_stmt, {**h, "et": FILE_ENTITY_TYPES})
        deleted["file_attachments"] = result.rowcount or 0

        # Xonalar yangidek — barchasi bo'sh holatga qaytadi
        result = await s.execute(
            text(
                "UPDATE rooms SET current_status = 'AVAILABLE' "
                "WHERE hotel_id = :h AND current_status <> 'AVAILABLE'"
            ),
            h,
        )
        deleted["rooms_reset"] = result.rowcount or 0

        if include_employees:
            # Xodim yuklagan qolgan fayllar (FK bloklamasligi uchun)
            result = await s.execute(
                text(
                    "DELETE FROM file_attachments WHERE uploaded_by IN "
                    "(SELECT id FROM users WHERE hotel_id = :h AND user_type = 'EMPLOYEE')"
                ),
                h,
            )
            deleted["file_attachments"] += result.rowcount or 0

            result = await s.execute(
                text("DELETE FROM user_permissions WHERE hotel_id = :h"), h
            )
            deleted["user_permissions"] = result.rowcount or 0

            result = await s.execute(
                text(
                    "DELETE FROM user_sessions WHERE user_id IN "
                    "(SELECT id FROM users WHERE hotel_id = :h AND user_type = 'EMPLOYEE')"
                ),
                h,
            )
            deleted["user_sessions"] = result.rowcount or 0

            # Faqat EMPLOYEE o'chiriladi — ADMIN/SUPER_ADMIN doim saqlanadi
            result = await s.execute(
                text(
                    "DELETE FROM users WHERE hotel_id = :h AND user_type = 'EMPLOYEE'"
                ),
                h,
            )
            deleted["users"] = result.rowcount or 0

        await s.flush()
        return deleted

"""Tozalash qamrovi.

Bu ro'yxat "Xavfli hudud" tugmasi nimani o'chirishini belgilaydi, shuning uchun
undan tushib qolgan jadval jimgina eski ma'lumot bo'lib qoladi — yoki, tashqi
kalit bilan bog'langan bo'lsa, tozalashning o'zini yiqitadi.
"""
from app.application.services.maintenance_service import (
    HOTEL_SCOPED_TABLES,
    MaintenanceService,
)


def test_shift_history_is_cleared_with_operational_data():
    """Smenalar operatsion tarixning bir qismi — kassa sessiyalari bilan birga."""
    assert "shift_sessions" in HOTEL_SCOPED_TABLES


def test_shift_sessions_are_removed_before_employees():
    """Xodimlarni o'chirishdan OLDIN bo'lishi shart.

    `shift_sessions.user_id` xodimga RESTRICT bilan bog'langan: kassali rejimda
    ishlagan har qanday xodimning sessiyasi bo'ladi, shuning uchun sessiyalar
    qolsa "to'liq tozalash" tashqi kalit xatosi bilan to'xtaydi. Xodimlar
    HOTEL_SCOPED_TABLES dan KEYIN o'chiriladi, ya'ni ro'yxatda bo'lishining
    o'zi tartibni kafolatlaydi.
    """
    assert "shift_sessions" in HOTEL_SCOPED_TABLES
    assert "users" not in HOTEL_SCOPED_TABLES


def test_hotel_structure_survives_an_operational_reset():
    """Mehmonxona tuzilmasi va xodimlar hech qachon bu ro'yxatga tushmasin."""
    protected = {
        "hotels", "branches", "floors", "rooms", "room_types",
        "services", "hotel_services", "amenities", "users", "permissions",
    }
    assert protected.isdisjoint(HOTEL_SCOPED_TABLES)


def test_every_table_is_listed_once():
    assert len(HOTEL_SCOPED_TABLES) == len(set(HOTEL_SCOPED_TABLES))


def test_reset_reports_a_count_for_every_scoped_table():
    """Natija jadvalidagi kalitlar ro'yxatdan kelib chiqadi — frontenddagi
    nomlar bilan mos bo'lishi uchun ular o'zgarmasligi kerak."""
    assert all(isinstance(name, str) and name.islower() for name in HOTEL_SCOPED_TABLES)
    assert hasattr(MaintenanceService, "reset_data")


class TestShopIsCleared:
    """Do'kon ma'lumotlari ham operatsion tarixning bir qismi.

    Sotuvlar o'chib mahsulot va partiyalar qolsa, ombordagi qoldiq
    o'chirilgan sotuvlar bilan kamaytirilgan holda qolib ketardi — ya'ni
    "yangidek" emas, buzuq holat.
    """

    def test_every_shop_table_is_cleared(self):
        for table in ("shop_sales", "shop_writeoffs", "shop_batches", "shop_products"):
            assert table in HOTEL_SCOPED_TABLES

    def test_sales_are_removed_before_products(self):
        """`shop_sale_items.product_id` RESTRICT bilan bog'langan: mahsulot
        avval o'chirilsa tashqi kalit xatosi bilan to'xtardi."""
        order = HOTEL_SCOPED_TABLES
        assert order.index("shop_sales") < order.index("shop_products")
        assert order.index("shop_batches") < order.index("shop_products")
        assert order.index("shop_writeoffs") < order.index("shop_products")

    def test_sale_items_are_removed_explicitly(self):
        """`shop_sale_items` da hotel_id yo'q — u sotuv orqali o'chiriladi.

        FK CASCADE ham o'chirardi, lekin unda soni natija jadvalida
        ko'rinmasdi.
        """
        import inspect

        source = inspect.getsource(MaintenanceService.reset_data)
        assert "shop_sale_items" in source
        assert 'deleted["shop_sale_items"]' in source

    def test_shop_tables_come_before_employees(self):
        """`shop_sales.created_by` xodimga RESTRICT bilan bog'langan.

        Ilgari do'kon jadvallari umuman o'chirilmasdi va do'konda savdo
        qilgan xodim bo'lsa "to'liq tozalash" tashqi kalit xatosi bilan
        to'xtardi.
        """
        assert "users" not in HOTEL_SCOPED_TABLES
        assert "shop_sales" in HOTEL_SCOPED_TABLES

    def test_hotel_structure_still_survives(self):
        """Do'kon qo'shilgani bilan mehmonxona tuzilmasi tegilmaydi."""
        protected = {"hotels", "rooms", "room_types", "services", "users"}
        assert protected.isdisjoint(HOTEL_SCOPED_TABLES)

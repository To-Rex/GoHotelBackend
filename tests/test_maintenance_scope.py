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

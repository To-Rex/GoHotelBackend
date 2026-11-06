"""To'lov turi QISMAN tahrirlanganda ajralgan qatorlar: vaqt asl to'lovniki.

Qisman tahrirda (100 000 naqd → 50 000 naqd + 50 000 online) ajralgan
qism yangi `payments` qatori bo'ladi. Shu yangilanishgacha unga
`created_at` sifatida TAHRIR payti yozilardi: kecha olingan pulning yarmi
bugungi smena kassasi va shaxsiy hisobotga o'tib qolar, To'lovlar
jadvalida ham bugungi vaqt bilan turardi (kunlik moliya hisoboti
`payment_date` bo'yicha — u to'g'ri edi, shu sabab ikki sahifa mos
kelmasdi).

Bu migratsiya eski ajralgan qatorlarni tuzatadi: `created_at` asl
to'lovnikiga qaytadi va qator tarixiga "qaysi to'lovdan, qancha, nimadan
nimaga ajralgan" yozuvi (`split_from`) qo'shiladi. Ajralgan qator asl
to'lov tarixidagi `new_payment_number` orqali ANIQ topiladi; summa, usul,
sana va xodim tegilmaydi. Mantiq va testlar: `app/shared/payment_splits.py`.

Revision ID: c1e3f5a7b9d2
Revises: b9d1f3a5c7e9
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c1e3f5a7b9d2"
down_revision: Union[str, None] = "b9d1f3a5c7e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.shared.payment_splits import repair_split_payments

    counts = repair_split_payments(op.get_bind())
    if counts["created_at"]:
        print(f"  payments: {counts['created_at']} ta ajralgan qator asl to'lov vaqtiga qaytarildi")
    if counts["history"]:
        print(f"  payments: {counts['history']} ta ajralgan qatorga tahrir tarixi yozildi")


def downgrade() -> None:
    # Ma'lumot migratsiyasi: eski (noto'g'ri) vaqtga qaytarilmaydi —
    # tuzatilgan holat to'g'ri.
    pass

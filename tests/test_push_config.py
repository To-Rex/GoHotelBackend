#!/usr/bin/env python3
"""Firebase kalitini panel orqali boshqarish — sof mantiq.

Ishga tushirish:  python tests/test_push_config.py

Tekshirilayotgani: kalit tekshiruvi (yaroqsiz fayl saqlangunga qadar
rad etilishi) va shifrlash aylanishi (bazadagi qiymat ochiq matn
bo'lmasligi).
"""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Model ro'yxati birinchi yuklanadi — superadmin.models to'g'ridan-to'g'ri
# import qilinsa, ikkalasi bir-birini kutib aylanma import chiqardi
import app.infrastructure.database.models  # noqa: E402,F401
from app.core.exceptions import ValidationException  # noqa: E402
from app.superadmin.push_config_service import (  # noqa: E402
    decrypt,
    encrypt,
    validate_credentials,
)

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label:<52} {str(got)[:40]}")
    else:
        fail += 1
        print(f"  XATO {label:<52} kutilgan {want}, chiqdi {got}")


def error_code(raw):
    try:
        validate_credentials(raw)
        return None
    except ValidationException as exc:
        return exc.error_code


GOOD = {
    "type": "service_account",
    "project_id": "demo-project",
    "private_key": "-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----\n",
    "client_email": "push@demo-project.iam.gserviceaccount.com",
}

print("--- kalit tekshiruvi ---")
normalized = validate_credentials(json.dumps(GOOD))
check("to'g'ri kalit qabul qilinadi", json.loads(normalized)["project_id"], "demo-project")
check(
    "base64 ham qabul qilinadi",
    json.loads(
        validate_credentials(base64.b64encode(json.dumps(GOOD).encode()).decode())
    )["client_email"],
    GOOD["client_email"],
)
check("bo'sh kalit", error_code("   "), "EMPTY_CREDENTIALS")
check("JSON emas", error_code("bu json emas"), "INVALID_CREDENTIALS")
check("buzuq JSON", error_code("{oops"), "INVALID_CREDENTIALS")
check("ro'yxat emas obyekt kerak", error_code("[1,2]"), "INVALID_CREDENTIALS")
check(
    "maydonlar yetishmasa",
    error_code(json.dumps({"type": "service_account", "project_id": "x"})),
    "MISSING_FIELDS",
)
check(
    "service_account bo'lmasa",
    error_code(json.dumps({**GOOD, "type": "authorized_user"})),
    "NOT_SERVICE_ACCOUNT",
)

print("--- shifrlash ---")
secret = json.dumps(GOOD)
token = encrypt(secret)
check("shifrlangan qiymat ochiq matn emas", "private_key" in token, False)
check("ochilganda asliga qaytadi", decrypt(token), secret)
check("buzilgan token None beradi", decrypt("buzilgan-token"), None)
check("har safar boshqa shifr (nonce)", encrypt(secret) != token, True)

print(f"\nJami: {ok} ok, {fail} xato")
raise SystemExit(1 if fail else 0)

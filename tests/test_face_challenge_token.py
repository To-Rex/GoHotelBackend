#!/usr/bin/env python3
"""Ikki bosqichli kirishdagi challenge token.

Ishga tushirish:  python tests/test_face_challenge_token.py

Nega kerak: bu token parol bilan yuz tekshiruvi orasidagi yagona bog'lovchi.
Agar u boshqa turdagi token bilan almashtirilsa yoki muddati e'tiborga
olinmasa, yuz tekshiruvini butunlay aylanib o'tish yo'li ochilardi.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jose import jwt  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.infrastructure.auth.jwt import (  # noqa: E402
    FACE_CHALLENGE_EXPIRE_MINUTES,
    create_access_token,
    create_face_challenge_token,
    create_refresh_token,
    decode_face_challenge_token,
)

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label:<56} {str(got)[:24]}")
    else:
        fail += 1
        print(f"  XATO {label:<56} kutilgan {want}, chiqdi {got}")


user_id = str(uuid.uuid4())

print("--- to'g'ri token ---")
token = create_face_challenge_token(user_id)
check("foydalanuvchi ID'si qaytadi", decode_face_challenge_token(token), user_id)

payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
check("turi face_challenge", payload["type"], "face_challenge")
lifetime = payload["exp"] - int(datetime.now(timezone.utc).timestamp())
check(
    "muddati qisqa (5 daqiqa atrofida)",
    abs(lifetime - FACE_CHALLENGE_EXPIRE_MINUTES * 60) <= 5,
    True,
)

print("--- boshqa turdagi tokenlar qabul qilinmaydi ---")
# Bu eng muhim tekshiruv: access token bilan yuz bosqichini o'tkazib
# yuborish mumkin bo'lmasligi kerak
data = {"sub": user_id, "user_type": "EMPLOYEE", "jti": "x"}
check("access token rad etiladi", decode_face_challenge_token(create_access_token(data)), None)
check("refresh token rad etiladi", decode_face_challenge_token(create_refresh_token(data)), None)

print("--- yaroqsiz tokenlar ---")
check("bo'sh satr", decode_face_challenge_token(""), None)
check("axlat matn", decode_face_challenge_token("abc.def.ghi"), None)
check(
    "boshqa kalit bilan imzolangan",
    decode_face_challenge_token(
        jwt.encode(
            {"sub": user_id, "type": "face_challenge",
             "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            "boshqa-maxfiy-kalit",
            algorithm=settings.JWT_ALGORITHM,
        )
    ),
    None,
)
check(
    "muddati o'tgan",
    decode_face_challenge_token(
        jwt.encode(
            {"sub": user_id, "type": "face_challenge",
             "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
    ),
    None,
)
check(
    "turi yo'q token",
    decode_face_challenge_token(
        jwt.encode(
            {"sub": user_id,
             "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
    ),
    None,
)

print(f"\nJami: {ok} ok, {fail} xato")
raise SystemExit(1 if fail else 0)

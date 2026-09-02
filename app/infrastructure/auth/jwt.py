from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from app.core.config import settings


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


#: Parol to'g'ri kelganidan keyin yuz tekshiruvigacha beriladigan qisqa
#: muddatli token. U bilan HECH QANDAY so'rov bajarib bo'lmaydi — faqat
#: ikkinchi bosqichni yakunlash uchun. Muddati qisqa: parolni bilgan, lekin
#: yuz tekshiruvidan o'tolmagan odam uzoq vaqt urinib turmasligi kerak.
FACE_CHALLENGE_EXPIRE_MINUTES = 5


def create_face_challenge_token(user_id: str) -> str:
    """Ikkinchi bosqich uchun vaqtinchalik token."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=FACE_CHALLENGE_EXPIRE_MINUTES
    )
    return jwt.encode(
        {"sub": user_id, "exp": expire, "type": "face_challenge"},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_face_challenge_token(token: str) -> str | None:
    """Tokendan foydalanuvchi ID'si. Yaroqsiz yoki boshqa turdagi bo'lsa None.

    `type` tekshiruvi muhim: aks holda oddiy access token ham shu yerga
    berilib, yuz tekshiruvini aylanib o'tish yo'li ochilardi.
    """
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError:
        return None
    if payload.get("type") != "face_challenge":
        return None
    return payload.get("sub")


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None

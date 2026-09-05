from __future__ import annotations

import json

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    APP_NAME: str = "GoHotel"
    APP_VERSION: str = "1.0.0"
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    SECRET_KEY: str = "change-me-to-a-long-random-string-at-least-64-chars"
    HOST: str = "0.0.0.0"
    PORT: int = 8000


class DatabaseSettings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://postgres:toor@localhost:5432/hotels-db"
    DATABASE_URL_SYNC: str = "postgresql://postgres:toor@localhost:5432/hotels-db"
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_RECYCLE: int = 3600


class JWTSettings(BaseSettings):
    JWT_SECRET_KEY: str = "change-me-jwt-secret-at-least-64-chars"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7


class MinIOSettings(BaseSettings):
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_SECURE: bool = False
    MINIO_BUCKET_DOCUMENTS: str = "hotel-documents"
    MINIO_BUCKET_GUESTS: str = "hotel-guests"


class RateLimitSettings(BaseSettings):
    RATE_LIMIT_LOGIN_PER_IP: int = 5
    RATE_LIMIT_LOGIN_PER_USER: int = 10
    RATE_LIMIT_WINDOW_SECONDS: int = 60


class CORSSettings(BaseSettings):
    CORS_ORIGINS: list[str] = ["*"]
    # Dokploy (sslip.io) subdomenlaridagi frontendlarga hamda ishlab chiqarish
    # domeni gohotels.uz (va uning subdomenlariga) env ro'yxatidan qat'i nazar
    # ruxsat beriladi — yangi frontend deploy bo'lganda CORS sinmasligi uchun.
    CORS_ORIGIN_REGEX: str = r"https://(.*\.sslip\.io|([A-Za-z0-9-]+\.)*gohotels\.uz)$"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: object) -> list[str]:
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            return json.loads(v)
        return []


class LogSettings(BaseSettings):
    LOG_LEVEL: str = "DEBUG"
    LOG_FORMAT: str = "json"


class WebAuthnSettings(BaseSettings):
    """Face ID/Windows Hello (passkey) autentifikatsiyasi sozlamalari.

    WebAuthn kredensiallari yagona RP ID'ga bog'lanadi va faqat shu RP ID bilan
    mos keladigan origin'lardan ishlaydi. Lokal ishlab chiqishda frontend
    localhost'da ochilsa, .env orqali quyidagilarni ustidan yozing:
        WEBAUTHN_RP_ID=localhost
        WEBAUTHN_ORIGINS=["http://localhost:5173","http://localhost:8004"]
    """

    WEBAUTHN_RP_ID: str = "gohotels.uz"
    WEBAUTHN_RP_NAME: str = "GoHotel"
    WEBAUTHN_ORIGINS: list[str] = ["https://gohotels.uz", "https://www.gohotels.uz"]

    @field_validator("WEBAUTHN_ORIGINS", mode="before")
    @classmethod
    def parse_webauthn_origins(cls, v: object) -> list[str]:
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            return json.loads(v)
        return []


class FirebaseSettings(BaseSettings):
    """Firebase Cloud Messaging (push notification) sozlamalari.

    FIREBASE_ENABLED=false yoki kalit fayli topilmasa — push jimgina o'chadi
    va boshqa hech bir funksionallikka ta'sir qilmaydi.
    """

    FIREBASE_ENABLED: bool = True
    # Service-account JSON kalitiga yo'l (loyiha ildizidan nisbiy yoki absolyut).
    FIREBASE_CREDENTIALS_FILE: str = "secrets/gohotel-firebase-service-account.json"
    # Muqobil: service-account'ni butun JSON matn (yoki base64) sifatida env orqali
    # berish. Deploy platformalarida (Railway/Render va h.k.) faylni commit qilmasdan
    # sirni env-var qilib qo'yish uchun. Berilsa, FIREBASE_CREDENTIALS_FILE'dan ustun.
    FIREBASE_CREDENTIALS_JSON: str | None = None


class AutomationSettings(BaseSettings):
    """Bron chiqishini avtomatlashtirish (fon vazifasi) sozlamalari.

    Oqim: chiqish vaqtidan LEAD daqiqa oldin farroshga tozalash tuni yaratiladi;
    chiqish vaqti kelganda xona "CLEANING" bo'ladi va bron darhol "CHECKED_OUT"
    qilinadi (GRACE=0). Tozalash vazifasi bron holatidan mustaqil davom etadi.
    """

    # Fon rejalashtiruvchisini yoqish/o'chirish
    AUTO_CHECKOUT_ENABLED: bool = True
    # Har necha soniyada bir marta tekshiriladi
    AUTO_CHECKOUT_INTERVAL_SECONDS: int = 60
    # Chiqish vaqtidan necha daqiqa oldin tozalash tuni yaratiladi (farroshga yuboriladi)
    HOUSEKEEPING_LEAD_MINUTES: int = 5
    # Chiqish vaqtidan necha daqiqa keyin bron CHECKED_OUT qilinadi.
    # 0 — vaqt tugashi bilanoq (keyingi tekshiruv siklida) chiqariladi;
    # tozalash vazifasi bunga bog'liq emas, o'z holicha davom etadi
    AUTO_CHECKOUT_GRACE_MINUTES: int = 0
    # Xona CLEANING da turib, unga ochiq tozalash vazifasi bo'lmasa — necha
    # daqiqadan keyin vazifa qayta yaratiladi. Bunday xona "yetim" qoladi:
    # xonani CLEANING dan chiqaradigan yagona narsa — tozalash vazifasining
    # yakunlanishi, vazifa yo'q bo'lsa esa xona abadiy o'sha holatda qoladi.
    # 0 — tekshiruvni butunlay o'chiradi.
    CLEANING_REPAIR_GRACE_MINUTES: int = 10
    # Kunlik (soatsiz) bronlar uchun standart chiqish soati (mahalliy vaqt, 0-23)
    DEFAULT_CHECKOUT_HOUR: int = 12
    # Soatlik bronlar orasidagi majburiy tanaffus (daqiqalarda) — mijoz chiqib
    # ketgach xonani tayyorlash uchun; keyingi bron shu vaqtdan keyin boshlanadi
    HOURLY_TURNOVER_MINUTES: int = 15
    # Mahalliy vaqt UTC dan necha daqiqa oldinda (O'zbekiston = +300). Bron
    # datetime'lari mahalliy "devor soati" sifatida saqlanadi; solishtirish shu
    # ofset bilan mahalliy hozirgi vaqtga keltiriladi.
    APP_TZ_OFFSET_MINUTES: int = 300


class SmsSettings(BaseSettings):
    """Xabarchi SMS xizmati (o'z Android telefon + SIM orqali yuboradi).

    API kalitlari bu yerda EMAS — har filialning kaliti bazada shifrlangan
    holda saqlanadi va sozlamalar sahifasidan kiritiladi. Bu yerda faqat
    xizmat manzili.
    """

    SMS_API_BASE: str = (
        "https://manager-xabar-web-udyrjh-9d78c6-13-140-185-49.sslip.io/api/v1"
    )


class Settings(
    AppSettings,
    DatabaseSettings,
    JWTSettings,
    MinIOSettings,
    RateLimitSettings,
    CORSSettings,
    LogSettings,
    FirebaseSettings,
    AutomationSettings,
    WebAuthnSettings,
    SmsSettings,
):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


settings = Settings()

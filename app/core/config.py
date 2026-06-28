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
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

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


class Settings(
    AppSettings,
    DatabaseSettings,
    JWTSettings,
    MinIOSettings,
    RateLimitSettings,
    CORSSettings,
    LogSettings,
):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


settings = Settings()

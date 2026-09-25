"""
GoHotel ERP Backend — FastAPI Application
"""
import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import _get_engine, check_database, dispose_engine
from app.core.db_errors import db_unavailable_payload, is_connection_lost, short_error
from app.core.exceptions import AppException
from app.core.scheduler import start_scheduler, stop_scheduler
from app.presentation.api.v1.router import api_router

logging.basicConfig(level=logging.DEBUG if settings.APP_DEBUG else logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _get_engine()
    # Baza hozir yotgan bo'lsa ham server ishga tushadi: so'rovlar 503
    # DB_UNAVAILABLE qaytaradi, baza tiklangach pool o'zi ulanadi
    # (pre_ping). Ilgari bu yerda yiqilib, jarayon qayta-qayta o'lardi.
    db_ok, db_error = await check_database()
    if db_ok:
        logger.info("Ma'lumotlar bazasi ulanishi tekshirildi")
    else:
        logger.warning(
            "Ma'lumotlar bazasi hozircha javob bermaydi — server baribir ishga "
            "tushadi, so'rovlar 503 DB_UNAVAILABLE qaytaradi: %s",
            db_error,
        )
    # Panel orqali yuklangan Firebase kaliti (bo'lsa) qo'llanadi — baza
    # yo'q bo'lsa o'zi jim o'tadi
    from app.superadmin.push_config_service import apply_stored_credentials

    await apply_stored_credentials()
    # Bron chiqishini avtomatlashtiruvchi fon vazifasini ishga tushiramiz
    start_scheduler()
    yield
    await stop_scheduler()
    await dispose_engine()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.APP_DEBUG else None,
    redoc_url="/redoc" if settings.APP_DEBUG else None,
    lifespan=lifespan,
)

# Ishlab chiqarish domeni env sozlamalaridan qat'i nazar DOIM ruxsat etiladi —
# CORS_ORIGINS ro'yxati (masalan, faqat localhost) tor bo'lib qolsa ham
# gohotels.uz saytida CORS xatosi chiqmasligi uchun. Mavjud ro'yxat saqlanadi,
# faqat qo'shimcha domenlar qo'shiladi (dublikatlar olib tashlanadi).
PRODUCTION_ORIGINS = ["https://gohotels.uz", "https://www.gohotels.uz"]
_allow_origins = list(dict.fromkeys([*settings.CORS_ORIGINS, *PRODUCTION_ORIGINS]))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # Sahifalanadigan ro'yxatlar jami qatorlar sonini shu sarlavhada
    # qaytaradi. `allow_headers` so'rov sarlavhalariga tegishli — javob
    # sarlavhasini brauzerga ko'rsatish uchun uni ALOHIDA ochish kerak,
    # aks holda mijoz kodi uni umuman ko'rmaydi.
    expose_headers=["X-Total-Count"],
)

# So'rovlar jurnali — panel uchun oxirgi 500 ta so'rov XOTIRADA turadi
# (disk/baza yo'q, qayta ishga tushishda bo'shaydi). Faqat kichik JSON
# tanalari o'qiladi, fayl yuklash va oqimlar tegilmaydi — performansega
# ta'siri deyarli nol. Batafsil: app/superadmin/api_log.py
from app.superadmin import api_log  # noqa: E402

app.middleware("http")(api_log.middleware)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "error_code": exc.error_code},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    # Baza bilan aloqa uzilgani dastur xatosi emas — 503 va qisqa log.
    # get_db bularni o'zi 503 ga aylantiradi; bu yerga sessiyani o'zi
    # ochadigan joylardan (fon xizmatlari, panel) chiqqanlari tushadi.
    if is_connection_lost(exc):
        logger.warning(
            "Ma'lumotlar bazasi mavjud emas (%s %s): %s",
            request.method,
            request.url.path,
            short_error(exc),
        )
        return JSONResponse(status_code=503, content=db_unavailable_payload())
    logger.error(
        "Unhandled exception on %s %s: %s\n%s",
        request.method,
        request.url.path,
        exc,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error_code": "INTERNAL_ERROR",
            "message": str(exc) if settings.APP_DEBUG else "An unexpected error occurred",
        },
    )


@app.get("/health")
async def health_check():
    # `status` jarayon tirikligini bildiradi (platforma shunga qaraydi);
    # baza holati alohida — baza yotganda serverni qayta ishga tushirish
    # foyda bermaydi, lekin sababi shu yerda ko'rinishi kerak
    db_ok, db_error = await check_database()
    payload = {
        "status": "ok",
        "version": settings.APP_VERSION,
        "env": settings.APP_ENV,
        "database": "ok" if db_ok else "down",
    }
    if not db_ok:
        payload["database_error"] = db_error
    return payload


app.include_router(api_router)

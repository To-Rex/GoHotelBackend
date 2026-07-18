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
from app.core.database import _get_engine, dispose_engine
from app.core.exceptions import AppException
from app.presentation.api.v1.router import api_router

logging.basicConfig(level=logging.DEBUG if settings.APP_DEBUG else logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = _get_engine()
    async with engine.begin() as conn:
        pass
    yield
    await dispose_engine()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.APP_DEBUG else None,
    redoc_url="/redoc" if settings.APP_DEBUG else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "error_code": exc.error_code},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
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
    return {"status": "ok", "version": settings.APP_VERSION, "env": settings.APP_ENV}


app.include_router(api_router)

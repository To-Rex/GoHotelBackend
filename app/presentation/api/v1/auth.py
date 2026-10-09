from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.application.services.auth_service import AuthService
from app.application.services.webauthn_service import WebAuthnService
from app.application.dto.auth import (
    FaceSkipRequest,
    ContextSwitchRequest,
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    TokenResponse,
    UserProfileResponse,
)
from app.application.dto.common import MessageResponse
from app.application.dto.webauthn import (
    PasskeyResponse,
    WebAuthnLoginOptionsRequest,
    WebAuthnLoginVerifyRequest,
    WebAuthnOptionsResponse,
    WebAuthnRegisterVerifyRequest,
)
from app.presentation.middleware.auth import get_current_user

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
async def login(
    data: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Birinchi bosqich: login va parol.

    Xodim yuz biriktirgan bo'lsa tokenlar shu yerda BERILMAYDI — javobda
    `face_required` va qisqa muddatli `face_token` keladi, kirish esa
    `/face/verify-login` da yakunlanadi.
    """
    service = AuthService(session)
    return await service.login(
        username=data.username,
        password=data.password,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        fcm_token=data.fcm_token,
        # Qurilma identifikatori sarlavhada keladi — brauzer uni o'zi
        # yaratib saqlaydi. Tanadagi maydon emas: mobil klientlar ham
        # o'zgartirmasdan yubora oladi.
        device_id=request.headers.get("x-device-id"),
    )


@router.post("/login/no-camera", response_model=TokenResponse)
async def login_without_camera(
    data: FaceSkipRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Kamerasiz qurilmada ikkinchi bosqichni o'tkazib yuborish.

    Qoida: kamera BOR qurilmada yuz tasdiqlash shart (mijoz bu yo'lni
    taklif qilmaydi), kamerasiz kompyuterda esa parol yetarli. Kamera
    bor-yo'qligini faqat qurilmaning o'zi biladi, shuning uchun qaror
    mijozdan keladi. Lekin parolsiz ochilmaydi: `face_token` faqat login va
    parol to'g'ri kelganda beriladi va besh daqiqada kuchini yo'qotadi.
    Sabab sessiya yozuviga tushadi — kim qaysi yo'l bilan kirgani ko'rinadi.
    """
    service = AuthService(session)
    return await service.complete_login_without_face(
        data.face_token,
        reason=data.reason,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        device_id=request.headers.get("x-device-id"),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    data: RefreshRequest,
    session: AsyncSession = Depends(get_db),
):
    service = AuthService(session)
    result = await service.refresh_token(data.refresh_token)
    return result


@router.post("/logout", response_model=MessageResponse)
async def logout(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = AuthService(session)
    await service.logout(current_user["jti"])
    # Chiqqan foydalanuvchiga push bormasligi kerak — saqlangan FCM token
    # o'chadi; keyingi kirishda mobil ilova yangisini ro'yxatdan o'tkazadi
    from app.infrastructure.database.models.user import User

    user = await session.get(User, current_user["id"])
    if user is not None and user.fcm_token:
        user.fcm_token = None
        await session.flush()
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserProfileResponse)
async def get_me(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = AuthService(session)
    # Sozlovchi / tizim ma'muri uchun mehmonxona — tokendagi tanlov
    profile = await service.get_me(
        current_user["id"],
        context_hotel_id=current_user.get("hotel_id"),
        context_branch_id=current_user.get("branch_id"),
    )
    return profile


# --- Sozlovchi: mehmonxona va filialni tanlash --------------------------------


def _assert_context_user(current_user: dict) -> None:
    from app.application.services.configurator_access import can_switch_branch
    from app.core.exceptions import ForbiddenException

    if not can_switch_branch(current_user):
        raise ForbiddenException(
            "Mehmonxona yoki filialni faqat administrator va sozlovchi tanlay oladi",
            "CONTEXT_FORBIDDEN",
        )


@router.get("/context/options")
async def context_options(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Sozlovchi va tizim ma'muri uchun: barcha mehmonxonalar va filiallari.
    Administrator uchun — faqat o'z mehmonxonasi va uning filiallari."""
    _assert_context_user(current_user)
    return {"hotels": await AuthService(session).context_options(current_user)}


@router.post("/context", response_model=TokenResponse)
async def switch_context(
    data: ContextSwitchRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Mehmonxona va filialni tanlash — yangi token juftligi qaytadi.

    Tanlangan mehmonxonada sozlovchi administrator kabi ishlaydi va
    sozlamalarni o'zgartira oladi (configurator_access). Administrator
    faqat o'z mehmonxonasining filialini tanlaydi — keyingi barcha
    so'rovlar shu filial ichida ishlaydi.
    """
    _assert_context_user(current_user)
    return await AuthService(session).switch_context(
        current_user,
        data.hotel_id,
        data.branch_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


# --- WebAuthn (Face ID / Windows Hello / Touch ID) ---------------------------


@router.get("/webauthn/register/options", response_model=WebAuthnOptionsResponse)
async def webauthn_register_options(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = WebAuthnService(session)
    return await service.register_options(current_user["id"])


@router.post("/webauthn/register/verify", response_model=MessageResponse)
async def webauthn_register_verify(
    data: WebAuthnRegisterVerifyRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = WebAuthnService(session)
    await service.register_verify(
        current_user["id"],
        data.challenge_id,
        data.credential,
        user_agent=request.headers.get("user-agent"),
    )
    return {"message": "Passkey qo'shildi"}


@router.get("/webauthn/passkeys", response_model=list[PasskeyResponse])
async def webauthn_list_passkeys(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = WebAuthnService(session)
    return await service.list_passkeys(current_user["id"])


@router.delete("/webauthn/passkeys/{passkey_id}", response_model=MessageResponse)
async def webauthn_delete_passkey(
    passkey_id: UUID,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = WebAuthnService(session)
    await service.delete_passkey(current_user["id"], passkey_id)
    return {"message": "Passkey o'chirildi"}


@router.post("/webauthn/login/options", response_model=WebAuthnOptionsResponse)
async def webauthn_login_options(
    data: WebAuthnLoginOptionsRequest,
    session: AsyncSession = Depends(get_db),
):
    service = WebAuthnService(session)
    return await service.login_options(data.username)


@router.post("/webauthn/login/verify", response_model=TokenResponse)
async def webauthn_login_verify(
    data: WebAuthnLoginVerifyRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    service = WebAuthnService(session)
    return await service.login_verify(
        data.challenge_id,
        data.credential,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

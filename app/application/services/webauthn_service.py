"""
WebAuthn (Face ID/Windows Hello/Touch ID) passkey autentifikatsiyasi.

Oqim:
  - register_options/register_verify: tizimga kirgan foydalanuvchi o'z
    qurilmasini passkey sifatida bog'laydi (Sozlamalar sahifasidan).
  - login_options/login_verify: parolsiz kirish — discoverable credential
    (passkey) orqali qaysi foydalanuvchi ekani credential.id'dan aniqlanadi.

Har bir seremoniya bir martalik challenge'ga bog'lanadi (webauthn_challenges),
bu esa options so'ralgan payt bilan tasdiqlash o'rtasidagi holatni frontendga
ishonmasdan serverda saqlaydi.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

import webauthn
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json_dict
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.application.services.auth_service import AuthService
from app.core.config import settings
from app.core.exceptions import BadRequestException, NotFoundException, UnauthorizedException
from app.infrastructure.database.models.webauthn_credential import WebAuthnCredential
from app.infrastructure.database.repositories.user_repo import UserRepository
from app.infrastructure.database.repositories.webauthn_repo import (
    WebAuthnChallengeRepository,
    WebAuthnCredentialRepository,
)

CHALLENGE_TTL_MINUTES = 5


def _device_label_from_user_agent(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    ua = user_agent.lower()
    if "iphone" in ua or "ipad" in ua:
        return "iPhone/iPad (Face ID)"
    if "mac os" in ua:
        return "Mac (Touch ID)"
    if "windows" in ua:
        return "Windows (Windows Hello)"
    if "android" in ua:
        return "Android"
    return "Noma'lum qurilma"


class WebAuthnService:
    def __init__(self, session):
        self.session = session
        self.user_repo = UserRepository(session)
        self.cred_repo = WebAuthnCredentialRepository(session)
        self.challenge_repo = WebAuthnChallengeRepository(session)

    async def register_options(self, user_id: UUID) -> dict:
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise NotFoundException("Foydalanuvchi topilmadi")

        existing = await self.cred_repo.get_by_user(user_id)
        exclude_credentials = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
            for c in existing
        ]

        options = webauthn.generate_registration_options(
            rp_id=settings.WEBAUTHN_RP_ID,
            rp_name=settings.WEBAUTHN_RP_NAME,
            user_id=user.id.bytes,
            user_name=user.username,
            user_display_name=f"{user.first_name} {user.last_name}".strip() or user.username,
            exclude_credentials=exclude_credentials,
            authenticator_selection=AuthenticatorSelectionCriteria(
                authenticator_attachment=AuthenticatorAttachment.PLATFORM,
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )

        challenge_row = await self.challenge_repo.create(
            purpose="register",
            challenge=bytes_to_base64url(options.challenge),
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=CHALLENGE_TTL_MINUTES),
        )

        return {"challenge_id": challenge_row.id, "options": options_to_json_dict(options)}

    async def register_verify(
        self,
        user_id: UUID,
        challenge_id: UUID,
        credential: dict,
        user_agent: str | None,
    ) -> None:
        challenge_row = await self.challenge_repo.get_valid(challenge_id, "register")
        if not challenge_row or challenge_row.user_id != user_id:
            raise BadRequestException(
                "Ro'yxatdan o'tish muddati tugagan, qaytadan urinib ko'ring"
            )

        verification = webauthn.verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge_row.challenge),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGINS,
        )

        await self.challenge_repo.delete(challenge_row)

        cred = WebAuthnCredential(
            user_id=user_id,
            credential_id=bytes_to_base64url(verification.credential_id),
            public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            device_type=verification.credential_device_type.value,
            backed_up=verification.credential_backed_up,
            device_label=_device_label_from_user_agent(user_agent),
        )
        await self.cred_repo.create(cred)

    async def list_passkeys(self, user_id: UUID) -> list[dict]:
        creds = await self.cred_repo.get_by_user(user_id)
        return [
            {
                "id": c.id,
                "device_label": c.device_label,
                "created_at": c.created_at,
                "last_used_at": c.last_used_at,
            }
            for c in creds
        ]

    async def delete_passkey(self, user_id: UUID, passkey_id: UUID) -> None:
        cred = await self.cred_repo.get_by_id_and_user(passkey_id, user_id)
        if not cred:
            raise NotFoundException("Passkey topilmadi")
        await self.cred_repo.delete(cred)

    async def login_options(self, username: str | None) -> dict:
        allow_credentials: list[PublicKeyCredentialDescriptor] = []
        user_id: UUID | None = None
        if username:
            user = await self.user_repo.get_by_username(username)
            if user:
                user_id = user.id
                creds = await self.cred_repo.get_by_user(user.id)
                allow_credentials = [
                    PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
                    for c in creds
                ]

        options = webauthn.generate_authentication_options(
            rp_id=settings.WEBAUTHN_RP_ID,
            allow_credentials=allow_credentials,
            user_verification=UserVerificationRequirement.REQUIRED,
        )

        challenge_row = await self.challenge_repo.create(
            purpose="login",
            challenge=bytes_to_base64url(options.challenge),
            user_id=user_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=CHALLENGE_TTL_MINUTES),
        )

        return {"challenge_id": challenge_row.id, "options": options_to_json_dict(options)}

    async def login_verify(
        self,
        challenge_id: UUID,
        credential: dict,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        challenge_row = await self.challenge_repo.get_valid(challenge_id, "login")
        if not challenge_row:
            raise UnauthorizedException("Kirish muddati tugagan, qaytadan urinib ko'ring")

        credential_id = credential.get("id")
        if not credential_id:
            raise BadRequestException("Noto'g'ri so'rov")

        cred = await self.cred_repo.get_by_credential_id(credential_id)
        if not cred:
            raise UnauthorizedException("Bu qurilma ro'yxatdan o'tmagan", "CREDENTIAL_NOT_FOUND")

        verification = webauthn.verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge_row.challenge),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGINS,
            credential_public_key=cred.public_key,
            credential_current_sign_count=cred.sign_count,
        )

        await self.challenge_repo.delete(challenge_row)

        cred.sign_count = verification.new_sign_count
        cred.last_used_at = datetime.now(timezone.utc)

        user = await self.user_repo.get_by_id(cred.user_id)
        if not user or user.status != "ACTIVE" or user.is_deleted:
            raise UnauthorizedException("Hisob faol emas", "ACCOUNT_INACTIVE")

        auth_service = AuthService(self.session)
        return await auth_service.issue_tokens(user, ip_address=ip_address, user_agent=user_agent)

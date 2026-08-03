from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.webauthn_challenge import WebAuthnChallenge
from app.infrastructure.database.models.webauthn_credential import WebAuthnCredential
from app.infrastructure.database.repositories.base import BaseRepository


class WebAuthnCredentialRepository(BaseRepository[WebAuthnCredential]):
    model = WebAuthnCredential

    async def get_by_user(self, user_id: UUID) -> list[WebAuthnCredential]:
        stmt = select(WebAuthnCredential).where(WebAuthnCredential.user_id == user_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_credential_id(self, credential_id: str) -> WebAuthnCredential | None:
        stmt = select(WebAuthnCredential).where(
            WebAuthnCredential.credential_id == credential_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_and_user(
        self, credential_pk: UUID, user_id: UUID
    ) -> WebAuthnCredential | None:
        stmt = select(WebAuthnCredential).where(
            WebAuthnCredential.id == credential_pk,
            WebAuthnCredential.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class WebAuthnChallengeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> WebAuthnChallenge:
        challenge = WebAuthnChallenge(**kwargs)
        self.session.add(challenge)
        await self.session.flush()
        return challenge

    async def get_valid(self, challenge_id: UUID, purpose: str) -> WebAuthnChallenge | None:
        stmt = select(WebAuthnChallenge).where(
            WebAuthnChallenge.id == challenge_id,
            WebAuthnChallenge.purpose == purpose,
            WebAuthnChallenge.expires_at > datetime.now(timezone.utc),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete(self, challenge: WebAuthnChallenge) -> None:
        await self.session.delete(challenge)
        await self.session.flush()

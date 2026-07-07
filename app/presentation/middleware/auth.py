from uuid import UUID
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.database import get_db
from app.infrastructure.auth.jwt import decode_token
from app.infrastructure.database.repositories.user_repo import UserRepository, SessionRepository
from sqlalchemy.ext.asyncio import AsyncSession

security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = credentials.credentials
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    def _safe_uuid(value):
        try:
            return UUID(value) if value else None
        except (ValueError, TypeError, AttributeError):
            return None

    return {
        "id": _safe_uuid(user_id),
        "user_type": payload.get("user_type", ""),
        "hotel_id": _safe_uuid(payload.get("hotel_id")),
        "branch_id": _safe_uuid(payload.get("branch_id")),
        "permissions": payload.get("permissions", []),
        "jti": payload.get("jti", ""),
    }


def require_permission(permission_code: str):
    async def permission_checker(
        current_user: dict = Depends(get_current_user),
    ) -> dict:
        user_type = current_user.get("user_type", "")
        if user_type == "SUPER_ADMIN":
            return current_user
        if user_type == "ADMIN":
            return current_user
        permissions = current_user.get("permissions", [])
        if permission_code not in permissions:
            raise HTTPException(
                status_code=403,
                detail=f"Missing required permission: {permission_code}",
            )
        return current_user

    return permission_checker

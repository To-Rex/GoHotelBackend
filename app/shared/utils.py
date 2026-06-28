import secrets
import string
from datetime import date, datetime, timezone


def generate_code(prefix: str, hotel_code: str) -> str:
    today = date.today().strftime("%Y%m%d")
    suffix = "".join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4)
    )
    return f"{prefix}-{hotel_code}-{today}-{suffix}"


def generate_jti() -> str:
    return secrets.token_hex(32)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

import re

from pydantic import field_validator

PHONE_REGEX = re.compile(r"^\+?[\d\s\-\(\)]{7,20}$")


def validate_phone(value: str | None) -> str | None:
    if not value:
        return None
    if not PHONE_REGEX.match(value):
        raise ValueError("Invalid phone number format")
    return value


def validate_email(value: str | None) -> str | None:
    if not value:
        return None
    if "@" not in value or "." not in value.split("@")[-1]:
        raise ValueError("Invalid email format")
    return value.lower().strip()
